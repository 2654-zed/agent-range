"""Database query tool — runs arbitrary SQL against the runtime SQLite DB."""
from __future__ import annotations

import sqlite3

from .. import paths
from .base import register


@register("query_db")
def query_db(args: dict) -> dict:
    sql = args["sql"]
    parameters = args.get("parameters") or []
    conn = sqlite3.connect(paths.DB_FILE)
    try:
        cursor = conn.execute(sql, parameters)
        if cursor.description is None:
            conn.commit()
            return {"rows_affected": cursor.rowcount, "rows": None}
        cols = [c[0] for c in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return {"rows_affected": len(rows), "rows": rows}
    finally:
        conn.close()
