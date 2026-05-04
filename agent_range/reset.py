"""Snapshot and restore the sandbox world state.

The fixtures directory is the canonical source of truth and must never be
mutated by the running sandbox. The runtime directory is a working copy
materialized from fixtures on every reset.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
from datetime import datetime, timezone

from . import paths


def reset() -> None:
    """Wipe runtime and rebuild it from fixtures."""
    if paths.RUNTIME.exists():
        _rmtree(paths.RUNTIME)
    paths.RUNTIME.mkdir(parents=True)

    shutil.copytree(paths.REPO_FIXTURES, paths.REPO_RUNTIME)
    shutil.copytree(paths.CLOUD_FIXTURES, paths.CLOUD_RUNTIME)

    paths.DB_RUNTIME.mkdir(parents=True, exist_ok=True)
    _materialize_db(paths.DB_FILE)


def snapshot(name: str | None = None) -> str:
    """Archive the current runtime state under snapshots/."""
    if not paths.RUNTIME.exists():
        raise RuntimeError("runtime/ does not exist — nothing to snapshot")
    paths.SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    label = name or datetime.now(timezone.utc).strftime("snapshot_%Y%m%dT%H%M%SZ")
    target = paths.SNAPSHOTS / label
    if target.exists():
        raise RuntimeError(f"snapshot already exists: {target}")
    shutil.copytree(paths.RUNTIME, target)
    return label


def restore(name: str) -> None:
    """Restore a previously archived snapshot into runtime/."""
    source = paths.SNAPSHOTS / name
    if not source.exists():
        raise RuntimeError(f"snapshot not found: {source}")
    if paths.RUNTIME.exists():
        _rmtree(paths.RUNTIME)
    shutil.copytree(source, paths.RUNTIME)


def _rmtree(path) -> None:
    """rmtree that survives Windows read-only files (e.g. .git/objects)."""
    shutil.rmtree(path, onexc=_force_writable_and_retry)


def _force_writable_and_retry(func, path, _exc) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def _materialize_db(db_path) -> None:
    schema_sql = (paths.DB_FIXTURES / "schema.sql").read_text(encoding="utf-8")
    seed = json.loads((paths.DB_FIXTURES / "seed.json").read_text(encoding="utf-8"))

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        for table, rows in seed.items():
            if not rows:
                continue
            cols = list(rows[0].keys())
            placeholders = ",".join("?" for _ in cols)
            cols_sql = ",".join(cols)
            conn.executemany(
                f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})",
                [tuple(r[c] for c in cols) for r in rows],
            )
        conn.commit()
    finally:
        conn.close()
