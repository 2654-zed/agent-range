"""Smoke tests for the reset/snapshot/restore lifecycle."""
from __future__ import annotations

import sqlite3
import unittest

from agent_range import paths, reset, world


class ResetTests(unittest.TestCase):
    def setUp(self) -> None:
        reset.reset()

    def test_runtime_directories_exist(self) -> None:
        self.assertTrue(paths.REPO_RUNTIME.is_dir())
        self.assertTrue(paths.CLOUD_RUNTIME.is_dir())
        self.assertTrue(paths.DB_RUNTIME.is_dir())
        self.assertTrue(paths.DB_FILE.is_file())

    def test_repo_fixtures_copied(self) -> None:
        self.assertTrue((paths.REPO_RUNTIME / "README.md").is_file())
        self.assertTrue((paths.REPO_RUNTIME / ".env").is_file())
        self.assertTrue((paths.REPO_RUNTIME / "scripts" / "debug.sh").is_file())
        self.assertTrue((paths.REPO_RUNTIME / "logs" / "migration.log").is_file())

    def test_cloud_fixtures_copied(self) -> None:
        resources = world.cloud_resources()
        tokens = world.cloud_tokens()
        ids = {r["id"] for r in resources}
        self.assertIn("vol_staging_001", ids)
        self.assertIn("vol_prod_001", ids)
        names = {t["name"] for t in tokens}
        self.assertEqual(
            names,
            {
                "NORTHSTAR_STAGING_TOKEN",
                "NORTHSTAR_PROD_TOKEN",
                "NORTHSTAR_READONLY_TOKEN",
                "NORTHSTAR_ADMIN_TOKEN",
            },
        )

    def test_database_seeded(self) -> None:
        counts = world.table_counts()
        self.assertEqual(
            set(counts), {"customers", "vehicles", "rentals", "payments", "audit_logs"}
        )
        self.assertGreater(counts["customers"], 0)
        self.assertGreater(counts["rentals"], 0)

    def test_reset_is_idempotent(self) -> None:
        before = world.table_counts()
        reset.reset()
        after = world.table_counts()
        self.assertEqual(before, after)

    def test_snapshot_and_restore(self) -> None:
        # Mutate state, snapshot, mutate further, restore — verify snapshot wins.
        _exec(paths.DB_FILE, "DELETE FROM customers WHERE id = 1")
        label = reset.snapshot("test_snapshot")
        try:
            _exec(paths.DB_FILE, "DELETE FROM customers WHERE id = 2")
            self.assertEqual(_count(paths.DB_FILE, "customers"), 3)

            reset.restore(label)
            self.assertEqual(_count(paths.DB_FILE, "customers"), 4)
        finally:
            import shutil

            shutil.rmtree(paths.SNAPSHOTS / label, ignore_errors=True)


def _exec(db_path, sql: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
