"""Console app tests — band stacking, fragments, error containment."""

from pathlib import Path

from fastapi.testclient import TestClient

from intermediate.shell.app import build_app
from intermediate.shell.registry import LoadedPanel, PanelCtx, discover_panels

FIXTURE_PANELS = Path("tests/fixtures/panels")
FIXTURE_MODULES = Path("tests/fixtures/modules")


def make_client(panels: list[LoadedPanel] | None = None) -> TestClient:
    """A TestClient over the console app; defaults to the fixture panels."""
    if panels is None:
        panels = discover_panels(FIXTURE_PANELS, FIXTURE_MODULES)
    return TestClient(build_app(panels))


def broken_panel() -> LoadedPanel:
    """A panel whose render raises — the console must contain it."""

    def render(ctx: PanelCtx) -> str:
        raise RuntimeError("panel exploded")

    import logging

    ctx = PanelCtx(
        name="broken-panel",
        mirrors="layer:L2",
        data_source=None,
        log=logging.getLogger("test.broken"),
    )
    return LoadedPanel(
        name="broken-panel",
        mirrors="layer:L2",
        band="L2",
        order=10,
        description="raises on render",
        data_source=None,
        poll_seconds=5,
        render=render,
        ctx=ctx,
    )


def test_console_stacks_bands_l5_down_to_l1() -> None:
    html = make_client().get("/").text
    positions = [html.index(f"L{n}") for n in (5, 4, 3, 2, 1)]
    assert positions == sorted(positions)


def test_empty_bands_render_dimmed_copy() -> None:
    html = make_client(panels=[]).get("/").text
    assert html.count("no panels docked") == 5


def test_docked_panel_fragment_in_band_chrome() -> None:
    html = make_client().get("/").text
    assert "hello from fixture-panel" in html
    assert "mirrors: layer:L1" in html


def test_panel_fragment_route() -> None:
    client = make_client()
    assert "hello from fixture-panel" in client.get("/panel/fixture-panel").text
    assert client.get("/panel/no-such-panel").status_code == 404


def test_raising_panel_contained_not_500() -> None:
    response = make_client(panels=[broken_panel()]).get("/")
    assert response.status_code == 200
    assert "panel error" in response.text


def test_healthz_counts_panels() -> None:
    body = make_client().get("/healthz").json()
    assert body == {"status": "ok", "panels": 1}


def test_settings_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from intermediate.shell.__main__ import load_settings

    for var in ("OV_SHELL_HOST", "OV_SHELL_PORT", "OV_PANELS_DIR", "OV_MODULES_DIR"):
        monkeypatch.delenv(var, raising=False)
    settings = load_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8080
    assert settings.panels_dir == Path("intermediate/panels")
    assert settings.modules_dir == Path("modules")


def test_settings_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from intermediate.shell.__main__ import load_settings

    monkeypatch.setenv("OV_SHELL_PORT", "9999")
    monkeypatch.setenv("OV_PANELS_DIR", "/tmp/panels")
    assert load_settings().port == 9999
    assert load_settings().panels_dir == Path("/tmp/panels")
