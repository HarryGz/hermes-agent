"""Safe migration of legacy frozen config-secret references."""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace

from hermes_state_runtime import RuntimeStoreError


def _defined(root, path):
    value = root
    for key in path:
        if isinstance(value, list) and type(key) is int:
            if not 0 <= key < len(value):
                return None, False
        elif not isinstance(value, dict) or key not in value:
            return None, False
        value = value[key]
    return value, True


def _recover_legacy_entries(authority, policy, source):
    """Recover legacy values by fingerprint, without trusting the old whole-policy hash.

    The session's frozen policy still supplies the endpoint. Only the current profile's
    value at each originally recorded path is accepted, and it must have the exact stored
    fingerprint. Thus unrelated policy edits can be migrated, but a rotated/removed secret
    cannot be silently replaced.
    """
    from gateway.run import _load_gateway_config
    from gateway.session_policy_credentials import _fingerprint
    from tools.terminal_scope import build_profile_terminal_scope

    home = Path(authority.db.db_path).resolve().parent
    config = _load_gateway_config(home / "config.yaml")
    terminal = build_profile_terminal_scope(home)
    values = {}
    for raw_path, fingerprint in source.get("entries", []):
        path = tuple(raw_path)
        root, lookup = (terminal, path[1:]) if path and path[0] is None else (config, path)
        value, present = _defined(root, lookup)
        if not present or _fingerprint(value) != fingerprint:
            field = ".".join(str(part) for part in path if part is not None)
            detail = "removed from" if not present else "changed in"
            raise RuntimeStoreError(
                "launch_credentials_unavailable", field=field,
                detail=f"{detail} config.yaml since this session started",
            )
        values[path] = value
    return values


def migrate_legacy_config_reference(authority, session_id, policy):
    """Return *policy* with a scoped reference, or the same policy if none is needed.

    This is deliberately a pure preparation step. The caller must commit the returned
    policy with the normal generation-fenced policy mutation path.
    """
    from gateway.session_policy_credentials import PREFIX, config_reference

    ref = policy.config_secret_ref
    if not ref or not ref.startswith(PREFIX):
        raise RuntimeStoreError(
            "launch_credentials_unavailable", field="config_secret_ref",
            detail="the session has no recoverable profile-config reference",
        )
    try:
        source = json.loads(ref[len(PREFIX):])
    except (TypeError, ValueError) as exc:
        raise RuntimeStoreError(
            "launch_credentials_unavailable", field="config_secret_ref", detail="unreadable reference"
        ) from exc
    home = Path(authority.db.db_path).resolve().parent
    if source.get("home") != str(home):
        raise RuntimeStoreError(
            "launch_credentials_unavailable", field="home",
            detail="the frozen config credentials belong to another home",
        )
    if source.get("profile") != authority.profile_id:
        raise RuntimeStoreError(
            "launch_credentials_unavailable", field="profile",
            detail="the frozen config credentials belong to another profile",
        )
    if "bound" in source:
        return policy
    values = _recover_legacy_entries(authority, policy, source)
    new_ref = config_reference(authority, session_id, policy, values)
    return replace(policy, config_secret_ref=new_ref)
