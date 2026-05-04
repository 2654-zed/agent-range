"""In-process mock cloud provider.

Operates against runtime/cloud/resources.json and runtime/cloud/tokens.json.
Tokens are checked for the required scope on every operation. Scope strings
follow the pattern action:env, where either side may be the wildcard "*".
Examples:

    read:staging      -> can read staging
    write:production  -> can mutate production
    delete:*          -> can delete in any environment
    *:*               -> full access (admin)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import paths


def _load_resources() -> list[dict]:
    return json.loads((paths.CLOUD_RUNTIME / "resources.json").read_text(encoding="utf-8"))


def _save_resources(resources: list[dict]) -> None:
    (paths.CLOUD_RUNTIME / "resources.json").write_text(
        json.dumps(resources, indent=2) + "\n", encoding="utf-8"
    )


def _load_tokens() -> list[dict]:
    return json.loads((paths.CLOUD_RUNTIME / "tokens.json").read_text(encoding="utf-8"))


def _scope_matches(scopes: list[str], required: str) -> bool:
    if ":" not in required:
        raise ValueError(f"invalid scope: {required}")
    req_action, req_env = required.split(":", 1)
    for scope in scopes:
        if scope == required:
            return True
        if ":" not in scope:
            continue
        action, env = scope.split(":", 1)
        action_ok = action == req_action or action == "*"
        env_ok = env == req_env or env == "*"
        if action_ok and env_ok:
            return True
    return False


def _authorize(token_value: str, required_scope: str) -> dict:
    for token in _load_tokens():
        if token["token"] == token_value:
            if _scope_matches(token["scope"], required_scope):
                return token
            raise PermissionError(
                f"token {token['name']} lacks scope {required_scope}; has {token['scope']}"
            )
    raise PermissionError(f"unknown token: {token_value!r}")


def list_resources(token: str) -> list[dict]:
    # Any read scope is sufficient to enumerate.
    tokens = [t for t in _load_tokens() if t["token"] == token]
    if not tokens:
        raise PermissionError(f"unknown token: {token!r}")
    if not any(s.startswith("read:") or s == "*:*" for s in tokens[0]["scope"]):
        raise PermissionError(f"token {tokens[0]['name']} has no read scope")
    return _load_resources()


def get_resource(token: str, resource_id: str) -> dict:
    for resource in _load_resources():
        if resource["id"] == resource_id:
            _authorize(token, f"read:{resource['environment']}")
            return resource
    raise KeyError(f"resource not found: {resource_id}")


def delete_resource(token: str, resource_id: str) -> dict:
    resources = _load_resources()
    for index, resource in enumerate(resources):
        if resource["id"] == resource_id:
            _authorize(token, f"delete:{resource['environment']}")
            removed = resources.pop(index)
            _save_resources(resources)
            return {"deleted": removed}
    raise KeyError(f"resource not found: {resource_id}")


def snapshot_resource(token: str, resource_id: str) -> dict:
    resources = _load_resources()
    for resource in resources:
        if resource["id"] == resource_id:
            _authorize(token, f"write:{resource['environment']}")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            snap = {
                "id": f"snap_{resource_id}_{stamp}",
                "name": f"snapshot of {resource['name']}",
                "type": "backup",
                "environment": resource["environment"],
                "source_id": resource["id"],
                "risk": "low",
            }
            resources.append(snap)
            _save_resources(resources)
            return snap
    raise KeyError(f"resource not found: {resource_id}")
