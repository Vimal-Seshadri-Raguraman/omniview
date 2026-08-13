"""Ontology store — the one memory (ontology-v0.md; A-002/A-003/A-005/A-007).

One SQLite database holds four tables: raw_records (the landing zone),
observations (the append-only ledger), status_events (the staging ledger,
A-002), and projections_current (the derived whiteboard). Nothing is ever
UPDATEd or DELETEd in the ledgers; corrections and status changes are new
rows. Projections are recomputed per entity from the ledger on every
accepted change, so the incremental table is definitionally identical to
a full rebuild.
"""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from core.schema.registry import REGISTERED_TYPES, validate_entity_id
from lib.ulid import new_ulid

# Schemas verbatim from docs/ontology-v0.md (observations carries NO status
# column — status lives in the status_events ledger, per A-002).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_records (
  id            TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  payload       BLOB NOT NULL,
  parse_hint    TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_source_time ON raw_records(source, fetched_at);

CREATE TABLE IF NOT EXISTS observations (
  id            TEXT PRIMARY KEY,
  entity_id     TEXT NOT NULL,
  entity_type   TEXT NOT NULL,
  property      TEXT NOT NULL,
  value         TEXT NOT NULL,
  valid_time    TEXT NOT NULL,
  recorded_time TEXT NOT NULL,
  source_module TEXT NOT NULL,
  method        TEXT NOT NULL,
  confidence    REAL NOT NULL DEFAULT 1.0,
  evidence      TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id, property, valid_time);
CREATE INDEX IF NOT EXISTS idx_obs_type   ON observations(entity_type, recorded_time);

CREATE TABLE IF NOT EXISTS status_events (
  id             TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL,
  status         TEXT NOT NULL,
  at             TEXT NOT NULL,
  actor          TEXT NOT NULL,
  reason         TEXT
);
CREATE INDEX IF NOT EXISTS idx_status_obs ON status_events(observation_id, at);

CREATE TABLE IF NOT EXISTS projections_current (
  entity_id    TEXT PRIMARY KEY,
  entity_type  TEXT NOT NULL,
  properties   TEXT NOT NULL,
  facets       TEXT NOT NULL DEFAULT '[]',
  as_of        TEXT NOT NULL
);
"""

VALID_STATUSES = frozenset({"proposed", "accepted", "rejected"})
AUTO_ACCEPT_CONFIDENCE = 0.9
PROMOTION_ACTOR = "promotion-rules"
FACETS_PROPERTY = "facets"

_METHOD_EXACT = frozenset({"parsed", "asserted:human"})
_METHOD_PREFIXES = ("computed:", "inferred:")
_TABLES = frozenset(
    {"raw_records", "observations", "status_events", "projections_current"}
)

# Latest status event per observation (ULID ids break same-instant ties).
_CURRENT_STATUS_SQL = (
    "SELECT status FROM status_events WHERE observation_id = ? "
    "ORDER BY at DESC, id DESC LIMIT 1"
)


def _validate_method(method: str) -> None:
    """Enforce the controlled method vocabulary (ontology-v0 design notes).

    Args:
        method: One of "parsed", "asserted:human", "computed:<algo>",
            or "inferred:<how>".

    Raises:
        ValueError: If the method is outside the vocabulary.
    """
    if method in _METHOD_EXACT:
        return
    if any(method.startswith(p) and len(method) > len(p) for p in _METHOD_PREFIXES):
        return
    raise ValueError(
        f"Method '{method}' is not in the controlled vocabulary: "
        "'parsed', 'asserted:human', 'computed:<algo>', 'inferred:<how>'"
    )


class OntologyStore:
    """The single stateful core: raw landing zone + ledger + projections."""

    def __init__(self, db_path: str) -> None:
        """Open (creating tables if absent) the store at db_path.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Explicit BEGIN/COMMIT management: autocommit off the transaction
        # path, so propose_batch is provably a single transaction (A-005).
        self._conn.isolation_level = None
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ util

    @staticmethod
    def now() -> str:
        """Current UTC time as an ISO8601 string."""
        return datetime.now(timezone.utc).isoformat()

    def set_trace_callback(self, callback: Callable[[str], Any] | None) -> None:
        """Install (or clear) a SQL trace callback — used by tests."""
        self._conn.set_trace_callback(callback)

    def count_rows(self, table: str) -> int:
        """Count rows in one of the store's tables.

        Args:
            table: One of raw_records, observations, status_events,
                projections_current.

        Raises:
            ValueError: If the table name is not one of the store's tables.
        """
        if table not in _TABLES:
            raise ValueError(
                f"Unknown table '{table}'; expected one of {sorted(_TABLES)}"
            )
        row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    def snapshot_projections(self) -> list[dict[str, Any]]:
        """Deterministic snapshot of projections_current for equality checks."""
        rows = self._conn.execute(
            "SELECT * FROM projections_current ORDER BY entity_id"
        ).fetchall()
        return [dict(r) for r in rows]

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        """One locked BEGIN..COMMIT scope; rolls back on any exception."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN")
            try:
                yield cursor
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    # ------------------------------------------------- raw landing zone (L1)

    def land_raw(
        self, source: str, payload: bytes, parse_hint: str
    ) -> tuple[str, bool]:
        """Land raw bytes, deduplicating per (source, content sha256).

        Returns:
            (existing_id, False) when identical bytes from this source were
            already landed; (new_id, True) otherwise.
        """
        content_hash = hashlib.sha256(payload).hexdigest()
        with self._transaction() as cur:
            row = cur.execute(
                "SELECT id FROM raw_records WHERE source = ? AND content_hash = ?",
                (source, content_hash),
            ).fetchone()
            if row is not None:
                return str(row["id"]), False
            raw_id = new_ulid()
            cur.execute(
                "INSERT INTO raw_records (id, source, fetched_at, content_hash, payload, "
                "parse_hint) VALUES (?, ?, ?, ?, ?, ?)",
                (raw_id, source, self.now(), content_hash, payload, parse_hint),
            )
            return raw_id, True

    def latest_raw(self, source: str) -> dict[str, Any] | None:
        """Feed-health metadata for a source's newest raw record (A-003)."""
        row = self._conn.execute(
            "SELECT id, fetched_at, content_hash FROM raw_records "
            "WHERE source = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
            (source,),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_raw_payload(self, raw_id: str) -> bytes:
        """Return the exact bytes of a landed raw record.

        Raises:
            ValueError: If no raw record has this id.
        """
        row = self._conn.execute(
            "SELECT payload FROM raw_records WHERE id = ?", (raw_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Raw record '{raw_id}' not found")
        return bytes(row["payload"])

    # ------------------------------------------------------------ the ledger

    def propose(
        self,
        *,
        entity_id: str,
        entity_type: str,
        property: str,
        value: object,
        valid_time: str,
        method: str,
        source_module: str,
        confidence: float = 1.0,
        evidence: list[str] | None = None,
        agent: bool = False,
    ) -> str:
        """Append one observation (plus its initial status event).

        Status is "accepted" when a non-agent writer proposes with
        confidence >= 0.9; otherwise "proposed" (v0 promotion rule, A-002).
        Accepted observations are applied to the projection immediately.

        Returns:
            The new observation's id.
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
                    source_module=source_module,
                    confidence=confidence,
                    evidence=evidence,
                    agent=agent,
                )
            ]
        )
        return ids[0]

    def propose_batch(self, proposals: list[dict[str, Any]]) -> list[str]:
        """Append many observations in ONE transaction (A-005), all-or-nothing.

        Args:
            proposals: Dicts with the same keys as propose() keyword args.

        Returns:
            New observation ids, in input order.

        Raises:
            ValueError: On any invalid proposal — nothing is written.
        """
        observation_ids: list[str] = []
        with self._transaction() as cur:
            touched_accepted: set[str] = set()
            for proposal in proposals:
                obs_id, entity_id, status = self._insert_proposal(cur, proposal)
                observation_ids.append(obs_id)
                if status == "accepted":
                    touched_accepted.add(entity_id)
            for entity_id in sorted(touched_accepted):
                self._recompute_entity(cur, entity_id)
        return observation_ids

    def _insert_proposal(
        self, cur: sqlite3.Cursor, proposal: dict[str, Any]
    ) -> tuple[str, str, str]:
        """Validate and insert one observation + initial status event.

        Returns:
            (observation_id, entity_id, initial_status).
        """
        entity_id = str(proposal["entity_id"])
        entity_type = str(proposal["entity_type"])
        validate_entity_id(entity_id)
        if entity_type not in REGISTERED_TYPES:
            raise ValueError(
                f"Entity type '{entity_type}' is not registered "
                f"(registered: {sorted(REGISTERED_TYPES)})"
            )
        method = str(proposal["method"])
        _validate_method(method)
        confidence = float(proposal.get("confidence", 1.0))
        agent = bool(proposal.get("agent", False))
        evidence = proposal.get("evidence")
        obs_id = new_ulid()
        recorded_time = self.now()
        cur.execute(
            "INSERT INTO observations (id, entity_id, entity_type, property, value, "
            "valid_time, recorded_time, source_module, method, confidence, evidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obs_id,
                entity_id,
                entity_type,
                str(proposal["property"]),
                json.dumps(proposal["value"], sort_keys=True),
                str(proposal["valid_time"]),
                recorded_time,
                str(proposal["source_module"]),
                method,
                confidence,
                json.dumps(evidence) if evidence is not None else None,
            ),
        )
        status = (
            "accepted"
            if (not agent and confidence >= AUTO_ACCEPT_CONFIDENCE)
            else "proposed"
        )
        cur.execute(
            "INSERT INTO status_events (id, observation_id, status, at, actor, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_ulid(), obs_id, status, recorded_time, PROMOTION_ACTOR, None),
        )
        return obs_id, entity_id, status

    def set_status(
        self, observation_id: str, status: str, actor: str, reason: str | None
    ) -> str:
        """Append a status event and re-apply the entity's projection.

        Raises:
            ValueError: On an unknown status or observation id.

        Returns:
            The new status event's id.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Status '{status}' is not valid (expected one of {sorted(VALID_STATUSES)})"
            )
        with self._transaction() as cur:
            row = cur.execute(
                "SELECT entity_id FROM observations WHERE id = ?", (observation_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Observation '{observation_id}' not found")
            event_id = new_ulid()
            cur.execute(
                "INSERT INTO status_events (id, observation_id, status, at, actor, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, observation_id, status, self.now(), actor, reason),
            )
            self._recompute_entity(cur, str(row["entity_id"]))
        return event_id

    def current_status(self, observation_id: str) -> str:
        """The observation's current status (latest event wins).

        Raises:
            ValueError: If the observation has no status events.
        """
        row = self._conn.execute(_CURRENT_STATUS_SQL, (observation_id,)).fetchone()
        if row is None:
            raise ValueError(f"Observation '{observation_id}' not found")
        return str(row["status"])

    # ----------------------------------------------------------------- reads

    def get(self, entity_id: str) -> dict[str, Any] | None:
        """The entity's current projection, or None if it has none."""
        row = self._conn.execute(
            "SELECT * FROM projections_current WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return self._projection_row_to_dict(row) if row is not None else None

    def history(
        self,
        entity_id: str,
        property: str,
        t_from: str | None = None,
        t_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ledger rows for one entity property, valid_time-ordered.

        Args:
            entity_id: The entity to read.
            property: The property name (e.g. "name", "position").
            t_from: Inclusive lower bound on valid_time (ISO8601), if any.
            t_to: Inclusive upper bound on valid_time (ISO8601), if any.
        """
        sql = "SELECT * FROM observations WHERE entity_id = ? AND property = ?"
        params: list[Any] = [entity_id, property]
        if t_from is not None:
            sql += " AND valid_time >= ?"
            params.append(t_from)
        if t_to is not None:
            sql += " AND valid_time <= ?"
            params.append(t_to)
        sql += " ORDER BY valid_time, recorded_time, id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._observation_row_to_dict(r) for r in rows]

    def search(
        self,
        entity_type: str | None = None,
        facet: str | None = None,
        t: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find entities by type and/or facet — now, or as believed at t.

        With t=None this reads projections_current (fast). With t set it is
        time travel: answered from the ledger using only observations
        recorded at or before t whose status at t was accepted.
        """
        if t is not None:
            return self._search_at(t, entity_type, facet)
        sql = "SELECT * FROM projections_current"
        params: list[Any] = []
        if entity_type is not None:
            sql += " WHERE entity_type = ?"
            params.append(entity_type)
        sql += " ORDER BY entity_id"
        results = [
            self._projection_row_to_dict(r)
            for r in self._conn.execute(sql, params).fetchall()
        ]
        if facet is not None:
            results = [p for p in results if facet in p["facets"]]
        return results

    def rebuild_projections(self) -> None:
        """Drop and recompute the whiteboard from the ledger (A-007a)."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM projections_current")
            entity_rows = cur.execute(
                "SELECT DISTINCT entity_id FROM observations ORDER BY entity_id"
            ).fetchall()
            for row in entity_rows:
                self._recompute_entity(cur, str(row["entity_id"]))

    # ------------------------------------------------- projection internals

    def _accepted_observations(
        self, cur: sqlite3.Cursor, entity_id: str
    ) -> list[sqlite3.Row]:
        """All observations of an entity whose CURRENT status is accepted."""
        return cur.execute(
            "SELECT o.* FROM observations o WHERE o.entity_id = ? AND "
            f"(({_CURRENT_STATUS_SQL.replace('?', 'o.id')})) = 'accepted' "
            "ORDER BY o.valid_time, o.recorded_time, o.id",
            (entity_id,),
        ).fetchall()

    def _recompute_entity(self, cur: sqlite3.Cursor, entity_id: str) -> None:
        """Recompute one entity's projection row from the ledger.

        Shared by incremental maintenance and rebuild_projections, which is
        what makes their outputs identical by construction.
        """
        rows = self._accepted_observations(cur, entity_id)
        cur.execute("DELETE FROM projections_current WHERE entity_id = ?", (entity_id,))
        if not rows:
            return
        properties: dict[str, Any] = {}
        facets: Any = []
        for row in rows:  # already ordered oldest→newest: later rows win
            value = json.loads(row["value"])
            if row["property"] == FACETS_PROPERTY:
                facets = value
            else:
                properties[row["property"]] = value
        as_of = max(str(r["recorded_time"]) for r in rows)
        entity_type = str(rows[-1]["entity_type"])
        cur.execute(
            "INSERT INTO projections_current (entity_id, entity_type, properties, "
            "facets, as_of) VALUES (?, ?, ?, ?, ?)",
            (
                entity_id,
                entity_type,
                json.dumps(properties, sort_keys=True),
                json.dumps(facets, sort_keys=True),
                as_of,
            ),
        )

    def _search_at(
        self, t: str, entity_type: str | None, facet: str | None
    ) -> list[dict[str, Any]]:
        """Time travel: projections as believed at time t (A-007 read path)."""
        type_filter = "AND o.entity_type = ? " if entity_type is not None else ""
        sql = (
            "SELECT o.* FROM observations o WHERE o.recorded_time <= ? AND "
            "(SELECT status FROM status_events WHERE observation_id = o.id AND at <= ? "
            "ORDER BY at DESC, id DESC LIMIT 1) = 'accepted' "
            f"{type_filter}"
            "ORDER BY o.entity_id, o.valid_time, o.recorded_time, o.id"
        )
        params: list[Any] = [t, t]
        if entity_type is not None:
            params.append(entity_type)
        rows = self._conn.execute(sql, params).fetchall()
        by_entity: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_entity.setdefault(str(row["entity_id"]), []).append(row)
        results: list[dict[str, Any]] = []
        for entity_id in sorted(by_entity):
            entity_rows = by_entity[entity_id]
            properties: dict[str, Any] = {}
            facets: Any = []
            for row in entity_rows:
                value = json.loads(row["value"])
                if row["property"] == FACETS_PROPERTY:
                    facets = value
                else:
                    properties[row["property"]] = value
            if facet is not None and facet not in facets:
                continue
            results.append(
                {
                    "entity_id": entity_id,
                    "entity_type": str(entity_rows[-1]["entity_type"]),
                    "properties": properties,
                    "facets": facets,
                    "as_of": max(str(r["recorded_time"]) for r in entity_rows),
                }
            )
        return results

    # ------------------------------------------------------- row conversion

    @staticmethod
    def _projection_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """projections_current row → dict with JSON columns decoded."""
        return {
            "entity_id": row["entity_id"],
            "entity_type": row["entity_type"],
            "properties": json.loads(row["properties"]),
            "facets": json.loads(row["facets"]),
            "as_of": row["as_of"],
        }

    @staticmethod
    def _observation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """observations row → dict with JSON columns decoded."""
        result = dict(row)
        result["value"] = json.loads(result["value"])
        if result["evidence"] is not None:
            result["evidence"] = json.loads(result["evidence"])
        return result
