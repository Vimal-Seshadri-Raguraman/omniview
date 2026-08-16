"""Panel registry tests — discovery, band derivation, and refusals."""

import shutil
from pathlib import Path

import pytest

from intermediate.shell.registry import PanelError, discover_panels

FIXTURE_PANELS = Path("tests/fixtures/panels")
FIXTURE_MODULES = Path("tests/fixtures/modules")


def copy_fixture(tmp_path: Path, new_name: str | None = None) -> Path:
    """Copy the fixture panel into a tmp panels dir, optionally renamed."""
    panels_dir = tmp_path / "panels"
    name = new_name or "fixture-panel"
    shutil.copytree(FIXTURE_PANELS / "fixture-panel", panels_dir / name)
    return panels_dir


def rewrite(panels_dir: Path, name: str, **overrides: object) -> None:
    """Rewrite one top-level key of a copied panel.yaml via crude line editing."""
    import yaml

    path = panels_dir / name / "panel.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest.update(overrides)
    path.write_text(yaml.safe_dump(manifest))


def test_discovers_fixture_panel() -> None:
    panels = discover_panels(FIXTURE_PANELS, FIXTURE_MODULES)
    assert [p.name for p in panels] == ["fixture-panel"]
    panel = panels[0]
    assert panel.band == "L1"
    assert panel.mirrors == "layer:L1"
    assert panel.order == 10
    assert panel.data_source is None
    assert panel.render(panel.ctx) == '<p class="fixture">hello from fixture-panel</p>'


def test_missing_panels_dir_is_empty(tmp_path: Path) -> None:
    assert discover_panels(tmp_path / "absent", FIXTURE_MODULES) == []


def test_dot_and_dunder_folders_skipped(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    (panels_dir / ".retired").mkdir()
    (panels_dir / "__pycache__").mkdir()
    assert len(discover_panels(panels_dir, FIXTURE_MODULES)) == 1


def test_refuses_folder_without_panel_yaml(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    (panels_dir / "bare").mkdir()
    with pytest.raises(PanelError, match="no panel.yaml"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_refuses_name_folder_mismatch(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path, new_name="renamed-folder")
    with pytest.raises(PanelError, match="does not equal its folder name"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_refuses_unknown_mirrors_form(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    rewrite(panels_dir, "fixture-panel", mirrors="widget:thing")
    with pytest.raises(PanelError, match="mirrors"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_refuses_mirrors_module_absent(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    rewrite(panels_dir, "fixture-panel", mirrors="module:no-such-module")
    with pytest.raises(PanelError, match="no-such-module"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_band_derived_from_mirrored_module_layer(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    rewrite(panels_dir, "fixture-panel", mirrors="module:sched-fixture")
    panels = discover_panels(panels_dir, FIXTURE_MODULES)
    assert panels[0].band == "L1"  # sched-fixture manifest declares layer: 1


def test_refuses_non_int_order(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    rewrite(panels_dir, "fixture-panel", order="ten")
    with pytest.raises(PanelError, match="order"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_refuses_low_poll_with_source(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    rewrite(
        panels_dir,
        "fixture-panel",
        data={"source": "http://localhost:1/x", "poll": 0},
    )
    with pytest.raises(PanelError, match="poll"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_refuses_missing_entrypoint_callable(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    rewrite(panels_dir, "fixture-panel", entrypoint="panel.py:missing")
    with pytest.raises(PanelError, match="missing"):
        discover_panels(panels_dir, FIXTURE_MODULES)


def test_sorted_by_band_then_order(tmp_path: Path) -> None:
    panels_dir = copy_fixture(tmp_path)
    shutil.copytree(panels_dir / "fixture-panel", panels_dir / "second-panel")
    import yaml

    path = panels_dir / "second-panel" / "panel.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest.update({"name": "second-panel", "order": 5})
    path.write_text(yaml.safe_dump(manifest))
    names = [p.name for p in discover_panels(panels_dir, FIXTURE_MODULES)]
    assert names == ["second-panel", "fixture-panel"]
