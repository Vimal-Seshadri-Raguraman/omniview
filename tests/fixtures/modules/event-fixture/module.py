"""Event-triggered fixture module: records topic and payload of each delivery."""

from typing import Any

CALLS: list[tuple[str, dict]] = []


def run(ctx: Any, topic: str, payload: dict) -> None:
    """Record the delivered event."""
    CALLS.append((topic, payload))
    ctx.log.info("event-fixture received %s", topic)
