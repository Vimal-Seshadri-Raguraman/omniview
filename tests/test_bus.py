"""Tests for core.bus — in-process pub/sub (plan Task 4, OV-13)."""

import logging

import pytest

from core.bus import Bus


@pytest.fixture()
def bus() -> Bus:
    """A fresh bus."""
    return Bus()


def test_exact_match_delivers(bus: Bus) -> None:
    received: list[tuple[str, dict]] = []
    bus.subscribe("raw.landed:celestrak", lambda t, p: received.append((t, p)))
    bus.publish("raw.landed:celestrak", {"raw_id": "abc"})
    assert received == [("raw.landed:celestrak", {"raw_id": "abc"})]


def test_wildcard_delivers(bus: Bus) -> None:
    received: list[str] = []
    bus.subscribe("raw.landed:*", lambda t, p: received.append(t))
    bus.subscribe("observation.*", lambda t, p: received.append(t))
    bus.publish("raw.landed:celestrak", {})
    bus.publish("observation.appended", {})
    assert received == ["raw.landed:celestrak", "observation.appended"]


def test_non_match_does_not_deliver(bus: Bus) -> None:
    received: list[str] = []
    bus.subscribe("raw.landed:celestrak", lambda t, p: received.append(t))
    bus.publish("raw.landed:aisstream", {})
    bus.publish("observation.appended", {})
    assert received == []


def test_multiple_subscribers_fire_in_subscription_order(bus: Bus) -> None:
    calls: list[str] = []
    bus.subscribe("topic", lambda t, p: calls.append("first"))
    bus.subscribe("topic", lambda t, p: calls.append("second"))
    bus.subscribe("*", lambda t, p: calls.append("third"))
    bus.publish("topic", {})
    assert calls == ["first", "second", "third"]


def test_payload_carried_verbatim(bus: Bus) -> None:
    payload = {"nested": {"deep": [1, 2, 3]}, "flag": True}
    received: list[dict] = []
    bus.subscribe("topic", lambda t, p: received.append(p))
    bus.publish("topic", payload)
    assert received[0] is payload


def test_raising_handler_is_logged_and_contained(
    bus: Bus, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def bad_handler(topic: str, payload: dict) -> None:
        raise RuntimeError("handler exploded")

    bus.subscribe("topic", bad_handler)
    bus.subscribe("topic", lambda t, p: calls.append("survivor"))
    with caplog.at_level(logging.ERROR, logger="omniview.bus"):
        bus.publish("topic", {})  # must not raise
    assert calls == ["survivor"]
    assert any(
        "handler exploded" in r.message or "topic" in r.message for r in caplog.records
    )
