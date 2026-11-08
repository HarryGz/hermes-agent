"""Idle auto-rotation (gateway/session_idle_reset.py) behavior tests.

Covers: disabled no-op, rotation past the window, freshness/running/command/
internal/platform-filter refusals, and the GatewayConfig parse of the two new
keys (top-level and nested ``gateway.`` form).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any


class _Store:
    def __init__(self, entry):
        self.entry = entry

    def lookup_by_session_key(self, session_key):
        return self.entry


class _Runner:
    def __init__(self, entry=None, *, minutes=60, platforms=None, running=False):
        self.config = SimpleNamespace(
            session_idle_reset_minutes=minutes, session_idle_reset_platforms=platforms
        )
        self.session_store: Any = _Store(entry)
        self._running = running
        self.reset_calls = []

    def _is_session_running(self, session_key):
        return self._running

    async def _handle_reset_command(self, event, *, title_arg=None):
        self.reset_calls.append(title_arg)
        # Rotate the route like the real reset does.
        self.session_store.entry = SimpleNamespace(
            session_id="fresh", updated_at=datetime.now()
        )
        return "banner"


def _event(*, command=None, internal=False, fifo_only=False):
    return SimpleNamespace(
        get_command=lambda: command,
        internal=internal,
        _fifo_only=fifo_only,
    )


def _entry(age_minutes: float, session_id: str = "old"):
    return SimpleNamespace(
        session_id=session_id, updated_at=datetime.now() - timedelta(minutes=age_minutes)
    )


def _source(platform="feishu"):
    return SimpleNamespace(platform=SimpleNamespace(value=platform))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


from gateway.session_idle_reset import maybe_idle_reset, reset_window


def test_disabled_feature_never_rotates():
    runner = _Runner(_entry(999), minutes=0)
    assert run(maybe_idle_reset(runner, _event(), _source(), "k")) is False
    assert runner.reset_calls == []


def test_idle_past_window_rotates_with_pinned_title():
    runner = _Runner(_entry(120), minutes=60)
    assert run(maybe_idle_reset(runner, _event(), _source(), "k")) is True
    assert runner.reset_calls == [""]  # title pinned: message text never becomes the title


def test_fresh_session_not_rotated():
    runner = _Runner(_entry(10), minutes=60)
    assert run(maybe_idle_reset(runner, _event(), _source(), "k")) is False


def test_boundary_exactly_at_window_rotates():
    runner = _Runner(_entry(60.0), minutes=60)
    assert run(maybe_idle_reset(runner, _event(), _source(), "k")) is True


def test_command_message_never_rotates():
    runner = _Runner(_entry(120), minutes=60)
    assert run(maybe_idle_reset(runner, _event(command="new"), _source(), "k")) is False


def test_internal_and_fifo_events_never_rotate():
    runner = _Runner(_entry(120), minutes=60)
    assert run(maybe_idle_reset(runner, _event(internal=True), _source(), "k")) is False
    assert run(maybe_idle_reset(runner, _event(fifo_only=True), _source(), "k")) is False


def test_running_session_never_rotates():
    runner = _Runner(_entry(120), minutes=60, running=True)
    assert run(maybe_idle_reset(runner, _event(), _source(), "k")) is False


def test_platform_filter_blocks_other_platforms():
    runner = _Runner(_entry(120), minutes=60, platforms=["feishu"])
    assert run(maybe_idle_reset(runner, _event(), _source("telegram"), "k")) is False
    assert run(maybe_idle_reset(runner, _event(), _source("feishu"), "k")) is True


def test_missing_route_or_store_is_noop():
    runner = _Runner(None, minutes=60)
    assert run(maybe_idle_reset(runner, _event(), _source(), "k")) is False
    runner2 = _Runner(_entry(120), minutes=60)
    runner2.session_store = SimpleNamespace()  # no lookup
    assert run(maybe_idle_reset(runner2, _event(), _source(), "k")) is False


def test_reset_window_helper():
    assert reset_window(_Runner(None, minutes=0)) is None
    assert reset_window(_Runner(None, minutes=90)) == timedelta(minutes=90)
    assert reset_window(SimpleNamespace(config=None)) is None


def test_gateway_config_parses_new_keys():
    from gateway.config import GatewayConfig

    cfg = GatewayConfig.from_dict({"gateway": {"session_idle_reset_minutes": 60}})
    assert cfg.session_idle_reset_minutes == 60
    assert cfg.session_idle_reset_platforms is None

    cfg2 = GatewayConfig.from_dict(
        {"session_idle_reset_minutes": 30, "session_idle_reset_platforms": ["feishu"]}
    )
    assert cfg2.session_idle_reset_minutes == 30
    assert cfg2.session_idle_reset_platforms == ["feishu"]

    cfg3 = GatewayConfig.from_dict({})
    assert cfg3.session_idle_reset_minutes == 0
    assert cfg3.session_idle_reset_platforms is None

    # to_dict round-trip keeps the keys
    dumped = cfg2.to_dict()
    assert dumped["session_idle_reset_minutes"] == 30
    assert dumped["session_idle_reset_platforms"] == ["feishu"]
