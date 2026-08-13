"""Tests for sdk.context — the door (plan Task 5, OV-14).

The ctx is the ONLY way modules touch the world: writes are hard-validated
against the manifest's produces (raise, never warn), scope checks are
log-only stubs until Phase 3, and the ctx emits the bus events that wire
module zero together.
"""

import logging
from pathlib import Path

import pytest

from core.bus import Bus
from core.ontology.store import OntologyStore
from sdk.context import Ctx, ScopeViolation, build_ctx

NOW = "2026-08-13T00:00:00+00:00"


def connector_manifest() -> dict:
    """Manifest shaped like the celestrak-connector worked example."""
    return {
        "name": "celestrak-connector",
        "kind": "connector",
        "layer": 1,
        "produces": ["RawRecord:celestrak"],
        "consumes": [],
        "scopes": ["raw:write:celestrak"],
        "runtime": {"entrypoint": "module.py:run", "agent": False},
    }


def classifier_manifest(agent: bool = False) -> dict:
    """Manifest shaped like the satellite-classifier worked example."""
    return {
        "name": "satellite-classifier",
        "kind": "transform",
        "layer": 2,
        "produces": ["Satellite"],
        "consumes": ["RawRecord:celestrak"],
        "scopes": ["raw:read:celestrak", "ontology:propose:Satellite"],
        "runtime": {"entrypoint": "module.py:run", "agent": agent},
    }


@pytest.fixture()
def store(tmp_path: Path) -> OntologyStore:
    """A fresh store."""
    return OntologyStore(str(tmp_path / "test.db"))


@pytest.fixture()
def bus() -> Bus:
    """A fresh bus."""
    return Bus()


def make_ctx(manifest: dict, store: OntologyStore, bus: Bus) -> Ctx:
    """Build a ctx for the given manifest."""
    return build_ctx(manifest=manifest, store=store, bus=bus)


def propose_via(ctx: Ctx, **overrides: object) -> str:
    """Propose a simple Satellite name observation through the ctx."""
    kwargs: dict = dict(
        entity_id="sat:norad:25544",
        entity_type="Satellite",
        property="name",
        value="ISS (ZARYA)",
        valid_time=NOW,
        method="parsed",
    )
    kwargs.update(overrides)
    return ctx.ontology.propose(**kwargs)


class TestScopeViolations:
    """Writes outside declared produces raise, never warn."""

    def test_undeclared_raw_land_raises(self, store: OntologyStore, bus: Bus) -> None:
        ctx = make_ctx(classifier_manifest(), store, bus)  # produces [Satellite] only
        with pytest.raises(ScopeViolation) as excinfo:
            ctx.raw.land("celestrak", b"data", "celestrak-gp-json")
        message = str(excinfo.value)
        assert "satellite-classifier" in message
        assert "RawRecord:celestrak" in message
        assert "Satellite" in message  # declared produces named

    def test_undeclared_ontology_type_raises(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        ctx = make_ctx(connector_manifest(), store, bus)  # produces RawRecord only
        with pytest.raises(ScopeViolation) as excinfo:
            propose_via(ctx)
        assert "celestrak-connector" in str(excinfo.value)
        assert "Satellite" in str(excinfo.value)

    def test_batch_validates_every_row(self, store: OntologyStore, bus: Bus) -> None:
        ctx = make_ctx(classifier_manifest(), store, bus)
        proposals = [
            dict(
                entity_id="sat:norad:25544",
                entity_type="Satellite",
                property="name",
                value="ISS",
                valid_time=NOW,
                method="parsed",
            ),
            dict(
                entity_id="alert:01ARZ3NDEKTSV4RRFFQ69G5FAV",
                entity_type="Alert",  # not declared by this manifest
                property="severity",
                value="info",
                valid_time=NOW,
                method="parsed",
            ),
        ]
        with pytest.raises(ScopeViolation):
            ctx.ontology.propose_batch(proposals)
        assert store.count_rows("observations") == 0  # all-or-nothing held


class TestBusEmission:
    """The ctx emits the events that stitch module zero together."""

    def test_new_landing_emits_raw_landed(self, store: OntologyStore, bus: Bus) -> None:
        events: list[tuple[str, dict]] = []
        bus.subscribe("raw.landed:*", lambda t, p: events.append((t, p)))
        ctx = make_ctx(connector_manifest(), store, bus)
        raw_id, is_new = ctx.raw.land("celestrak", b"gp-json", "celestrak-gp-json")
        assert is_new is True
        assert events == [
            ("raw.landed:celestrak", {"raw_id": raw_id, "source": "celestrak"})
        ]

    def test_duplicate_landing_emits_nothing(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        events: list[str] = []
        ctx = make_ctx(connector_manifest(), store, bus)
        ctx.raw.land("celestrak", b"gp-json", "celestrak-gp-json")
        bus.subscribe("raw.landed:*", lambda t, p: events.append(t))
        _, is_new = ctx.raw.land("celestrak", b"gp-json", "celestrak-gp-json")
        assert is_new is False
        assert events == []

    def test_accepted_propose_emits_observation_appended(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        events: list[dict] = []
        bus.subscribe("observation.appended", lambda t, p: events.append(p))
        ctx = make_ctx(classifier_manifest(), store, bus)
        obs_id = propose_via(ctx)
        assert events == [
            {
                "entity_id": "sat:norad:25544",
                "entity_type": "Satellite",
                "observation_id": obs_id,
            }
        ]

    def test_batch_emits_per_accepted_observation(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        events: list[dict] = []
        bus.subscribe("observation.appended", lambda t, p: events.append(p))
        ctx = make_ctx(classifier_manifest(), store, bus)
        proposals = [
            dict(
                entity_id=f"sat:norad:{25544 + i}",
                entity_type="Satellite",
                property="name",
                value=f"SAT-{i}",
                valid_time=NOW,
                method="parsed",
            )
            for i in range(3)
        ]
        ids = ctx.ontology.propose_batch(proposals)
        assert [e["observation_id"] for e in events] == ids


class TestAgentStaging:
    """Agent-flagged manifests always stage; staged writes emit nothing."""

    def test_agent_proposals_land_proposed(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        events: list[dict] = []
        bus.subscribe("observation.appended", lambda t, p: events.append(p))
        ctx = make_ctx(classifier_manifest(agent=True), store, bus)
        obs_id = propose_via(ctx)
        assert store.current_status(obs_id) == "proposed"
        assert events == []  # only accepted observations announce themselves


class TestCtxSurface:
    """The ctx exposes exactly the contracted surface."""

    def test_log_is_module_named_logger(self, store: OntologyStore, bus: Bus) -> None:
        ctx = make_ctx(classifier_manifest(), store, bus)
        assert ctx.log.name == "omniview.satellite-classifier"

    def test_source_module_is_injected(self, store: OntologyStore, bus: Bus) -> None:
        ctx = make_ctx(classifier_manifest(), store, bus)
        propose_via(ctx)
        rows = store.history("sat:norad:25544", "name")
        assert rows[0]["source_module"] == "satellite-classifier"

    def test_raw_reads_roundtrip(self, store: OntologyStore, bus: Bus) -> None:
        writer = make_ctx(connector_manifest(), store, bus)
        raw_id, _ = writer.raw.land("celestrak", b"payload", "celestrak-gp-json")
        reader = make_ctx(classifier_manifest(), store, bus)
        assert reader.raw.get_payload(raw_id) == b"payload"
        latest = reader.raw.latest("celestrak")
        assert latest is not None and latest["id"] == raw_id
        assert reader.raw.read("celestrak") == b"payload"

    def test_ontology_reads(self, store: OntologyStore, bus: Bus) -> None:
        ctx = make_ctx(classifier_manifest(), store, bus)
        propose_via(ctx)
        assert ctx.ontology.get("sat:norad:25544") is not None
        assert len(ctx.ontology.search(entity_type="Satellite")) == 1
        assert len(ctx.ontology.history("sat:norad:25544", "name")) == 1

    def test_bus_passthrough(self, store: OntologyStore, bus: Bus) -> None:
        ctx = make_ctx(classifier_manifest(), store, bus)
        received: list[str] = []
        ctx.bus.subscribe("custom.*", lambda t, p: received.append(t))
        ctx.bus.publish("custom.topic", {})
        assert received == ["custom.topic"]


class TestScopeStubs:
    """Scope checks are log-only stubs until Phase 3 — but they must log."""

    def test_scope_stub_lines_logged(
        self, store: OntologyStore, bus: Bus, caplog: pytest.LogCaptureFixture
    ) -> None:
        ctx = make_ctx(connector_manifest(), store, bus)
        with caplog.at_level(logging.DEBUG, logger="omniview.celestrak-connector"):
            ctx.raw.land("celestrak", b"data", "celestrak-gp-json")
        stub_lines = [r.message for r in caplog.records if "scope-check" in r.message]
        assert stub_lines, "expected scope-check stub log lines"
        assert any(
            "celestrak-connector" in line and "stub-pass" in line for line in stub_lines
        )
