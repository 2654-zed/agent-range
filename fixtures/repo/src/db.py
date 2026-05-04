"""Database utilities (mock)."""
from __future__ import annotations

import sys


def migrate() -> None:
    print("Running migrations...")


def status() -> None:
    print("Database OK")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "migrate":
        migrate()
    else:
        status()
