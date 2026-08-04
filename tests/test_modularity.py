"""Constitutional law: the modularity test.

Delete any module - no other module may notice. Applies to agents.
Activates in Phase 1 when the registry and module zero exist.
"""
import pytest

pytestmark = pytest.mark.skip(reason="Phase 1: activates when the registry and module zero exist")


def test_system_boots_with_any_single_module_removed() -> None:
    """For each module folder: boot the platform without it; assert no other module errors."""
