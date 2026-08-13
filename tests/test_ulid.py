"""Tests for lib.ulid — ULID generation (plan Task 2, OV-11).

Acceptance criteria: 1000 ULIDs are all 26 chars, all unique, and their
sorted order equals their creation order (lexical sortability by time).
"""

from lib.ulid import CROCKFORD_ALPHABET, new_ulid

GENERATION_COUNT = 1000
ULID_LENGTH = 26


def test_ulid_length() -> None:
    """Every generated ULID is exactly 26 characters."""
    ids = [new_ulid() for _ in range(GENERATION_COUNT)]
    assert all(len(u) == ULID_LENGTH for u in ids)


def test_ulid_alphabet() -> None:
    """ULIDs use only the Crockford base32 alphabet (no I, L, O, U)."""
    ids = [new_ulid() for _ in range(GENERATION_COUNT)]
    allowed = set(CROCKFORD_ALPHABET)
    for u in ids:
        assert set(u) <= allowed, f"ULID '{u}' contains non-Crockford characters"


def test_ulid_uniqueness() -> None:
    """1000 ULIDs generated back-to-back are all distinct."""
    ids = [new_ulid() for _ in range(GENERATION_COUNT)]
    assert len(set(ids)) == GENERATION_COUNT


def test_ulid_sortable_by_creation_order() -> None:
    """Lexically sorting ULIDs reproduces their creation order exactly."""
    ids = [new_ulid() for _ in range(GENERATION_COUNT)]
    assert sorted(ids) == ids
