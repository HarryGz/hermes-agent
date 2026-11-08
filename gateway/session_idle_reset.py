"""Idle auto-rotation for gateway chat routes (local customization).

A route whose ``SessionEntry`` has been silent longer than
``gateway.session_idle_reset_minutes`` starts a FRESH session on its next
plain (non-command, non-internal) message: the same clean-break sequence the
``/new`` command runs, minus the banner reply. Commands never rotate (their
own handler owns the route), internal events never rotate, a busy session
never reaches this module (the caller already checked), and any failure is
fail-open: the message proceeds on the existing session.

``SessionEntry.updated_at`` is the activity clock: user turns advance it
(``touch_activity=True``), internal/system turns do not, so background work
does not keep an abandoned chat alive.
"""

from __future__ import annotations

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)


def reset_window(runner) -> timedelta | None:
    """Configured idle window as a timedelta; None when the feature is off."""
    try:
        minutes = int(getattr(getattr(runner, "config", None), "session_idle_reset_minutes", 0) or 0)
    except (TypeError, ValueError):
        minutes = 0
    return timedelta(minutes=minutes) if minutes > 0 else None


def _platform_allowed(runner, source) -> bool:
    allowed = getattr(getattr(runner, "config", None), "session_idle_reset_platforms", None)
    if not allowed:
        return True
    platform = getattr(source, "platform", None)
    value = getattr(platform, "value", None) or (str(platform) if platform is not None else "")
    return value in allowed


async def maybe_idle_reset(runner, event, source, session_key: str) -> bool:
    """Rotate an idle route to a fresh session before this message runs.

    Returns True when the route was rotated (the caller proceeds normally;
    the next route resolution picks up the fresh session id).
    """
    window = reset_window(runner)
    if window is None or not session_key:
        return False
    if getattr(event, "internal", False) or getattr(event, "_fifo_only", False):
        return False
    try:
        if event.get_command():  # a command's own handler owns the route — never rotate under it
            return False
    except Exception:
        return False
    if not _platform_allowed(runner, source):
        return False
    is_running = getattr(runner, "_is_session_running", None)
    if is_running is not None and is_running(session_key):
        return False
    store = getattr(runner, "session_store", None)
    lookup = getattr(store, "lookup_by_session_key", None)
    if lookup is None:
        return False
    entry = lookup(session_key)
    updated_at = getattr(entry, "updated_at", None)
    if entry is None or updated_at is None:
        return False
    try:
        from gateway.session_lifecycle import _now
        idle_for = _now() - updated_at
    except (TypeError, ImportError):  # tz mismatch or moved helper: fail open
        return False
    if idle_for < window:
        return False
    old_session_id = getattr(entry, "session_id", "")
    logger.info(
        "Idle auto-rotation: session=%s idle=%ss >= window %ss — starting a fresh session",
        session_key, int(idle_for.total_seconds()), int(window.total_seconds()),
    )
    # The /new sequence without the banner. title_arg="" pins the title so the
    # inbound message text is never adopted as the fresh session's title
    # (get_command_args() on a plain message returns the whole text).
    reset_command = getattr(runner, "_handle_reset_command", None)
    if reset_command is None:
        return False
    await reset_command(event, title_arg="")
    new_session_id = getattr(lookup(session_key), "session_id", "?")
    logger.info("Idle auto-rotation: %s -> %s", old_session_id, new_session_id)
    return new_session_id != old_session_id
