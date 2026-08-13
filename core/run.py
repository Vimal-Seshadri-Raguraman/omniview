"""Engine ignition: python -m core.run [--modules-dir modules] [--db PATH] [--once].

Builds the store, bus, and registry; loads and wires every module; then
either fires each schedule-module once and exits (--once) or runs until
SIGINT. The DB path defaults to the OMNIVIEW_DB environment variable,
falling back to "omniview.db".
"""

import argparse
import logging
import os
import signal
import sys
import threading
from typing import Any

from core.bus import Bus
from core.ontology.store import OntologyStore
from core.registry import Registry

DEFAULT_MODULES_DIR = "modules"
DB_ENV_VAR = "OMNIVIEW_DB"
DEFAULT_DB = "omniview.db"

_LOG = logging.getLogger("omniview.run")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="core.run", description="Boot the OmniView core."
    )
    parser.add_argument(
        "--modules-dir",
        default=DEFAULT_MODULES_DIR,
        help=f"Directory of module folders (default: {DEFAULT_MODULES_DIR})",
    )
    parser.add_argument(
        "--db",
        default=os.getenv(DB_ENV_VAR, DEFAULT_DB),
        help=f"SQLite path (default: ${DB_ENV_VAR} or {DEFAULT_DB})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fire every schedule-module once, then exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Boot the engine.

    Returns:
        Process exit code (0 on clean run).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    args = _parse_args(argv)
    store = OntologyStore(args.db)
    bus = Bus()
    registry = Registry(args.modules_dir, store, bus)
    loaded = registry.load()
    _LOG.info("Loaded %d module(s): %s", len(loaded), [m.name for m in loaded])
    registry.wire()
    try:
        if args.once:
            for module in loaded:
                if module.trigger_style in ("schedule", "continuous"):
                    _LOG.info("Firing '%s' once", module.name)
                    registry.run_once(module.name)
            return 0
        _run_until_sigint()
        return 0
    finally:
        registry.shutdown()


def _run_until_sigint() -> None:
    """Block until SIGINT/SIGTERM."""
    stop = threading.Event()

    def _handle(signum: int, frame: Any) -> None:
        _LOG.info("Signal %d received; shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    stop.wait()


if __name__ == "__main__":
    sys.exit(main())
