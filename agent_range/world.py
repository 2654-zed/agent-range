"""Read-only inspection of the materialized world state."""
from __future__ import annotations

import json
import sqlite3

from . import paths


def is_initialized() -> bool:
    return paths.RUNTIME.exists() and paths.DB_FILE.exists()


def cloud_resources() -> list[dict]:
    return json.loads((paths.CLOUD_RUNTIME / "resources.json").read_text(encoding="utf-8"))


def cloud_tokens() -> list[dict]:
    return json.loads((paths.CLOUD_RUNTIME / "tokens.json").read_text(encoding="utf-8"))


def db_connect() -> sqlite3.Connection:
    return sqlite3.connect(paths.DB_FILE)


def table_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for (name,) in rows:
            counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    finally:
        conn.close()
    return counts
