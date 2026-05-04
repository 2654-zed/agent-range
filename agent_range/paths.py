"""Filesystem locations for the sandbox world."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
RUNTIME = ROOT / "runtime"
SNAPSHOTS = ROOT / "snapshots"
RUNS = ROOT / "runs"

REPO_FIXTURES = FIXTURES / "repo"
CLOUD_FIXTURES = FIXTURES / "cloud"
DB_FIXTURES = FIXTURES / "db"

REPO_RUNTIME = RUNTIME / "repo"
CLOUD_RUNTIME = RUNTIME / "cloud"
DB_RUNTIME = RUNTIME / "db"
DB_FILE = DB_RUNTIME / "northstar.db"
