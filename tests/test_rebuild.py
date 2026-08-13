"""Constitutional law: rebuildability (scoped by amendment A-007).

Three assertions once core v0 exists (Phase 1):
(a) projections == rebuild(log), unconditionally;
(b) mechanically-derived observations (parsed, computed:*) == replay(raw);
(c) judgment rows (inferred:*, asserted:human) and status_events survive
    replay untouched.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="Phase 1: activates when core v0 (ontology log + projections) exists"
)


def test_projections_rebuild_from_log() -> None:
    """Law (a): rebuild_projections() reproduces projections_current exactly."""


def test_mechanical_observations_replay_from_raw() -> None:
    """Law (b): replaying the raw landing zone regenerates parsed/computed rows."""


def test_judgment_rows_survive_replay() -> None:
    """Law (c): inferred/asserted rows and status_events are preserved, never regenerated."""
