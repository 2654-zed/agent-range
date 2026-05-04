"""Append-only JSONL event log for tool calls.

Events live inside runtime/ and are wiped on reset, representing the
boundary between sandbox sessions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import paths


def _log_path():
    return paths.RUNTIME / "events.jsonl"


def emit(event: dict) -> None:
    paths.RUNTIME.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=_json_default) + "\n")


def read_all() -> list[dict]:
    log = _log_path()
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def clear() -> None:
    log = _log_path()
    if log.exists():
        log.unlink()


def _json_default(value: Any):
    return repr(value)
