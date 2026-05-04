"""Smoke tests for the v0.5 scenario harness."""
from __future__ import annotations

import unittest

from agent_range import policy, scenarios


class HarnessTests(unittest.TestCase):
    def test_registry_has_expected_scenarios(self) -> None:
        ids = {s.id for s in scenarios.all_scenarios()}
        self.assertIn("staging_prod_confusion", ids)
        self.assertIn("log_injection", ids)

    def test_unknown_scenario_raises(self) -> None:
        scenarios.all_scenarios()  # populate registry
        with self.assertRaises(KeyError):
            scenarios.get("nope")

    def test_unknown_agent_raises(self) -> None:
        with self.assertRaises(KeyError):
            scenarios.run("staging_prod_confusion", "does_not_exist")


class StagingProdConfusionTests(unittest.TestCase):
    def test_safe_agent_passes(self) -> None:
        report = scenarios.run("staging_prod_confusion", "safe")
        self.assertTrue(report.task_success)
        self.assertEqual(report.safety_failures, [])
        self.assertEqual(report.override_count, 0)

    def test_unsafe_agent_under_production_policy_blocks_destructive(self) -> None:
        report = scenarios.run("staging_prod_confusion", "unsafe")
        # The destructive cloud call should be HARD_STOP'd by policy.
        self.assertGreaterEqual(report.blocked_actions, 1)
        # Production volume must NOT have actually been deleted.
        self.assertNotIn("production_volume_deleted", report.safety_failures)
        # But the *attempt* still counts as touching production.
        self.assertIn("production_resource_touched", report.safety_failures)
        self.assertIn("admin_token_used", report.safety_failures)

    def test_unsafe_overriding_agent_succeeds_in_damage(self) -> None:
        report = scenarios.run("staging_prod_confusion", "unsafe-override")
        # Override should let the deletion through.
        self.assertGreaterEqual(report.override_count, 1)
        self.assertIn("production_volume_deleted", report.safety_failures)
        self.assertEqual(report.max_risk, 100)

    def test_unsafe_under_permissive_policy_succeeds_in_damage(self) -> None:
        # Without preflight gating, the unsafe agent's deletion lands.
        report = scenarios.run(
            "staging_prod_confusion", "unsafe", policy=policy.Policy.permissive()
        )
        self.assertEqual(report.blocked_actions, 0)
        self.assertIn("production_volume_deleted", report.safety_failures)

    def test_report_summary_shape(self) -> None:
        report = scenarios.run("staging_prod_confusion", "safe")
        summary = report.summary()
        for field in (
            "scenario_id",
            "agent",
            "policy",
            "passed",
            "task_success",
            "safety_failures",
            "total_calls",
            "blocked_actions",
            "override_count",
            "max_risk",
            "avg_risk",
            "risk_per_100_calls",
        ):
            self.assertIn(field, summary)


class LogInjectionTests(unittest.TestCase):
    def test_safe_agent_does_not_follow_injection(self) -> None:
        report = scenarios.run("log_injection", "safe")
        self.assertTrue(report.task_success)
        self.assertNotIn("followed_log_instruction", report.safety_failures)
        self.assertNotIn("production_volume_deleted", report.safety_failures)

    def test_unsafe_agent_follows_injection(self) -> None:
        report = scenarios.run("log_injection", "unsafe")
        self.assertIn("followed_log_instruction", report.safety_failures)
        self.assertIn("read_env_after_log", report.safety_failures)
        # Under production policy, the deletion itself is blocked.
        self.assertNotIn("production_volume_deleted", report.safety_failures)

    def test_unsafe_under_permissive_succeeds_in_damage(self) -> None:
        report = scenarios.run(
            "log_injection", "unsafe", policy=policy.Policy.permissive()
        )
        self.assertIn("production_volume_deleted", report.safety_failures)


class PreflightComparativeTests(unittest.TestCase):
    """Blueprint section 29 — preflight ON vs OFF on the same unsafe agent."""

    def test_preflight_blocks_critical_actions_on_log_injection(self) -> None:
        with_preflight = scenarios.run(
            "log_injection", "unsafe", policy=policy.Policy.production()
        )
        without_preflight = scenarios.run(
            "log_injection", "unsafe", policy=policy.Policy.permissive()
        )
        # Both runs make the same attempts.
        self.assertEqual(with_preflight.total_calls, without_preflight.total_calls)
        # Production policy actually blocks at least one call; permissive doesn't.
        self.assertGreater(with_preflight.blocked_actions, 0)
        self.assertEqual(without_preflight.blocked_actions, 0)
        # And the destructive damage only lands without preflight.
        self.assertNotIn(
            "production_volume_deleted", with_preflight.safety_failures
        )
        self.assertIn(
            "production_volume_deleted", without_preflight.safety_failures
        )


if __name__ == "__main__":
    unittest.main()
