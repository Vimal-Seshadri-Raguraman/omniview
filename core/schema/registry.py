"""Type and identity registries — the core's vocabulary (ontology-v0, A-004/A-006/A-008).

Two registries live here:

- The **type registry**: which entity types a module may declare in its
  manifest `produces`. v0 starts with Satellite, PositionState, and Alert;
  later types (e.g. Hotspot in Phase 5, per A-004) are added by amendment —
  register the type, then the module.
- The **identity registry**: the legal `entity_id` schemes. The grammar is
  `<scheme>:<authority>:<local-id>` (A-006); internal schemes may collapse
  the authority segment (A-008) — the registry entry is authoritative.
"""

import re

REGISTERED_TYPES: frozenset[str] = frozenset({"Satellite", "PositionState", "Alert"})

RAW_RECORD_PREFIX = "RawRecord:"

# One pattern per registered scheme, keyed by scheme name. Each pattern
# matches the FULL entity id, so authority collapsing (A-008) is expressed
# directly in the entry (e.g. alert:<ulid> has no authority segment).
_CROCKFORD = "0-9ABCDEFGHJKMNPQRSTVWXYZ"
ENTITY_ID_SCHEMES: dict[str, re.Pattern[str]] = {
    "sat": re.compile(r"sat:norad:\d+"),
    "vessel": re.compile(r"vessel:mmsi:\d+"),
    "aircraft": re.compile(r"aircraft:icao24:[0-9a-f]+"),
    "site": re.compile(r"site:omniview:[a-z0-9]+(?:-[a-z0-9]+)*"),
    "event": re.compile(r"event:gdelt:[A-Za-z0-9._-]+"),
    "alert": re.compile(rf"alert:[{_CROCKFORD}]{{26}}"),
    "hotspot": re.compile(r"hotspot:[0-9a-f]{15}:\S+"),
}


def is_registered_produces(type_str: str) -> bool:
    """Check whether a manifest `produces` entry names a legal type.

    Args:
        type_str: A produces entry — either a registered ontology type
            (e.g. "Satellite") or a raw-record type "RawRecord:<source>".

    Returns:
        True for registered ontology types and for RawRecord:<source>
        with a nonempty source; False otherwise.
    """
    if type_str in REGISTERED_TYPES:
        return True
    return type_str.startswith(RAW_RECORD_PREFIX) and len(type_str) > len(
        RAW_RECORD_PREFIX
    )


def validate_entity_id(entity_id: str) -> None:
    """Validate an entity id against the registered identity schemes.

    Args:
        entity_id: Candidate id, e.g. "sat:norad:25544".

    Raises:
        ValueError: If the id's scheme is unregistered or the id does not
            match the scheme's registered pattern. The message names the
            offending id and the known schemes.
    """
    scheme = entity_id.split(":", 1)[0]
    pattern = ENTITY_ID_SCHEMES.get(scheme)
    if pattern is None or pattern.fullmatch(entity_id) is None:
        known = ", ".join(sorted(ENTITY_ID_SCHEMES))
        raise ValueError(
            f"Entity id '{entity_id}' does not match any registered identity scheme "
            f"(known schemes: {known})"
        )
