"""Tests for core.registry + core.run (plan Task 6, OV-15).

The registry is the gatekeeper: it discovers module folders, validates
manifests against the manifest-spec checklist (refusing invalid ones with
the rule named), wires triggers, and fires entrypoints. core.run boots the
whole engine — including with zero modules, the modularity law's
degenerate case.
"""

from pathlib import Path

import pytest

from core.bus import Bus
from core.ontology.store import OntologyStore
from core.registry import ManifestError, Registry
from core.run import main

FIXTURES = Path(__file__).parent / "fixtures" / "modules"

VALID_MANIFEST = """\
name: {name}
kind: {kind}
layer: {layer}
description: A tmp test module.
produces: {produces}
consumes: []
triggers:
  {triggers}
scopes: []
runtime:
  entrypoint: module.py:run
  agent: false
  secrets: {secrets}
  budget: {budget}
"""

MODULE_BODY = '''\
"""Tmp fixture module."""


def run(ctx, topic=None, payload=None):
    """No-op entrypoint."""
'''


def write_module(
    parent: Path,
    folder: str,
    *,
    name: str | None = None,
    kind: str = "connector",
    layer: int = 1,
    produces: str = "[RawRecord:tmp]",
    triggers: str = 'schedule: "1h"',
    secrets: str = "[]",
    budget: str = "null",
) -> Path:
    """Write a throwaway module folder for validation tests."""
    module_dir = parent / folder
    module_dir.mkdir(parents=True)
    manifest = VALID_MANIFEST.format(
        name=name if name is not None else folder,
        kind=kind,
        layer=layer,
        produces=produces,
        triggers=triggers,
        secrets=secrets,
        budget=budget,
    )
    (module_dir / "manifest.yaml").write_text(manifest)
    (module_dir / "module.py").write_text(MODULE_BODY)
    return module_dir


@pytest.fixture()
def store(tmp_path: Path) -> OntologyStore:
    """A fresh store."""
    return OntologyStore(str(tmp_path / "test.db"))


@pytest.fixture()
def bus() -> Bus:
    """A fresh bus."""
    return Bus()


class TestLoadValidation:
    """Invalid manifests are refused with the violated rule named."""

    def test_valid_fixture_pair_loads(self, store: OntologyStore, bus: Bus) -> None:
        registry = Registry(str(FIXTURES), store, bus)
        loaded = registry.load()
        assert sorted(m.name for m in loaded) == ["event-fixture", "sched-fixture"]

    def test_name_folder_mismatch_refused(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        write_module(modules, "folder-name", name="other-name")
        with pytest.raises(ManifestError, match="folder"):
            Registry(str(modules), store, bus).load()

    def test_unregistered_produces_refused(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        write_module(
            modules, "bad-produces", kind="transform", layer=2, produces="[Hotspot]"
        )
        with pytest.raises(ManifestError, match="Hotspot"):
            Registry(str(modules), store, bus).load()

    def test_two_trigger_styles_refused(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        write_module(
            modules,
            "two-triggers",
            triggers='schedule: "1h"\n  event: "raw.landed:x"',
        )
        with pytest.raises(ManifestError, match="trigger"):
            Registry(str(modules), store, bus).load()

    def test_kind_layer_mismatch_refused(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        write_module(modules, "bad-layer", kind="connector", layer=2)
        with pytest.raises(ManifestError, match="layer"):
            Registry(str(modules), store, bus).load()

    def test_secrets_without_budget_refused(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        write_module(modules, "metered", secrets="[SOME_TOKEN]", budget="null")
        with pytest.raises(ManifestError, match="budget"):
            Registry(str(modules), store, bus).load()

    def test_missing_manifest_refused(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        (modules / "no-manifest").mkdir(parents=True)
        with pytest.raises(ManifestError, match="manifest.yaml"):
            Registry(str(modules), store, bus).load()

    def test_zero_modules_boots(
        self, tmp_path: Path, store: OntologyStore, bus: Bus
    ) -> None:
        modules = tmp_path / "modules"
        modules.mkdir()
        registry = Registry(str(modules), store, bus)
        assert registry.load() == []
        registry.wire()  # wiring nothing is legal — modularity's degenerate case
        registry.shutdown()


class TestWiringAndFiring:
    """Triggers wire to the bus / timers; entrypoints fire correctly."""

    def _loaded_registry(self, store: OntologyStore, bus: Bus) -> Registry:
        registry = Registry(str(FIXTURES), store, bus)
        registry.load()
        registry.wire()
        return registry

    def test_event_wiring_invokes_handler(self, store: OntologyStore, bus: Bus) -> None:
        registry = self._loaded_registry(store, bus)
        try:
            event_module = registry.get("event-fixture").py_module
            bus.publish("raw.landed:sched-fixture", {"raw_id": "test-id"})
            assert event_module.CALLS == [
                ("raw.landed:sched-fixture", {"raw_id": "test-id"})
            ]
        finally:
            registry.shutdown()

    def test_non_matching_topic_does_not_invoke(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        registry = self._loaded_registry(store, bus)
        try:
            event_module = registry.get("event-fixture").py_module
            bus.publish("raw.landed:other-source", {})
            assert event_module.CALLS == []
        finally:
            registry.shutdown()

    def test_run_once_fires_schedule_module(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        registry = Registry(str(FIXTURES), store, bus)
        registry.load()
        sched_module = registry.get("sched-fixture").py_module
        registry.run_once("sched-fixture")
        assert sched_module.CALLS == ["ran"]

    def test_run_once_refuses_event_module(
        self, store: OntologyStore, bus: Bus
    ) -> None:
        registry = Registry(str(FIXTURES), store, bus)
        registry.load()
        with pytest.raises(ValueError, match="event"):
            registry.run_once("event-fixture")

    def test_run_once_unknown_module(self, store: OntologyStore, bus: Bus) -> None:
        registry = Registry(str(FIXTURES), store, bus)
        registry.load()
        with pytest.raises(ValueError, match="no-such-module"):
            registry.run_once("no-such-module")


class TestCoreRun:
    """python -m core.run — the ignition."""

    def test_once_with_zero_modules_exits_clean(self, tmp_path: Path) -> None:
        modules = tmp_path / "modules"
        modules.mkdir()
        exit_code = main(
            [
                "--modules-dir",
                str(modules),
                "--db",
                str(tmp_path / "boot.db"),
                "--once",
            ]
        )
        assert exit_code == 0

    def test_once_fires_schedule_modules(self, tmp_path: Path) -> None:
        exit_code = main(
            [
                "--modules-dir",
                str(FIXTURES),
                "--db",
                str(tmp_path / "boot.db"),
                "--once",
            ]
        )
        assert exit_code == 0
