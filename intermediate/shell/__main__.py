"""Shell ignition: python -m intermediate.shell.

Reads env config, discovers panels (refusing invalid ones), builds the
console app, and serves it with uvicorn. Env vars: OV_SHELL_HOST
(default 127.0.0.1), OV_SHELL_PORT (8080), OV_PANELS_DIR
(intermediate/panels), OV_MODULES_DIR (modules).
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from intermediate.shell.app import build_app
from intermediate.shell.registry import discover_panels

_LOG = logging.getLogger("omniview.intermediate.shell")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_PANELS_DIR = "intermediate/panels"
DEFAULT_MODULES_DIR = "modules"


@dataclass
class Settings:
    """Shell runtime configuration, resolved from environment variables."""

    host: str
    port: int
    panels_dir: Path
    modules_dir: Path


def load_settings() -> Settings:
    """Resolve shell settings from the environment, with defaults."""
    return Settings(
        host=os.getenv("OV_SHELL_HOST", DEFAULT_HOST),
        port=int(os.getenv("OV_SHELL_PORT", str(DEFAULT_PORT))),
        panels_dir=Path(os.getenv("OV_PANELS_DIR", DEFAULT_PANELS_DIR)),
        modules_dir=Path(os.getenv("OV_MODULES_DIR", DEFAULT_MODULES_DIR)),
    )


def main() -> None:
    """Discover panels, build the console app, and serve it."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    settings = load_settings()
    panels = discover_panels(settings.panels_dir, settings.modules_dir)
    _LOG.info("Docked %d panel(s): %s", len(panels), [panel.name for panel in panels])
    uvicorn.run(build_app(panels), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
