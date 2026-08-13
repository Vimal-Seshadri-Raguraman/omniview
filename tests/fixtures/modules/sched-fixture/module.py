"""Schedule-triggered fixture module: records each invocation."""

from typing import Any

CALLS: list[str] = []


def run(ctx: Any) -> None:
    """Record that the schedule trigger fired."""
    CALLS.append("ran")
    ctx.log.info("sched-fixture ran")
