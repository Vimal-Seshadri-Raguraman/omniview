"""Module registry — discovery, validation, trigger wiring (manifest-spec v0).

The registry is the gatekeeper of the outer ring: it discovers folders
under the modules directory, validates each manifest against the spec's
checklist, REFUSES invalid ones (raise ManifestError naming the violated
rule — no warnings), builds each module's SDK ctx, and wires triggers:
event patterns onto the bus, schedules onto repeating timer threads, and
"continuous" modules into supervised restart-on-crash threads.
"""

import importlib.util
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import yaml

from core.bus import Bus
from core.ontology.store import OntologyStore
from core.schema.registry import is_registered_produces
from lib.ulid import new_ulid
from sdk.context import Ctx, build_ctx

_LOG = logging.getLogger("omniview.registry")

REQUIRED_FIELDS = (
    "name",
    "kind",
    "layer",
    "produces",
    "consumes",
    "triggers",
    "runtime",
)
KIND_LEGAL_LAYERS: dict[str, frozenset[int]] = {
    "connector": frozenset({1}),
    "transform": frozenset({2}),
    "analytic": frozenset({2}),
    "view": frozenset({4, 5}),
}
TRIGGER_STYLES = ("schedule", "event")
CONTINUOUS = "continuous"
_SCHEDULE_RE = re.compile(r"(\d+)([smh])")
_SCHEDULE_UNITS = {"s": 1, "m": 60, "h": 3600}
CONTINUOUS_RESTART_DELAY_S = 1.0


class ManifestError(Exception):
    """A manifest failed validation; the message names the violated rule."""


@dataclass
class LoadedModule:
    """A validated, imported module ready to fire."""

    name: str
    manifest: dict[str, Any]
    trigger_style: str  # "schedule" | "event" | "continuous"
    schedule_seconds: float | None
    event_pattern: str | None
    entrypoint: Callable[..., None]
    py_module: ModuleType
    ctx: Ctx


def _parse_schedule(value: str, module_name: str) -> tuple[str, float | None]:
    """Parse a schedule string into (style, seconds).

    Args:
        value: "<n>s|m|h" or "continuous".
        module_name: For error messages.

    Returns:
        ("continuous", None) or ("schedule", seconds).

    Raises:
        ManifestError: On an unparseable schedule string.
    """
    if value == CONTINUOUS:
        return CONTINUOUS, None
    match = _SCHEDULE_RE.fullmatch(value)
    if match is None:
        raise ManifestError(
            f"Module '{module_name}': schedule '{value}' is not '<n>s|m|h' or 'continuous'"
        )
    return "schedule", float(int(match.group(1)) * _SCHEDULE_UNITS[match.group(2)])


class Registry:
    """Discovers, validates, wires, and fires modules."""

    def __init__(self, modules_dir: str, store: OntologyStore, bus: Bus) -> None:
        """Create a registry over a modules directory.

        Args:
            modules_dir: Directory whose child folders are modules.
            store: The one ontology store (injected into each ctx).
            bus: The one event bus (injected into each ctx).
        """
        self._modules_dir = Path(modules_dir)
        self._store = store
        self._bus = bus
        self._loaded: dict[str, LoadedModule] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # ------------------------------------------------------------------ load

    def load(self) -> list[LoadedModule]:
        """Discover and validate all module folders; refuse any invalid one.

        Returns:
            The loaded modules, folder-name order.

        Raises:
            ManifestError: On the first invalid manifest, naming the rule.
        """
        self._loaded = {}
        for folder in sorted(self._modules_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith((".", "__")):
                continue
            module = self._load_one(folder)
            self._loaded[module.name] = module
        return list(self._loaded.values())

    def _load_one(self, folder: Path) -> LoadedModule:
        """Validate one module folder and import its entrypoint."""
        manifest_path = folder / "manifest.yaml"
        if not manifest_path.is_file():
            raise ManifestError(f"Module folder '{folder.name}' has no manifest.yaml")
        manifest = yaml.safe_load(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise ManifestError(
                f"Module '{folder.name}': manifest.yaml is not a mapping"
            )
        for field in REQUIRED_FIELDS:
            if field not in manifest:
                raise ManifestError(
                    f"Module '{folder.name}': manifest missing required field '{field}'"
                )
        name = str(manifest["name"])
        if name != folder.name:
            raise ManifestError(
                f"Module name '{name}' does not equal its folder name '{folder.name}'"
            )
        self._validate_kind_layer(manifest, name)
        self._validate_produces(manifest, name)
        trigger_style, seconds, pattern = self._validate_triggers(manifest, name)
        self._validate_budget(manifest, name)
        entrypoint, py_module = self._import_entrypoint(folder, manifest, name)
        ctx = build_ctx(manifest=manifest, store=self._store, bus=self._bus)
        return LoadedModule(
            name=name,
            manifest=manifest,
            trigger_style=trigger_style,
            schedule_seconds=seconds,
            event_pattern=pattern,
            entrypoint=entrypoint,
            py_module=py_module,
            ctx=ctx,
        )

    @staticmethod
    def _validate_kind_layer(manifest: dict[str, Any], name: str) -> None:
        """Rule 2: the kind/layer combination must be legal."""
        kind = str(manifest["kind"])
        if kind not in KIND_LEGAL_LAYERS:
            raise ManifestError(
                f"Module '{name}': unknown kind '{kind}' "
                f"(legal: {sorted(KIND_LEGAL_LAYERS)})"
            )
        layer = manifest["layer"]
        if layer not in KIND_LEGAL_LAYERS[kind]:
            raise ManifestError(
                f"Module '{name}': kind '{kind}' is illegal in layer {layer} "
                f"(legal layers: {sorted(KIND_LEGAL_LAYERS[kind])})"
            )

    @staticmethod
    def _validate_produces(manifest: dict[str, Any], name: str) -> None:
        """Rule 3: every produces entry must pass the schema registry."""
        for type_str in manifest["produces"]:
            if not is_registered_produces(str(type_str)):
                raise ManifestError(
                    f"Module '{name}': produces type '{type_str}' is not registered "
                    "in the schema registry (register the type, then the module)"
                )

    @staticmethod
    def _validate_triggers(
        manifest: dict[str, Any], name: str
    ) -> tuple[str, float | None, str | None]:
        """Rule 4: exactly one trigger style; parse it."""
        triggers = manifest["triggers"]
        if not isinstance(triggers, dict):
            raise ManifestError(f"Module '{name}': triggers must be a mapping")
        styles = [s for s in TRIGGER_STYLES if s in triggers]
        if len(styles) != 1:
            raise ManifestError(
                f"Module '{name}': exactly one trigger style required, found {styles or 'none'}"
            )
        if styles[0] == "event":
            return "event", None, str(triggers["event"])
        style, seconds = _parse_schedule(str(triggers["schedule"]), name)
        return style, seconds, None

    @staticmethod
    def _validate_budget(manifest: dict[str, Any], name: str) -> None:
        """Rule 6: metered secrets require a budget."""
        runtime = manifest["runtime"]
        if runtime.get("secrets") and not runtime.get("budget"):
            raise ManifestError(
                f"Module '{name}': secrets {runtime['secrets']} declared without a budget"
            )

    def _import_entrypoint(
        self, folder: Path, manifest: dict[str, Any], name: str
    ) -> tuple[Callable[..., None], ModuleType]:
        """Import the runtime entrypoint ("<file>.py:<callable>") via importlib."""
        entrypoint_spec = str(manifest["runtime"].get("entrypoint", ""))
        if ":" not in entrypoint_spec:
            raise ManifestError(
                f"Module '{name}': runtime.entrypoint '{entrypoint_spec}' "
                "is not '<file>.py:<callable>'"
            )
        file_name, callable_name = entrypoint_spec.split(":", 1)
        file_path = folder / file_name
        if not file_path.is_file():
            raise ManifestError(
                f"Module '{name}': entrypoint file '{file_name}' not found"
            )
        # Unique module key per load — fixture reloads stay isolated.
        spec = importlib.util.spec_from_file_location(
            f"omniview_module_{name.replace('-', '_')}_{new_ulid()}", file_path
        )
        if spec is None or spec.loader is None:
            raise ManifestError(f"Module '{name}': cannot import '{file_name}'")
        py_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(py_module)
        entrypoint = getattr(py_module, callable_name, None)
        if not callable(entrypoint):
            raise ManifestError(
                f"Module '{name}': entrypoint callable '{callable_name}' "
                f"not found in '{file_name}'"
            )
        return entrypoint, py_module

    # ------------------------------------------------------------------ wire

    def wire(self) -> None:
        """Wire triggers: events onto the bus, schedules onto timer threads."""
        for module in self._loaded.values():
            if module.trigger_style == "event":
                self._bus.subscribe(
                    str(module.event_pattern), self._event_handler(module)
                )
            elif module.trigger_style == "schedule":
                self._threads.append(self._schedule_thread(module))
            else:  # continuous
                self._threads.append(self._continuous_thread(module))
        for thread in self._threads:
            thread.start()

    @staticmethod
    def _event_handler(module: LoadedModule) -> Callable[[str, dict], None]:
        """Handler closure delivering bus events to run(ctx, topic, payload)."""

        def handler(topic: str, payload: dict) -> None:
            module.entrypoint(module.ctx, topic, payload)

        return handler

    def _schedule_thread(self, module: LoadedModule) -> threading.Thread:
        """A daemon thread firing run(ctx) every schedule interval."""

        def loop() -> None:
            interval = float(module.schedule_seconds or 0)
            while not self._stop.wait(interval):
                self._fire_supervised(module)

        return threading.Thread(
            target=loop, name=f"schedule:{module.name}", daemon=True
        )

    def _continuous_thread(self, module: LoadedModule) -> threading.Thread:
        """A supervised daemon thread: run(ctx) restarts on crash."""

        def loop() -> None:
            while not self._stop.is_set():
                self._fire_supervised(module)
                self._stop.wait(CONTINUOUS_RESTART_DELAY_S)

        return threading.Thread(
            target=loop, name=f"continuous:{module.name}", daemon=True
        )

    def _fire_supervised(self, module: LoadedModule) -> None:
        """Invoke a schedule/continuous entrypoint; log crashes, never die."""
        try:
            module.entrypoint(module.ctx)
        except Exception:
            _LOG.exception("Module '%s' crashed; registry supervises", module.name)

    # ------------------------------------------------------------------ fire

    def get(self, module_name: str) -> LoadedModule:
        """Look up a loaded module by name.

        Raises:
            ValueError: If no module with this name is loaded.
        """
        if module_name not in self._loaded:
            raise ValueError(
                f"Module '{module_name}' is not loaded "
                f"(loaded: {sorted(self._loaded) or 'none'})"
            )
        return self._loaded[module_name]

    def run_once(self, module_name: str) -> None:
        """Fire a schedule/continuous module's entrypoint once, synchronously.

        Raises:
            ValueError: For unknown modules or event-triggered modules
                (those fire only via their bus subscription).
        """
        module = self.get(module_name)
        if module.trigger_style == "event":
            raise ValueError(
                f"Module '{module_name}' is event-triggered; publish "
                f"'{module.event_pattern}' on the bus instead"
            )
        module.entrypoint(module.ctx)

    def shutdown(self) -> None:
        """Stop all schedule/continuous threads."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads = []
