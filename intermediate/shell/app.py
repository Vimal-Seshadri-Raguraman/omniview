"""Console app — renders the five stacked bands (L5 down to L1).

build_app() takes the loaded panels and returns a FastAPI app serving the
console page, per-panel fragments, and a health check. A panel whose
render() raises is contained to an error card — the console never 500s
because one panel is broken (observability over fragility).
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from intermediate.shell.registry import LoadedPanel

_LOG = logging.getLogger("omniview.intermediate.app")

LAYERS: tuple[tuple[str, str], ...] = (
    ("L5", "Presentation"),
    ("L4", "Services"),
    ("L3", "Ontology"),
    ("L2", "Processing"),
    ("L1", "Ingestion"),
)
EMPTY_COPY: dict[str, str] = {
    "L5": "no panels docked — frontend engine (globe, tabs) arrives Phase 2",
    "L4": "no panels docked — api-gateway view docks here",
    "L3": "no panels docked — entity browser & history",
    "L2": "no panels docked — classifier & transform activity",
    "L1": "no panels docked — feed health lands with module zero",
}
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
ERROR_CARD = '<p class="panel-error">panel error — see shell log</p>'


def _render_fragment(panel: LoadedPanel) -> Markup:
    """Render one panel's fragment; contain any exception to an error card."""
    try:
        return Markup(panel.render(panel.ctx))
    except Exception:
        _LOG.exception("Panel '%s' raised during render", panel.name)
        return Markup(ERROR_CARD)


def build_app(panels: list[LoadedPanel]) -> FastAPI:
    """Build the console FastAPI app over the given loaded panels.

    Args:
        panels: Panels from discover_panels(), any order.

    Returns:
        FastAPI app with routes: GET / (console), GET /panel/{name}
        (single fragment; 404 for unknown names), GET /healthz.
    """
    app = FastAPI(title="OmniView Intermediate Console")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    by_name = {panel.name: panel for panel in panels}
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("console.html")

    @app.get("/", response_class=HTMLResponse)
    async def console() -> str:
        """The console page: five bands, L5 down to L1."""
        bands = [
            {
                "num": num,
                "name": name,
                "empty_copy": EMPTY_COPY[num],
                "panels": [
                    {"panel": panel, "fragment": _render_fragment(panel)}
                    for panel in panels
                    if panel.band == num
                ],
            }
            for num, name in LAYERS
        ]
        return template.render(bands=bands, panel_count=len(panels))

    @app.get("/panel/{name}", response_class=HTMLResponse)
    async def panel_fragment(name: str) -> str:
        """One panel's fragment (the future htmx poll target)."""
        if name not in by_name:
            raise HTTPException(status_code=404, detail=f"Panel '{name}' is not docked")
        return str(_render_fragment(by_name[name]))

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness: the shell is up and knows its panel count."""
        return {"status": "ok", "panels": len(panels)}

    return app
