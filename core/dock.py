"""Module lifecycle CLI — onboard (new) and offboard (retire).

Lifecycle: scaffolded -> validated -> docked -> live -> retired.

`new` scaffolds a module folder (manifest, module skeleton, test skeleton)
and validates it through the real core.registry path — dock-readiness is
proven, not assumed. `retire` (Task 5) undocks a module by moving its
folder to modules/.retired/ — the append-only ledger is never touched:
offboarding is undocking, not erasure.
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Callable

import yaml

from core.bus import Bus
from core.ontology.store import OntologyStore
from core.registry import KIND_LEGAL_LAYERS, ManifestError, Registry

DEFAULT_MODULES_DIR = "modules"
DEFAULT_CONNECTOR_SCHEDULE = "2h"
_VALIDATION_DB = ":memory:"

_CONNECTOR_TEMPLATE = '''"""{name} — L1 connector (scaffolded by core.dock; edit me)."""

from typing import Any, Callable
from urllib.request import urlopen

URL = "https://example.invalid/feed"  # TODO(owner): real source URL
TIMEOUT_S = 30
SOURCE = "{name}"
PARSE_HINT = "{name}-raw"


def _default_fetch(url: str) -> bytes:
    """Fetch raw bytes from the source (replaced by a stub in tests)."""
    with urlopen(url, timeout=TIMEOUT_S) as response:
        return bytes(response.read())


def run(ctx: Any, _fetch: Callable[[str], bytes] | None = None) -> None:
    """Fetch and land one batch of raw bytes."""
    fetch = _fetch or _default_fetch
    payload = fetch(URL)
    raw_id, is_new = ctx.raw.land(SOURCE, payload, parse_hint=PARSE_HINT)
    ctx.log.info("landed raw_id=%s is_new=%s", raw_id, is_new)
'''

_EVENT_TEMPLATE = '''"""{name} — L2 {kind} (scaffolded by core.dock; edit me)."""

from typing import Any


def run(ctx: Any, topic: str, payload: dict) -> None:
    """React to '{event}': read the landed record, propose observations."""
    ctx.log.info("{name} received %s (raw_id=%s)", topic, payload.get("raw_id"))
    # TODO(owner): parse ctx.raw.get_payload(payload["raw_id"]) and
    # ctx.ontology.propose(...) — see docs/manifest-spec.md worked examples.
'''

_VIEW_TEMPLATE = '''"""{name} — L{layer} view (scaffolded by core.dock; edit me)."""

from typing import Any


def run(ctx: Any) -> None:
    """Serve a view surface. Reads via ctx; writes only via propose."""
    ctx.log.info("{name} fired")
'''

_TEST_TEMPLATE = '''"""Tests for {name} (scaffolded by core.dock; make these real)."""

import pytest


@pytest.mark.skip(reason="scaffold: write the happy-path test before docking for real")
def test_{snake}_happy_path() -> None:
    """run(ctx) with a stubbed fetcher/payload produces the declared output."""


@pytest.mark.skip(reason="scaffold: write the dedup/idempotence test")
def test_{snake}_idempotent() -> None:
    """A second identical run produces nothing new and emits nothing."""


@pytest.mark.skip(reason="scaffold: write the error-propagation test")
def test_{snake}_error_propagates() -> None:
    """A failing dependency surfaces with context in the message."""
'''


class DockError(Exception):
    """A dock operation was refused; the message names the violated rule."""


def _build_manifest(
    name: str, kind: str, layer: int, trigger: dict[str, str]
) -> dict[str, object]:
    """Assemble the manifest mapping for a scaffolded module."""
    produces: list[str] = [f"RawRecord:{name}"] if kind == "connector" else []
    scopes: list[str] = [f"raw:write:{name}"] if kind == "connector" else []
    return {
        "name": name,
        "kind": kind,
        "layer": layer,
        "description": f"Scaffolded by core.dock on {date.today():%Y-%m-%d}; edit me.",
        "produces": produces,
        "consumes": [],
        "triggers": dict(trigger),
        "scopes": scopes,
        "runtime": {
            "entrypoint": "module.py:run",
            "agent": False,
            "secrets": [],
            "budget": None,
        },
    }


def _module_source(name: str, kind: str, layer: int, trigger: dict[str, str]) -> str:
    """Pick and fill the module.py skeleton for this kind."""
    if kind == "connector":
        return _CONNECTOR_TEMPLATE.format(name=name)
    if kind in ("transform", "analytic"):
        event = trigger.get("event", "raw.landed:<source>")
        return _EVENT_TEMPLATE.format(name=name, kind=kind, event=event)
    return _VIEW_TEMPLATE.format(name=name, layer=layer)


def scaffold_module(
    name: str,
    kind: str,
    layer: int,
    trigger: dict[str, str],
    modules_dir: Path,
    dry_run: bool,
) -> list[Path]:
    """Scaffold modules_dir/<name> and validate it via the real registry.

    Args:
        name: Module name (kebab-case; becomes the folder name).
        kind: connector | transform | analytic | view.
        layer: Must be legal for the kind (registry rule 2).
        trigger: {"schedule": "<n>s|m|h"} or {"event": "<pattern>"}.
        modules_dir: The modules directory to scaffold into.
        dry_run: When True, report paths without writing anything.

    Returns:
        The written (or would-be-written) file paths.

    Raises:
        DockError: On refusal — folder exists, illegal kind/layer, or the
            scaffold itself fails registry validation.
    """
    if kind not in KIND_LEGAL_LAYERS:
        raise DockError(f"Unknown kind '{kind}' (legal: {sorted(KIND_LEGAL_LAYERS)})")
    if layer not in KIND_LEGAL_LAYERS[kind]:
        raise DockError(
            f"Kind '{kind}' is illegal in layer {layer} "
            f"(legal layers: {sorted(KIND_LEGAL_LAYERS[kind])})"
        )
    folder = modules_dir / name
    if folder.exists():
        raise DockError(f"Module folder '{folder}' already exists — will not overwrite")
    snake = name.replace("-", "_")
    files: dict[Path, str] = {
        folder
        / "manifest.yaml": yaml.safe_dump(
            _build_manifest(name, kind, layer, trigger), sort_keys=False
        ),
        folder / "module.py": _module_source(name, kind, layer, trigger),
        modules_dir.parent
        / "tests"
        / f"test_{snake}.py": _TEST_TEMPLATE.format(name=name, snake=snake),
    }
    if dry_run:
        return list(files)
    folder.mkdir(parents=True)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    try:
        Registry(str(modules_dir), OntologyStore(_VALIDATION_DB), Bus()).load()
    except ManifestError as exc:
        raise DockError(f"Scaffold failed registry validation: {exc}") from exc
    return list(files)


def retire_module(
    name: str,
    modules_dir: Path,
    assume_yes: bool,
    confirm: Callable[[str], str] = input,
) -> Path:
    """Undock a module: move its folder to modules_dir/.retired/<name>-<date>.

    The append-only ledger is never touched — the retired module's raw
    records, observations, and provenance remain forever. Re-dock by
    moving the folder back.

    Args:
        name: The docked module's name.
        modules_dir: The modules directory it lives in.
        assume_yes: Skip the interactive confirmation.
        confirm: Injected prompt function (tests stub this).

    Returns:
        The retirement destination path.

    Raises:
        DockError: Unknown module, destination collision, or declined
            confirmation.
    """
    folder = modules_dir / name
    if not folder.is_dir():
        raise DockError(f"No module folder '{folder}' found — nothing to retire")
    destination = modules_dir / ".retired" / f"{name}-{date.today():%Y%m%d}"
    if destination.exists():
        raise DockError(
            f"Retirement destination '{destination}' already exists — "
            "move or rename it first"
        )
    if not assume_yes:
        answer = confirm(
            f"Retire '{name}'? Its folder moves to {destination}; the ledger "
            "keeps every record it ever produced. [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            raise DockError(f"Retirement of '{name}' declined")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(destination))
    return destination


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns 0 on success, 2 on refusal."""
    parser = argparse.ArgumentParser(
        prog="core.dock", description="Onboard/offboard OmniView modules."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="Scaffold and validate a new module")
    new.add_argument("name")
    new.add_argument("--kind", required=True, choices=sorted(KIND_LEGAL_LAYERS))
    new.add_argument("--layer", required=True, type=int)
    trigger_group = new.add_mutually_exclusive_group()
    trigger_group.add_argument("--schedule", help='e.g. "2h" or "continuous"')
    trigger_group.add_argument("--event", help='e.g. "raw.landed:<source>"')
    new.add_argument("--modules-dir", default=DEFAULT_MODULES_DIR)
    new.add_argument("--dry-run", action="store_true")
    retire = sub.add_parser("retire", help="Undock a module (ledger untouched)")
    retire.add_argument("name")
    retire.add_argument("--modules-dir", default=DEFAULT_MODULES_DIR)
    retire.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "retire":
            destination = retire_module(
                name=args.name,
                modules_dir=Path(args.modules_dir),
                assume_yes=args.yes,
            )
            print(f"Retired '{args.name}' -> {destination}")
            print("The ledger keeps every record it produced; re-dock by moving back.")
            return 0
        trigger: dict[str, str]
        if args.event:
            trigger = {"event": args.event}
        else:
            trigger = {"schedule": args.schedule or DEFAULT_CONNECTOR_SCHEDULE}
        written = scaffold_module(
            name=args.name,
            kind=args.kind,
            layer=args.layer,
            trigger=trigger,
            modules_dir=Path(args.modules_dir),
            dry_run=args.dry_run,
        )
    except DockError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    verb = "Would write" if args.dry_run else "Wrote + validated"
    print(f"{verb} {len(written)} file(s) for '{args.name}':")
    for path in written:
        print(f"  {path}")
    if not args.dry_run:
        print("Docked: the registry loads this module. Make the skipped tests real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
