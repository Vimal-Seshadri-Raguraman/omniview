"""Tests for core.ontology.store — the one memory (plan Task 3, OV-12).

Covers the full Task 3 checklist: raw dedup, promotion rules, append-only,
projection maintenance, facets, rejection, history, search, time travel,
rebuild equality, and batch transactionality (A-002/A-003/A-005/A-007).
"""

import time
from pathlib import Path

import pytest

from core.ontology.store import OntologyStore

ISS = "sat:norad:25544"
NOW = "2026-08-13T00:00:00+00:00"
LATER = "2026-08-14T00:00:00+00:00"


@pytest.fixture()
def store(tmp_path: Path) -> OntologyStore:
    """A fresh store backed by a temp SQLite file."""
    return OntologyStore(str(tmp_path / "test.db"))


def propose_name(
    store: OntologyStore, value: str = "ISS (ZARYA)", **overrides: object
) -> str:
    """Propose a simple name observation with sensible defaults."""
    kwargs: dict = dict(
        entity_id=ISS,
        entity_type="Satellite",
        property="name",
        value=value,
        valid_time=NOW,
        method="parsed",
        source_module="test-module",
    )
    kwargs.update(overrides)
    return store.propose(**kwargs)


class TestRawLandingZone:
    """land_raw / latest_raw / get_raw_payload (L1 tier)."""

    def test_land_raw_returns_new_id(self, store: OntologyStore) -> None:
        raw_id, is_new = store.land_raw("celestrak", b"payload-1", "celestrak-gp-json")
        assert is_new is True
        assert len(raw_id) == 26

    def test_land_raw_dedups_identical_payload(self, store: OntologyStore) -> None:
        first_id, _ = store.land_raw("celestrak", b"same-bytes", "celestrak-gp-json")
        second_id, is_new = store.land_raw(
            "celestrak", b"same-bytes", "celestrak-gp-json"
        )
        assert second_id == first_id
        assert is_new is False

    def test_same_payload_different_source_is_new(self, store: OntologyStore) -> None:
        id_a, _ = store.land_raw("celestrak", b"same-bytes", "celestrak-gp-json")
        id_b, is_new = store.land_raw("other", b"same-bytes", "other-format")
        assert is_new is True
        assert id_b != id_a

    def test_latest_raw_metadata(self, store: OntologyStore) -> None:
        assert store.latest_raw("celestrak") is None
        raw_id, _ = store.land_raw("celestrak", b"payload", "celestrak-gp-json")
        latest = store.latest_raw("celestrak")
        assert latest is not None
        assert latest["id"] == raw_id
        assert set(latest) == {"id", "fetched_at", "content_hash"}

    def test_get_raw_payload_roundtrip(self, store: OntologyStore) -> None:
        raw_id, _ = store.land_raw("celestrak", b"exact-bytes", "celestrak-gp-json")
        assert store.get_raw_payload(raw_id) == b"exact-bytes"

    def test_get_raw_payload_missing_raises(self, store: OntologyStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.get_raw_payload("01ARZ3NDEKTSV4RRFFQ69G5FAV")


class TestPromotionRules:
    """propose status assignment: auto-accept vs agent-staged (A-002)."""

    def test_non_agent_high_confidence_auto_accepts(self, store: OntologyStore) -> None:
        obs_id = propose_name(store, confidence=1.0)
        assert store.current_status(obs_id) == "accepted"

    def test_agent_always_lands_proposed(self, store: OntologyStore) -> None:
        obs_id = propose_name(store, agent=True, confidence=1.0)
        assert store.current_status(obs_id) == "proposed"

    def test_low_confidence_lands_proposed(self, store: OntologyStore) -> None:
        obs_id = propose_name(store, confidence=0.5)
        assert store.current_status(obs_id) == "proposed"

    def test_unregistered_type_refused(self, store: OntologyStore) -> None:
        with pytest.raises(ValueError, match="Hotspot"):
            propose_name(store, entity_type="Hotspot")

    def test_bad_entity_id_refused(self, store: OntologyStore) -> None:
        with pytest.raises(ValueError, match="ship:123"):
            propose_name(store, entity_id="ship:123")


class TestAppendOnly:
    """Status changes append events; nothing is ever updated (A-002)."""

    def test_set_status_appends_and_preserves_history(
        self, store: OntologyStore
    ) -> None:
        obs_id = propose_name(store)
        assert store.count_rows("status_events") == 1
        store.set_status(obs_id, "rejected", actor="human", reason="test rejection")
        assert store.count_rows("status_events") == 2
        assert store.current_status(obs_id) == "rejected"
        # The observation row itself is untouched.
        assert store.count_rows("observations") == 1

    def test_set_status_unknown_observation_raises(self, store: OntologyStore) -> None:
        with pytest.raises(ValueError, match="not found"):
            store.set_status("01ARZ3NDEKTSV4RRFFQ69G5FAV", "accepted", "human", None)


class TestProjections:
    """The whiteboard reflects latest accepted values only."""

    def test_projection_shows_latest_accepted_value(self, store: OntologyStore) -> None:
        propose_name(store, value="OLD NAME", valid_time=NOW)
        propose_name(store, value="NEW NAME", valid_time=LATER)
        projection = store.get(ISS)
        assert projection is not None
        assert projection["properties"]["name"] == "NEW NAME"
        assert projection["entity_type"] == "Satellite"

    def test_projection_skips_proposed(self, store: OntologyStore) -> None:
        propose_name(store, value="ACCEPTED", valid_time=NOW)
        propose_name(store, value="STAGED", valid_time=LATER, agent=True)
        projection = store.get(ISS)
        assert projection is not None
        assert projection["properties"]["name"] == "ACCEPTED"

    def test_facets_land_in_facets_column(self, store: OntologyStore) -> None:
        propose_name(store)
        propose_name(store, property="facets", value=["space"])
        projection = store.get(ISS)
        assert projection is not None
        assert projection["facets"] == ["space"]
        assert "facets" not in projection["properties"]

    def test_rejection_removes_value_from_projection(
        self, store: OntologyStore
    ) -> None:
        obs_id = propose_name(store, value="WRONG")
        store.set_status(obs_id, "rejected", actor="human", reason="bad parse")
        projection = store.get(ISS)
        assert projection is None or "name" not in projection["properties"]
        store.rebuild_projections()
        rebuilt = store.get(ISS)
        assert rebuilt is None or "name" not in rebuilt["properties"]

    def test_get_unknown_entity_returns_none(self, store: OntologyStore) -> None:
        assert store.get("sat:norad:99999") is None


class TestHistory:
    """history() reads the ledger in valid_time order."""

    def test_history_ordered_and_ranged(self, store: OntologyStore) -> None:
        propose_name(store, value="B", valid_time="2026-08-12T00:00:00+00:00")
        propose_name(store, value="A", valid_time="2026-08-11T00:00:00+00:00")
        propose_name(store, value="C", valid_time="2026-08-13T00:00:00+00:00")
        rows = store.history(ISS, "name")
        assert [r["value"] for r in rows] == ["A", "B", "C"]
        ranged = store.history(
            ISS,
            "name",
            t_from="2026-08-11T12:00:00+00:00",
            t_to="2026-08-12T12:00:00+00:00",
        )
        assert [r["value"] for r in ranged] == ["B"]


class TestSearch:
    """search() over projections, with facet filter and time travel."""

    def test_search_by_type_and_facet(self, store: OntologyStore) -> None:
        propose_name(store)
        propose_name(store, property="facets", value=["space"])
        assert [p["entity_id"] for p in store.search(entity_type="Satellite")] == [ISS]
        assert [p["entity_id"] for p in store.search(facet="space")] == [ISS]
        assert store.search(entity_type="Satellite", facet="maritime") == []

    def test_time_travel_returns_value_believed_at_t(
        self, store: OntologyStore
    ) -> None:
        propose_name(store, value="FIRST", valid_time=NOW)
        time.sleep(0.002)
        t_between = store.now()
        time.sleep(0.002)
        propose_name(store, value="SECOND", valid_time=LATER)
        current = store.search(entity_type="Satellite")
        assert current[0]["properties"]["name"] == "SECOND"
        believed = store.search(entity_type="Satellite", t=t_between)
        assert believed[0]["properties"]["name"] == "FIRST"


class TestRebuild:
    """rebuild_projections() reproduces the incremental table exactly."""

    def test_rebuild_matches_incremental(self, store: OntologyStore) -> None:
        propose_name(store, value="ISS (ZARYA)")
        propose_name(store, property="facets", value=["space"])
        propose_name(
            store,
            entity_id="sat:norad:20580",
            value="HST",
            property="name",
        )
        rejected = propose_name(store, value="TYPO", valid_time=LATER)
        store.set_status(rejected, "rejected", actor="human", reason="typo")
        incremental = store.snapshot_projections()
        store.rebuild_projections()
        assert store.snapshot_projections() == incremental
        assert incremental  # non-empty guard


class TestProposeBatch:
    """propose_batch: one transaction, all-or-nothing (A-005)."""

    def _batch(self, n: int) -> list[dict]:
        return [
            dict(
                entity_id=f"sat:norad:{10000 + i}",
                entity_type="Satellite",
                property="name",
                value=f"SAT-{i}",
                valid_time=NOW,
                method="parsed",
                source_module="test-module",
            )
            for i in range(n)
        ]

    def test_batch_of_500_is_one_transaction(self, store: OntologyStore) -> None:
        statements: list[str] = []
        store.set_trace_callback(statements.append)
        ids = store.propose_batch(self._batch(500))
        store.set_trace_callback(None)
        assert len(ids) == 500
        begins = [s for s in statements if s.strip().upper().startswith("BEGIN")]
        assert len(begins) == 1, f"expected 1 BEGIN, saw {len(begins)}"

    def test_batch_is_all_or_nothing_on_bad_row(self, store: OntologyStore) -> None:
        proposals = self._batch(10)
        proposals[5]["entity_id"] = "ship:123"  # invalid scheme → must abort all
        with pytest.raises(ValueError, match="ship:123"):
            store.propose_batch(proposals)
        assert store.count_rows("observations") == 0
        assert store.count_rows("status_events") == 0
        assert store.search(entity_type="Satellite") == []

    def test_batch_values_land(self, store: OntologyStore) -> None:
        store.propose_batch(self._batch(3))
        results = store.search(entity_type="Satellite")
        assert len(results) == 3
        values = {p["properties"]["name"] for p in results}
        assert values == {"SAT-0", "SAT-1", "SAT-2"}


class TestValueSerialization:
    """Values round-trip through JSON."""

    def test_structured_value_roundtrip(self, store: OntologyStore) -> None:
        tle = {"line1": "1 25544U ...", "line2": "2 25544 ...", "epoch": NOW}
        propose_name(store, property="tle", value=tle)
        projection = store.get(ISS)
        assert projection is not None
        assert projection["properties"]["tle"] == tle

    def test_evidence_stored_as_json_array(self, store: OntologyStore) -> None:
        raw_id, _ = store.land_raw("celestrak", b"x", "celestrak-gp-json")
        obs_id = propose_name(store, evidence=[raw_id])
        rows = store.history(ISS, "name")
        assert rows[0]["evidence"] == [raw_id]
        assert rows[0]["id"] == obs_id
