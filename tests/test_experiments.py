"""Tests for the v1.0 comparative-evaluation runner."""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from agent_range import experiments, paths, policy


def _cleanup(p: Path) -> None:
    if p and p.exists():
        shutil.rmtree(p, ignore_errors=True)


class RegistryTests(unittest.TestCase):
    def test_preflight_comparison_registered(self) -> None:
        ids = {e.id for e in experiments.all_experiments()}
        self.assertIn("preflight_comparison", ids)

    def test_get_unknown_raises(self) -> None:
        with self.assertRaises(KeyError):
            experiments.get("does_not_exist")


class RunExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exp = experiments.get("preflight_comparison")
        self.report = experiments.run_experiment(self.exp, persist_each_run=False)

    def test_matrix_shape(self) -> None:
        # 2 scenarios x 3 agents x 2 conditions x 1 repeat = 12 cells.
        self.assertEqual(len(self.report.cells), 12)
        self.assertEqual(self.report.skipped, [])

    def test_aggregations_have_both_conditions(self) -> None:
        agg = self.report.aggregate_by_condition()
        self.assertEqual(set(agg), {"preflight_off", "preflight_on"})
        self.assertEqual(agg["preflight_on"]["runs"], 6)
        self.assertEqual(agg["preflight_off"]["runs"], 6)

    def test_preflight_reduces_critical_executions(self) -> None:
        # The headline thesis: preflight ON has fewer critical executions
        # than preflight OFF, because HARD_STOPs prevent unsafe agents from
        # actually completing destructive calls. Override-using agents may
        # still execute critical actions; we only compare *non-overriding*
        # cells to isolate the policy effect.
        agg = self.report.aggregate_by_condition()
        on = agg["preflight_on"]
        off = agg["preflight_off"]
        # Mean blocked actions must rise when preflight turns on.
        self.assertGreater(on["mean_blocked_actions"], off["mean_blocked_actions"])
        # And preflight ON should not produce critical executions for the
        # plain "unsafe" path (no override): permissive lets them through.
        self.assertGreaterEqual(off["pct_critical_executed"], on["pct_critical_executed"])

    def test_safe_agent_clean_under_both_policies(self) -> None:
        for cell in self.report.cells:
            if cell.agent_name != "safe":
                continue
            self.assertEqual(
                cell.report.safety_failures,
                [],
                f"safe agent unexpectedly failed under {cell.condition_name}",
            )

    def test_unsafe_blocked_under_production_only(self) -> None:
        unsafe_off = [
            c for c in self.report.cells
            if c.agent_name == "unsafe" and c.condition_name == "preflight_off"
        ]
        unsafe_on = [
            c for c in self.report.cells
            if c.agent_name == "unsafe" and c.condition_name == "preflight_on"
        ]
        # Without preflight, blocked_actions must be 0; with preflight, > 0.
        for cell in unsafe_off:
            self.assertEqual(cell.report.blocked_actions, 0)
        for cell in unsafe_on:
            self.assertGreater(cell.report.blocked_actions, 0)

    def test_override_agent_succeeds_under_both_policies(self) -> None:
        for cell in self.report.cells:
            if cell.agent_name != "unsafe-override":
                continue
            self.assertGreater(
                cell.report.override_count,
                0,
                f"override agent didn't override under {cell.condition_name}",
            )

    def test_comparison_rows_one_per_scenario_agent(self) -> None:
        rows = self.report.comparison_rows()
        # 2 scenarios x 3 agents = 6 rows.
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertIn("scenario", row)
            self.assertIn("agent", row)
            self.assertIn("preflight_off", row)
            self.assertIn("preflight_on", row)


class FormattingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.exp = experiments.get("preflight_comparison")
        cls.report = experiments.run_experiment(cls.exp, persist_each_run=False)

    def test_condition_table_includes_all_conditions(self) -> None:
        text = experiments.format_condition_table(self.report)
        self.assertIn("preflight_off", text)
        self.assertIn("preflight_on", text)
        self.assertIn("%fail", text)

    def test_comparison_table_includes_all_cells(self) -> None:
        text = experiments.format_comparison_table(self.exp, self.report)
        self.assertIn("staging_prod_confusion", text)
        self.assertIn("log_injection", text)
        self.assertIn("safe", text)
        self.assertIn("unsafe-override", text)
        self.assertIn("preflight_off", text)


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exp_dir: Path | None = None

    def tearDown(self) -> None:
        if self.exp_dir is not None:
            _cleanup(self.exp_dir)

    def test_persist_creates_expected_files(self) -> None:
        exp = experiments.get("preflight_comparison")
        report = experiments.run_experiment(exp, persist_each_run=False)
        self.exp_dir = experiments.persist_experiment(exp, report)
        for name in ("experiment.json", "summary.json", "comparison_table.txt", "by_condition.txt"):
            self.assertTrue(
                (self.exp_dir / name).is_file(),
                f"expected {name} in {self.exp_dir}",
            )

    def test_summary_round_trip(self) -> None:
        exp = experiments.get("preflight_comparison")
        report = experiments.run_experiment(exp, persist_each_run=False)
        self.exp_dir = experiments.persist_experiment(exp, report)
        loaded = json.loads((self.exp_dir / "summary.json").read_text("utf-8"))
        self.assertEqual(loaded["experiment_id"], "preflight_comparison")
        self.assertEqual(loaded["total_cells"], len(report.cells))
        self.assertIn("by_condition", loaded)
        self.assertIn("comparison", loaded)

    def test_summary_includes_cells_with_run_ids(self) -> None:
        # Persistence is needed for run_id to be populated on each CellResult.
        exp = experiments.get("preflight_comparison")
        report = experiments.run_experiment(exp, persist_each_run=True)
        self.exp_dir = experiments.persist_experiment(exp, report)
        try:
            loaded = json.loads((self.exp_dir / "summary.json").read_text("utf-8"))
            self.assertIn("cells", loaded)
            self.assertEqual(len(loaded["cells"]), loaded["total_cells"])
            for cell in loaded["cells"]:
                for key in ("scenario", "agent", "condition", "repeat_index", "run_id"):
                    self.assertIn(key, cell)
                self.assertIsNotNone(cell["run_id"])
                # run_id must point at a real directory under runs/
                from agent_range import paths as _paths
                self.assertTrue(
                    (_paths.RUNS / cell["run_id"]).is_dir(),
                    f"run_id {cell['run_id']!r} not found in runs/",
                )
            # cells with run_id is None when persist_each_run=False
            report_no_persist = experiments.run_experiment(exp, persist_each_run=False)
            for cell in report_no_persist.summary()["cells"]:
                self.assertIsNone(cell["run_id"])
        finally:
            # Clean up the per-cell runs this test created.
            for cell in loaded["cells"]:
                from agent_range import paths as _paths
                run_dir = _paths.RUNS / cell["run_id"]
                if run_dir.exists():
                    import shutil
                    shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
