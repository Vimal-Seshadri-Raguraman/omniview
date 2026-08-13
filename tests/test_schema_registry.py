"""Tests for core.schema.registry — type + identity registries (plan Task 2, OV-11).

Acceptance criteria:
- is_registered_produces: True for registered ontology types and any
  RawRecord:<source> with a nonempty source; False otherwise ("Hotspot"
  stays unregistered until Phase 5, per A-004).
- validate_entity_id: accepts ids matching a registered scheme
  (A-006/A-008), raises ValueError naming the offending id otherwise.
"""

import pytest

from core.schema.registry import (
    RAW_RECORD_PREFIX,
    REGISTERED_TYPES,
    is_registered_produces,
    validate_entity_id,
)

# Canonical 26-char Crockford ULID used in scheme tests.
SAMPLE_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class TestRegisteredTypes:
    """The v0 type registry holds exactly the three starter types."""

    def test_registered_types_exact(self) -> None:
        assert REGISTERED_TYPES == frozenset({"Satellite", "PositionState", "Alert"})

    def test_raw_record_prefix(self) -> None:
        assert RAW_RECORD_PREFIX == "RawRecord:"


class TestIsRegisteredProduces:
    """Produces validation: registered types + RawRecord:<source>."""

    def test_registered_type_accepted(self) -> None:
        assert is_registered_produces("Satellite") is True

    def test_raw_record_with_source_accepted(self) -> None:
        assert is_registered_produces("RawRecord:celestrak") is True

    def test_unregistered_type_refused(self) -> None:
        # Hotspot is registered in Phase 5 (A-004) — refuse until then.
        assert is_registered_produces("Hotspot") is False

    def test_raw_record_without_source_refused(self) -> None:
        assert is_registered_produces("RawRecord:") is False


class TestValidateEntityId:
    """Identity grammar <scheme>:<authority>:<local-id> per the registry."""

    @pytest.mark.parametrize(
        "entity_id",
        [
            "sat:norad:25544",
            "vessel:mmsi:366999712",
            "aircraft:icao24:a1b2c3",
            "site:omniview:port-of-rotterdam",
            "event:gdelt:20260813123456-42",
            f"alert:{SAMPLE_ULID}",
            "hotspot:8928308280fffff:2026-08-13T00",
        ],
        ids=["sat", "vessel", "aircraft", "site", "event", "alert", "hotspot"],
    )
    def test_valid_ids_pass(self, entity_id: str) -> None:
        validate_entity_id(entity_id)  # must not raise

    @pytest.mark.parametrize(
        "entity_id",
        [
            "ship:123",  # unknown scheme
            "sat:norad:ISS",  # non-digit local id
            "sat:25544",  # missing authority (sat does not collapse)
            "alert:not-a-ulid",  # malformed ULID
            "",  # empty
        ],
        ids=[
            "unknown-scheme",
            "bad-local-id",
            "missing-authority",
            "bad-ulid",
            "empty",
        ],
    )
    def test_invalid_ids_raise_naming_offender(self, entity_id: str) -> None:
        with pytest.raises(ValueError, match="does not match"):
            validate_entity_id(entity_id)

    def test_error_message_names_the_offending_id(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_entity_id("ship:123")
        assert "ship:123" in str(excinfo.value)
