"""Fixture panel: renders a static fragment naming its panel."""

from typing import Any


def render(ctx: Any) -> str:
    """Return a static HTML fragment."""
    return f'<p class="fixture">hello from {ctx.name}</p>'
