"""Panel registry — discovery, validation, refusal (panel contract v0).

The registry is to panels what core.registry is to modules: it discovers
folders under the panels directory, validates each panel.yaml against the
contract, REFUSES invalid ones (raise PanelError naming the violated rule
— no warnings), derives each panel's band from its `mirrors:` declaration,
and imports the render entrypoint.
"""

import importlib.util
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from lib.ulid import new_ulid

_LOG = logging.getLogger("omniview.intermediate.registry")

REQUIRED_FIELDS = ("name", "mirrors", "order", "description", "data", "entrypoint")
BANDS = ("L1", "L2", "L3", "L4", "L5")
_MIRRORS_LAYER_RE = re.compile(r"layer:(L[1-5])")
_MIRRORS_MODULE_RE = re.compile(r"module:([a-z0-9][a-z0-9-]*)")


class PanelError(Exception):
    """A panel failed validation; the message names the violated rule."""


@dataclass
class PanelCtx:
    """The panel's door: everything a render() call may touch."""

    name: str
    mirrors: str
    data_source: str | None
    log: logging.Logger


@dataclass
class LoadedPanel:
    """A validated panel ready to render into its band."""

    name: str
    mirrors: str
    band: str
    order: int
    description: str
    data_source: str | None
    poll_seconds: int
    render: Callable[[PanelCtx], str]
    ctx: PanelCtx


def discover_panels(panels_dir: Path, modules_dir: Path) -> list[LoadedPanel]:
    """Discover and validate all panel folders; refuse any invalid one.

    Args:
        panels_dir: Directory whose child folders are panels.
        modules_dir: Backend modules directory (for module: mirror targets).

    Returns:
        Loaded panels sorted by (band, order, name). Empty when panels_dir
        does not exist or holds no panel folders.

    Raises:
        PanelError: On the first invalid panel, naming the violated rule.
    """
    if not panels_dir.is_dir():
        return []
    panels: list[LoadedPanel] = []
    for folder in sorted(panels_dir.iterdir()):
        if not folder.is_dir() or folder.name.startswith((".", "__")):
            continue
        panels.append(_load_one(folder, modules_dir))
    return sorted(panels, key=lambda p: (p.band, p.order, p.name))


def _load_one(folder: Path, modules_dir: Path) -> LoadedPanel:
    """Validate one panel folder and import its render entrypoint."""
    contract_path = folder / "panel.yaml"
    if not contract_path.is_file():
        raise PanelError(f"Panel folder '{folder.name}' has no panel.yaml")
    contract = yaml.safe_load(contract_path.read_text())
    if not isinstance(contract, dict):
        raise PanelError(f"Panel '{folder.name}': panel.yaml is not a mapping")
    for field in REQUIRED_FIELDS:
        if field not in contract:
            raise PanelError(
                f"Panel '{folder.name}': panel.yaml missing required field '{field}'"
            )
    name = str(contract["name"])
    if name != folder.name:
        raise PanelError(
            f"Panel name '{name}' does not equal its folder name '{folder.name}'"
        )
    band = _derive_band(str(contract["mirrors"]), modules_dir, name)
    order = contract["order"]
    if isinstance(order, bool) or not isinstance(order, int):
        raise PanelError(f"Panel '{name}': order '{order}' is not an integer")
    data_source, poll_seconds = _validate_data(contract["data"], name)
    render = _import_render(folder, str(contract["entrypoint"]), name)
    ctx = PanelCtx(
        name=name,
        mirrors=str(contract["mirrors"]),
        data_source=data_source,
        log=logging.getLogger(f"omniview.panel.{name}"),
    )
    return LoadedPanel(
        name=name,
        mirrors=str(contract["mirrors"]),
        band=band,
        order=order,
        description=str(contract["description"]),
        data_source=data_source,
        poll_seconds=poll_seconds,
        render=render,
        ctx=ctx,
    )


def _derive_band(mirrors: str, modules_dir: Path, name: str) -> str:
    """Derive the band from the mirrors declaration (the 1-to-1 mapping)."""
    layer_match = _MIRRORS_LAYER_RE.fullmatch(mirrors)
    if layer_match:
        return layer_match.group(1)
    module_match = _MIRRORS_MODULE_RE.fullmatch(mirrors)
    if module_match:
        target = module_match.group(1)
        manifest_path = modules_dir / target / "manifest.yaml"
        if not manifest_path.is_file():
            raise PanelError(
                f"Panel '{name}': mirrors module '{target}' but "
                f"'{manifest_path}' does not exist"
            )
        manifest = yaml.safe_load(manifest_path.read_text())
        layer = manifest.get("layer") if isinstance(manifest, dict) else None
        if layer not in (1, 2, 3, 4, 5):
            raise PanelError(
                f"Panel '{name}': mirrored module '{target}' has no valid layer"
            )
        return f"L{layer}"
    raise PanelError(
        f"Panel '{name}': mirrors '{mirrors}' is not 'layer:L1..L5' or 'module:<name>'"
    )


def _validate_data(data: Any, name: str) -> tuple[str | None, int]:
    """Validate the data binding block; return (source, poll_seconds)."""
    if not isinstance(data, dict) or "source" not in data or "poll" not in data:
        raise PanelError(
            f"Panel '{name}': data must be a mapping with 'source' and 'poll'"
        )
    source = data["source"]
    if source is not None and not isinstance(source, str):
        raise PanelError(f"Panel '{name}': data.source must be a URL string or null")
    poll = data["poll"]
    if isinstance(poll, bool) or not isinstance(poll, int):
        raise PanelError(f"Panel '{name}': data.poll '{poll}' is not an integer")
    if source is not None and poll < 1:
        raise PanelError(
            f"Panel '{name}': data.poll must be >= 1 second when a source is set"
        )
    return source, poll


def _import_render(
    folder: Path, entrypoint_spec: str, name: str
) -> Callable[[PanelCtx], str]:
    """Import the render entrypoint ("<file>.py:<callable>") via importlib."""
    if ":" not in entrypoint_spec:
        raise PanelError(
            f"Panel '{name}': entrypoint '{entrypoint_spec}' is not '<file>.py:<callable>'"
        )
    file_name, callable_name = entrypoint_spec.split(":", 1)
    file_path = folder / file_name
    if not file_path.is_file():
        raise PanelError(f"Panel '{name}': entrypoint file '{file_name}' not found")
    # Unique module key per load — fixture reloads stay isolated.
    spec = importlib.util.spec_from_file_location(
        f"omniview_panel_{name.replace('-', '_')}_{new_ulid()}", file_path
    )
    if spec is None or spec.loader is None:
        raise PanelError(f"Panel '{name}': cannot import '{file_name}'")
    py_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(py_module)
    render = getattr(py_module, callable_name, None)
    if not callable(render):
        raise PanelError(
            f"Panel '{name}': entrypoint callable '{callable_name}' "
            f"not found in '{file_name}'"
        )
    return render
