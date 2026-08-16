"""core.dock tests — onboarding scaffolds that pass real registry validation."""

from pathlib import Path

import pytest

from core.bus import Bus
from core.dock import DockError, main, scaffold_module
from core.ontology.store import OntologyStore
from core.registry import Registry


def load_via_registry(modules_dir: Path, tmp_path: Path) -> list[str]:
    """Load a modules dir through the real registry; return loaded names."""
    store = OntologyStore(str(tmp_path / "dock-test.db"))
    registry = Registry(str(modules_dir), store, Bus())
    return [module.name for module in registry.load()]


def test_new_connector_scaffold_passes_registry(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    written = scaffold_module(
        name="test-feed",
        kind="connector",
        layer=1,
        trigger={"schedule": "2h"},
        modules_dir=modules_dir,
        dry_run=False,
    )
    assert (modules_dir / "test-feed" / "manifest.yaml") in written
    assert (modules_dir / "test-feed" / "module.py") in written
    assert any(p.name == "test_test_feed.py" for p in written)
    assert load_via_registry(modules_dir, tmp_path) == ["test-feed"]


def test_new_transform_scaffold_passes_registry(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    scaffold_module(
        name="test-shaper",
        kind="transform",
        layer=2,
        trigger={"event": "raw.landed:test-feed"},
        modules_dir=modules_dir,
        dry_run=False,
    )
    assert load_via_registry(modules_dir, tmp_path) == ["test-shaper"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    written = scaffold_module(
        name="test-feed",
        kind="connector",
        layer=1,
        trigger={"schedule": "2h"},
        modules_dir=modules_dir,
        dry_run=True,
    )
    assert len(written) == 3
    assert not (modules_dir / "test-feed").exists()


def test_refuses_existing_module(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    kwargs: dict = dict(
        name="test-feed",
        kind="connector",
        layer=1,
        trigger={"schedule": "2h"},
        modules_dir=modules_dir,
        dry_run=False,
    )
    scaffold_module(**kwargs)
    with pytest.raises(DockError, match="already exists"):
        scaffold_module(**kwargs)


def test_refuses_illegal_kind_layer(tmp_path: Path) -> None:
    with pytest.raises(DockError, match="illegal in layer"):
        scaffold_module(
            name="test-feed",
            kind="connector",
            layer=2,
            trigger={"schedule": "2h"},
            modules_dir=tmp_path / "modules",
            dry_run=False,
        )


def test_cli_new_exit_codes(tmp_path: Path) -> None:
    modules_dir = str(tmp_path / "modules")
    argv = [
        "new",
        "test-feed",
        "--kind",
        "connector",
        "--layer",
        "1",
        "--modules-dir",
        modules_dir,
    ]
    assert main(argv) == 0
    assert main(argv) == 2  # second run refused: already exists
