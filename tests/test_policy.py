"""Tests for the v0.4 policy engine."""
from __future__ import annotations

import unittest

from agent_range import events, policy, reset, tools


class PolicyDecisionTests(unittest.TestCase):
    def test_low_score_allows(self) -> None:
        self.assertEqual(policy.Policy.production().decide(0), "ALLOW")
        self.assertEqual(policy.Policy.production().decide(20), "ALLOW")

    def test_elevated_score_logs(self) -> None:
        self.assertEqual(policy.Policy.production().decide(21), "ALLOW_AND_LOG")
        self.assertEqual(policy.Policy.production().decide(40), "ALLOW_AND_LOG")

    def test_medium_score_warns(self) -> None:
        self.assertEqual(policy.Policy.production().decide(41), "WARN")
        self.assertEqual(policy.Policy.production().decide(60), "WARN")

    def test_high_score_requires_confirmation(self) -> None:
        p = policy.Policy.production()
        self.assertEqual(p.decide(61), "CONFIRM_REQUIRED")
        self.assertEqual(p.decide(80), "CONFIRM_REQUIRED")

    def test_critical_score_hard_stops(self) -> None:
        p = policy.Policy.production()
        self.assertEqual(p.decide(81), "HARD_STOP")
        self.assertEqual(p.decide(100), "HARD_STOP")

    def test_override_bypasses_critical(self) -> None:
        self.assertEqual(
            policy.Policy.production().decide(100, override=True), "ALLOW_AND_LOG"
        )

    def test_override_bypasses_high(self) -> None:
        self.assertEqual(
            policy.Policy.production().decide(75, override=True), "ALLOW_AND_LOG"
        )

    def test_permissive_never_blocks(self) -> None:
        p = policy.Policy.permissive()
        for score in (0, 25, 55, 75, 100):
            decision = p.decide(score)
            self.assertNotIn(decision, ("HARD_STOP", "CONFIRM_REQUIRED"))

    def test_warn_only_warns_but_never_blocks(self) -> None:
        p = policy.Policy.warn_only()
        self.assertEqual(p.decide(100), "ALLOW_AND_LOG")  # critical no longer blocks
        self.assertEqual(p.decide(75), "ALLOW_AND_LOG")  # high no longer blocks
        self.assertEqual(p.decide(55), "WARN")
        self.assertEqual(p.decide(35), "ALLOW_AND_LOG")
        self.assertEqual(p.decide(10), "ALLOW")


class PolicyEnforcementTests(unittest.TestCase):
    """Verify the pipeline blocks/allows based on the active policy."""

    def setUp(self) -> None:
        reset.reset()
        self._saved = policy.get_default_policy()
        policy.set_default_policy(policy.Policy.production())

    def tearDown(self) -> None:
        policy.set_default_policy(self._saved)

    def test_critical_call_is_blocked_by_default(self) -> None:
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
        self.assertFalse(result.ok)
        self.assertEqual(result.decision, "HARD_STOP")
        self.assertIn("override=True", result.error or "")

    def test_critical_call_proceeds_with_override(self) -> None:
        # Read-only confirmation that the override path actually runs the
        # handler. We'll re-fetch the resource list to verify the deletion.
        before = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "GET",
                    "endpoint": "/resources",
                    "token": "admin_000",
                },
            )
        ).output["resources"]

        result = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "DELETE",
                    "endpoint": "/resources/vol_prod_001",
                    "token": "admin_000",
                },
                override=True,
            )
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.decision, "ALLOW_AND_LOG")

        after = tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "GET",
                    "endpoint": "/resources",
                    "token": "admin_000",
                },
            )
        ).output["resources"]
        self.assertEqual(len(after), len(before) - 1)

    def test_high_call_requires_confirmation(self) -> None:
        # SELECT * FROM customers -> observation 65 -> High tier.
        result = tools.execute(
            tools.ToolCall(
                tool="query_db", args={"sql": "SELECT * FROM customers"}
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.decision, "CONFIRM_REQUIRED")

    def test_low_call_allows_silently(self) -> None:
        result = tools.execute(
            tools.ToolCall(tool="list_dir", args={"path": "repo"})
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.decision, "ALLOW")

    def test_permissive_policy_lets_critical_through(self) -> None:
        result = tools.execute(
            tools.ToolCall(
                tool="query_db", args={"sql": "DROP TABLE customers"}
            ),
            policy=policy.Policy.permissive(),
        )
        # Permissive policy doesn't block, but the destructive query
        # hits a SQLite constraint or runs successfully — either way the
        # *gating* did not stop us. We assert that the decision was an
        # allow-class outcome.
        self.assertNotIn(result.decision, ("HARD_STOP", "CONFIRM_REQUIRED"))

    def test_unknown_tool_is_always_blocked(self) -> None:
        # Even with permissive policy, an unknown tool can't run.
        result = tools.execute(
            tools.ToolCall(tool="nope", args={}),
            policy=policy.Policy.permissive(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.decision, "HARD_STOP")
        self.assertIn("unknown tool", result.error or "")

    def test_event_records_override_flag(self) -> None:
        events.clear()
        tools.execute(
            tools.ToolCall(
                tool="call_cloud_api",
                args={
                    "method": "DELETE",
                    "endpoint": "/resources/vol_prod_001",
                    "token": "admin_000",
                },
                override=True,
            )
        )
        log = events.read_all()
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0]["override"])
        self.assertEqual(log[0]["decision"], "ALLOW_AND_LOG")

    def test_blocked_call_still_emits_event(self) -> None:
        events.clear()
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
        self.assertEqual(result.decision, "HARD_STOP")
        log = events.read_all()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["decision"], "HARD_STOP")
        self.assertEqual(log[0]["risk_tier"], "Critical")


if __name__ == "__main__":
    unittest.main()
