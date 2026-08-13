"""In-process pub/sub bus — the routing fabric (stdlib only).

Topics are dot/colon strings ("raw.landed:celestrak", "observation.appended");
subscription patterns use fnmatch wildcards ("raw.landed:*", "observation.*").
Fan-out is synchronous and in subscription order; a raising handler is logged
and never blocks later handlers or the publisher. Payloads are carried
verbatim (no copying, no serialization).
"""

import fnmatch
import logging
import threading
from typing import Callable

Handler = Callable[[str, dict], None]

_LOGGER_NAME = "omniview.bus"


class Bus:
    """Synchronous in-process publish/subscribe."""

    def __init__(self) -> None:
        """Create an empty bus."""
        self._subscriptions: list[tuple[str, Handler]] = []
        self._lock = threading.Lock()
        self._log = logging.getLogger(_LOGGER_NAME)

    def subscribe(self, pattern: str, handler: Handler) -> None:
        """Register a handler for topics matching an fnmatch pattern.

        Args:
            pattern: Topic pattern, e.g. "raw.landed:*" or an exact topic.
            handler: Called as handler(topic, payload) on each match.
        """
        with self._lock:
            self._subscriptions.append((pattern, handler))

    def publish(self, topic: str, payload: dict) -> None:
        """Deliver a payload to every matching subscriber, in order.

        Handler exceptions are logged to "omniview.bus" and contained —
        they never propagate to the publisher or block later handlers.

        Args:
            topic: The concrete topic string.
            payload: Carried verbatim to each handler.
        """
        with self._lock:
            matching = [
                h for (p, h) in self._subscriptions if fnmatch.fnmatchcase(topic, p)
            ]
        for handler in matching:
            try:
                handler(topic, payload)
            except Exception:
                self._log.exception("Handler %r failed for topic '%s'", handler, topic)
