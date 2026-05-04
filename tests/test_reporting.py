"""Tests for the v0.6 reporting module."""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from agent_range import paths, policy, reporting, scenarios


def _cleanup_run_dir(run_dir: Path) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)


class WorldDiffTests(unittest.TestCase):
    def test_no_changes_after_safe_run(self) -> None:
        scenarios.run("staging_prod_confusion", "safe")
        diff = reporting.world_diff()
        self.assertEqual(diff["cloud_resources"]["removed"], [])
        self.assertEqual(diff["cloud_resources"]["added"], [])
        self.assertEqual(diff["db_row_counts"], {})

    def test_destructive_run_shows_removed_resource(self) -> None:
        scenarios.run(
            "log_injection",
            "unsafe-override",
            policy=policy.Policy.production(),
        )
        diff = reporting.world_diff()
        self.assertIn("vol_prod_001", diff["cloud_resources"]["removed"])

    def test_blocked_run_leaves_world_clean(self) -> None:
        # Same agent path but no override - production policy stops it.
        scenarios.run(
            "log_injection", "unsafe", policy=policy.Policy.production()
        )
        diff = reporting.world_diff()
        self.assertEqual(diff["cloud_resources"]["removed"], [])


class PersistRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir: Path | None = None

    def tearDown(self) -> None:
        if self.run_dir is not None:
            _cleanup_run_dir(self.run_dir)

    def test_persist_creates_expected_files(self) -> None:
        report = scenarios.run("staging_prod_confusion", "safe")
        self.run_dir = reporting.persist_run(report)

        self.assertTrue(self.run_dir.is_dir())
        for name in ("summary.json", "events.jsonl", "session_trace.txt", "final_state_diff.json"):
            self.assertTrue(
                (self.run_dir / name).is_file(),
                f"expected {name} in {self.run_dir}",
            )

    def test_summary_round_trips(self) -> None:
        report = scenarios.run("staging_prod_confusion", "safe")
        self.run_dir = reporting.persist_run(report)
        loaded = reporting.load_run(self.run_dir.name)
        self.assertEqual(loaded["summary"]["scenario_id"], report.scenario_id)
        self.assertEqual(loaded["summary"]["agent"], report.agent_name)
        self.assertEqual(loaded["summary"]["max_risk"], report.max_risk)
        self.assertEqual(loaded["summary"]["passed"], report.passed())

    def test_events_round_trip_one_per_call(self) -> None:
        report = scenarios.run("log_injection", "unsafe")
        self.run_dir = reporting.persist_run(report)
        loaded = reporting.load_run(self.run_dir.name)
        self.assertEqual(len(loaded["events"]), len(report.events))
        # Risk metadata survives the round trip.
        self.assertTrue(
            all("risk_breakdown" in ev for ev in loaded["events"])
        )

    def test_run_id_is_unique_per_call(self) -> None:
        # Two back-to-back persists should not collide.
        a = reporting.persist_run(
            scenarios.run("staging_prod_confusion", "safe")
        )
        try:
            b = reporting.persist_run(
                scenarios.run("staging_prod_confusion", "safe")
            )
            try:
                self.assertNotEqual(a, b)
            finally:
                _cleanup_run_dir(b)
        finally:
            _cleanup_run_dir(a)

    def test_diff_captures_destructive_unsafe_run(self) -> None:
        report = scenarios.run(
            "log_injection",
            "unsafe-override",
            policy=policy.Policy.production(),
        )
        self.run_dir = reporting.persist_run(report)
        diff = json.loads((self.run_dir / "final_state_diff.json").read_text("utf-8"))
        self.assertIn("vol_prod_001", diff["cloud_resources"]["removed"])

    def test_load_unknown_run_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            reporting.load_run("does_not_exist")


class FormatTraceTests(unittest.TestCase):
    def test_trace_includes_summary_and_call_table(self) -> None:
        report = scenarios.run("staging_prod_confusion", "unsafe")
        text = reporting.format_trace(report)
        self.assertIn("Scenario: staging_prod_confusion", text)
        self.assertIn("Agent:    unsafe", text)
        # The unsafe agent under production policy should be marked FAILED.
        self.assertIn("FAILED", text)
        # And the call table should mention the destructive endpoint.
        self.assertIn("vol_prod_001", text)
        # Findings section should include at least one safety failure.
        self.assertIn("Findings:", text)


class ListRunsTests(unittest.TestCase):
    def test_list_runs_includes_persisted_runs(self) -> None:
        report = scenarios.run("staging_prod_confusion", "safe")
        run_dir = reporting.persist_run(report)
        try:
            runs = reporting.list_runs()
            self.assertIn(run_dir, runs)
        finally:
            _cleanup_run_dir(run_dir)


if __name__ == "__main__":
    unittest.main()
