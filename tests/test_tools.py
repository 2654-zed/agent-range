"""Smoke tests for the v0.2 instrumented tool layer.

These tests exercise tool *semantics*, not policy. Each TestCase opts
into a permissive policy so a critical-tier call like `DELETE FROM
payments` reaches its handler instead of being blocked by v0.4 gating.
Policy behavior itself is tested in test_policy.py.
"""
from __future__ import annotations

import json
import sqlite3
import unittest

from agent_range import events, paths, policy, reset, tools


def _last_event() -> dict:
    log = events.read_all()
    assert log, "expected at least one event"
    return log[-1]


class _PermissiveBase(unittest.TestCase):
    """Base class that runs each test under Policy.permissive()."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_policy = policy.get_default_policy()
        policy.set_default_policy(policy.Policy.permissive())

    @classmethod
    def tearDownClass(cls) -> None:
        policy.set_default_policy(cls._saved_policy)


class ToolBaseTests(_PermissiveBase):
    def setUp(self) -> None:
        reset.reset()

    def test_unknown_tool_blocked_and_logged(self) -> None:
        result = tools.execute(tools.ToolCall(tool="nope", args={}))
        self.assertFalse(result.ok)
        self.assertEqual(result.decision, "HARD_STOP")
        ev = _last_event()
        self.assertEqual(ev["tool"], "nope")
        self.assertFalse(ev["ok"])
        self.assertEqual(ev["decision"], "HARD_STOP")

    def test_every_call_appends_one_event(self) -> None:
        events.clear()
        tools.execute(tools.ToolCall(tool="list_dir", args={"path": "repo"}))
        tools.execute(tools.ToolCall(tool="list_dir", args={"path": "cloud"}))
        self.assertEqual(len(events.read_all()), 2)


class FilesystemToolTests(_PermissiveBase):
    def setUp(self) -> None:
        reset.reset()

    def test_read_existing_file(self) -> None:
        result = tools.execute(
            tools.ToolCall(tool="read_file", args={"path": "repo/README.md"})
        )
        self.assertTrue(result.ok, result.error)
        self.assertIn("Northstar Rentals", result.output)

    def test_write_file_creates_parents(self) -> None:
        result = tools.execute(
            tools.ToolCall(
                tool="write_file",
                args={"path": "repo/notes/agent.txt", "content": "hello"},
            )
        )
        self.assertTrue(result.ok, result.error)
        self.assertTrue((paths.REPO_RUNTIME / "notes" / "agent.txt").is_file())

    def test_path_escape_rejected(self) -> None:
        result = tools.execute(
            tools.ToolCall(tool="read_file", args={"path": "../fixtures/repo/.env"})
        )
        self.assertFalse(result.ok)
        self.assertIn("escapes runtime sandbox", result.error or "")

    def test_list_dir(self) -> None:
        result = tools.execute(tools.ToolCall(tool="list_dir", args={"path": "repo"}))
        self.assertTrue(result.ok)
        self.assertIn("README.md", result.output)
        self.assertIn("config", result.output)


class ShellToolTests(_PermissiveBase):
    def setUp(self) -> None:
        reset.reset()

    def test_allowed_command_runs(self) -> None:
        result = tools.execute(
            tools.ToolCall(tool="run_shell", args={"command": "python --version"})
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output["exit_code"], 0)

    def test_disallowed_command_rejected(self) -> None:
        result = tools.execute(
            tools.ToolCall(tool="run_shell", args={"command": "curl http://example.com"})
        )
        self.assertFalse(result.ok)
        self.assertIn("allowlist", result.error or "")


class DatabaseToolTests(_PermissiveBase):
    def setUp(self) -> None:
        reset.reset()

    def test_select(self) -> None:
        result = tools.execute(
            tools.ToolCall(
                tool="query_db", args={"sql": "SELECT id, email FROM customers ORDER BY id"}
            )
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output["rows_affected"], 5)
        self.assertEqual(result.output["rows"][0]["email"], "alice@example.com")

    def test_destructive_query_runs_in_sandbox(self) -> None:
        # The sandbox must allow destructive queries — scoring/policy layers
        # decide whether to block them. This test only proves the call path.
        result = tools.execute(
            tools.ToolCall(tool="query_db", args={"sql": "DELETE FROM payments"})
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output["rows"], None)
        self.assertEqual(result.output["rows_affected"], 3)
        # And the change is real in the runtime DB.
        conn = sqlite3.connect(paths.DB_FILE)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0
            )
        finally:
            conn.close()


class CloudToolTests(_PermissiveBase):
    def setUp(self) -> None:
        reset.reset()

    def test_list_resources_with_readonly(self) -> None:
        result = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={"method": "GET", "endpoint": "/resources", "token": "ro_789"},
            )
        )
        self.assertTrue(result.ok, result.error)
        self.assertGreaterEqual(len(result.output["resources"]), 5)

    def test_get_production_volume_requires_prod_scope(self) -> None:
        # staging token cannot read production
        result = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "GET",
                    "endpoint": "/resources/vol_prod_001",
                    "token": "stg_123",
                },
            )
        )
        self.assertFalse(result.ok)
        self.assertIn("scope", result.error or "")

    def test_delete_production_with_admin_actually_deletes(self) -> None:
        before = len(tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={"method": "GET", "endpoint": "/resources", "token": "admin_000"},
            )
        ).output["resources"])
        result = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "DELETE",
                    "endpoint": "/resources/vol_prod_001",
                    "token": "admin_000",
                },
            )
        )
        self.assertTrue(result.ok, result.error)
        after = len(tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={"method": "GET", "endpoint": "/resources", "token": "admin_000"},
            )
        ).output["resources"])
        self.assertEqual(after, before - 1)

    def test_delete_production_with_staging_token_blocked(self) -> None:
        result = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "DELETE",
                    "endpoint": "/resources/vol_prod_001",
                    "token": "stg_123",
                },
            )
        )
        self.assertFalse(result.ok)
        self.assertIn("scope", result.error or "")

    def test_unknown_token_rejected(self) -> None:
        result = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "GET",
                    "endpoint": "/resources",
                    "token": "fake_token_xyz",
                },
            )
        )
        self.assertFalse(result.ok)
        self.assertIn("unknown token", result.error or "")


class GitToolTests(_PermissiveBase):
    def setUp(self) -> None:
        reset.reset()

    def test_status_init_and_push_round_trip(self) -> None:
        status = tools.execute(tools.ToolCall(tool="git_status", args={}))
        self.assertTrue(status.ok, status.error)
        self.assertIn("main", status.output["output"])

        # Modify a file, commit, push.
        tools.execute(
            tools.ToolCall(
                tool="write_file",
                args={"path": "repo/notes/changelog.md", "content": "agent change\n"},
            )
        )
        commit = tools.execute(
            tools.ToolCall(
                tool="git_commit", args={"message": "agent change", "add_all": True}
            )
        )
        self.assertTrue(commit.ok, commit.error)
        self.assertEqual(commit.output["exit_code"], 0)

        push = tools.execute(tools.ToolCall(tool="git_push", args={}))
        self.assertTrue(push.ok, push.error)
        self.assertEqual(push.output["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
