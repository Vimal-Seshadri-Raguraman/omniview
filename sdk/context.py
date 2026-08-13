"""SDK context — the door (plan Task 5; constitution: modules touch the world only here).

build_ctx() wires a module's manifest to the core and returns the Ctx the
registry passes into the module's entrypoint. Enforcement:

- Writes are validated hard against the manifest's `produces` (registry
  rule 5 — raise ScopeViolation, never warn).
- Scope checks are log-only stubs until Phase 3: every ctx call logs
  "scope-check <module> <resource>:<action>:<qualifier> -> stub-pass".
- The ctx emits the bus events that wire modules together:
  raw.landed:<source> (only when a landing is new) and
  observation.appended (per accepted observation).
"""

import logging
from typing import Any

from core.bus import Bus
from core.ontology.store import OntologyStore
from core.schema.registry import RAW_RECORD_PREFIX

LOGGER_PREFIX = "omniview."
TOPIC_RAW_LANDED = "raw.landed:"
TOPIC_OBSERVATION_APPENDED = "observation.appended"


class ScopeViolation(Exception):
    """A module attempted a write its manifest does not declare."""


class _Facade:
    """Shared plumbing for ctx facades: scope stubs + produces checks."""

    def __init__(
        self,
        module_name: str,
        produces: list[str],
        store: OntologyStore,
        bus: Bus,
        log: logging.Logger,
    ) -> None:
        self._module = module_name
        self._produces = produces
        self._store = store
        self._bus = bus
        self._log = log

    def _scope_check(self, scope: str) -> None:
        """Phase 1 scope stub: log the check, always pass (Phase 3 makes it real)."""
        self._log.debug("scope-check %s %s -> stub-pass", self._module, scope)

    def _require_produces(self, declared_as: str) -> None:
        """Raise ScopeViolation unless the manifest declares this write."""
        if declared_as not in self._produces:
            raise ScopeViolation(
                f"Module '{self._module}' attempted to write '{declared_as}' "
                f"but its manifest declares produces={self._produces}"
            )


class RawFacade(_Facade):
    """ctx.raw — the raw landing zone, per scopes."""

    def land(self, source: str, payload: bytes, parse_hint: str) -> tuple[str, bool]:
        """Land raw bytes; emits raw.landed:<source> only when new.

        Raises:
            ScopeViolation: If RawRecord:<source> is not in produces.
        """
        self._scope_check(f"raw:write:{source}")
        self._require_produces(f"{RAW_RECORD_PREFIX}{source}")
        raw_id, is_new = self._store.land_raw(source, payload, parse_hint)
        if is_new:
            self._bus.publish(
                f"{TOPIC_RAW_LANDED}{source}", {"raw_id": raw_id, "source": source}
            )
        return raw_id, is_new

    def latest(self, source: str) -> dict[str, Any] | None:
        """Feed-health metadata for a source (A-003)."""
        self._scope_check(f"raw:read:{source}")
        return self._store.latest_raw(source)

    def get_payload(self, raw_id: str) -> bytes:
        """Exact bytes of a landed raw record."""
        self._scope_check(f"raw:read:{raw_id}")
        return self._store.get_raw_payload(raw_id)

    def read(self, source: str) -> bytes | None:
        """The newest raw payload for a source, or None if none landed."""
        self._scope_check(f"raw:read:{source}")
        latest = self._store.latest_raw(source)
        if latest is None:
            return None
        return self._store.get_raw_payload(str(latest["id"]))


class OntologyFacade(_Facade):
    """ctx.ontology — propose (validated) and read the shared memory."""

    def __init__(
        self,
        module_name: str,
        produces: list[str],
        store: OntologyStore,
        bus: Bus,
        log: logging.Logger,
        agent: bool,
    ) -> None:
        super().__init__(module_name, produces, store, bus, log)
        self._agent = agent

    def propose(
        self,
        *,
        entity_id: str,
        entity_type: str,
        property: str,
        value: object,
        valid_time: str,
        method: str,
        confidence: float = 1.0,
        evidence: list[str] | None = None,
    ) -> str:
        """Propose one observation; source_module and agent flag are injected.

        Raises:
            ScopeViolation: If entity_type is not in the manifest's produces.
        """
        ids = self.propose_batch(
            [
                dict(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    property=property,
                    value=value,
                    valid_time=valid_time,
                    method=method,
                    confidence=confidence,
                    evidence=evidence,
                )
            ]
        )
        return ids[0]

    def propose_batch(self, proposals: list[dict[str, Any]]) -> list[str]:
        """Propose many observations in one transaction (A-005).

        Every row is produces-validated BEFORE any write; the store then
        applies all-or-nothing. Emits observation.appended per accepted
        observation.

        Raises:
            ScopeViolation: If any row's entity_type is undeclared.
        """
        stamped: list[dict[str, Any]] = []
        for proposal in proposals:
            entity_type = str(proposal["entity_type"])
            self._scope_check(f"ontology:propose:{entity_type}")
            self._require_produces(entity_type)
            row = dict(proposal)
            row["source_module"] = self._module
            row["agent"] = self._agent
            stamped.append(row)
        observation_ids = self._store.propose_batch(stamped)
        for row, obs_id in zip(stamped, observation_ids):
            if self._store.current_status(obs_id) == "accepted":
                self._bus.publish(
                    TOPIC_OBSERVATION_APPENDED,
                    {
                        "entity_id": str(row["entity_id"]),
                        "entity_type": str(row["entity_type"]),
                        "observation_id": obs_id,
                    },
                )
        return observation_ids

    def get(self, entity_id: str) -> dict[str, Any] | None:
        """Current projection for an entity."""
        self._scope_check(f"ontology:read:{entity_id}")
        return self._store.get(entity_id)

    def history(
        self,
        entity_id: str,
        property: str,
        t_from: str | None = None,
        t_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ledger rows for one entity property, valid_time-ordered."""
        self._scope_check(f"ontology:read:{entity_id}")
        return self._store.history(entity_id, property, t_from, t_to)

    def search(
        self,
        entity_type: str | None = None,
        facet: str | None = None,
        t: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search projections (or time travel with t set)."""
        self._scope_check(f"ontology:search:{entity_type or '*'}")
        return self._store.search(entity_type=entity_type, facet=facet, t=t)


class BusFacade(_Facade):
    """ctx.bus — publish/subscribe with scope stubs."""

    def publish(self, topic: str, payload: dict) -> None:
        """Publish a payload to a topic."""
        self._scope_check(f"bus:publish:{topic}")
        self._bus.publish(topic, payload)

    def subscribe(self, pattern: str, handler: Any) -> None:
        """Subscribe a handler to a topic pattern."""
        self._scope_check(f"bus:subscribe:{pattern}")
        self._bus.subscribe(pattern, handler)


class Ctx:
    """The module-facing surface: exactly raw, ontology, bus, log."""

    def __init__(
        self,
        raw: RawFacade,
        ontology: OntologyFacade,
        bus: BusFacade,
        log: logging.Logger,
    ) -> None:
        self.raw = raw
        self.ontology = ontology
        self.bus = bus
        self.log = log


def build_ctx(*, manifest: dict, store: OntologyStore, bus: Bus) -> Ctx:
    """Build the ctx for one module from its (validated) manifest.

    Args:
        manifest: The module's manifest dict; must carry name, produces,
            and runtime (runtime.agent defaults False when absent).
        store: The one ontology store.
        bus: The one event bus.

    Returns:
        A Ctx whose writes are validated against manifest produces and
        whose log is the stdlib logger "omniview.<module-name>".
    """
    module_name = str(manifest["name"])
    produces = [str(p) for p in manifest.get("produces", [])]
    agent = bool(manifest.get("runtime", {}).get("agent", False))
    log = logging.getLogger(f"{LOGGER_PREFIX}{module_name}")
    return Ctx(
        raw=RawFacade(module_name, produces, store, bus, log),
        ontology=OntologyFacade(module_name, produces, store, bus, log, agent),
        bus=BusFacade(module_name, produces, store, bus, log),
        log=log,
    )
