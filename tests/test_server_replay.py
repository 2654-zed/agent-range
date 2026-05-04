"""Tests for agent_range.server.replay (Phase 1 of the Replay GUI).

These tests run against the on-disk artifacts produced by:

    python -m agent_range.cli experiment run preflight_comparison

If runs/ or experiments/ are empty, the asserting tests fail loudly.
Recovery is the one-line command above. The negative-path tests build
their own broken artifacts in tmpdir, so they don't depend on real data.
"""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from agent_range import paths
from agent_range.server import replay


ROOT = paths.ROOT


class RunDirNameParserTests(unittest.TestCase):
    def test_safe_run_name(self) -> None:
        ts, scenario, agent = replay._parse_run_dir_name(
            "20260504T034552_938950__staging_prod_confusion__safe"
        )
        self.assertEqual(ts, "20260504T034552_938950")
        self.assertEqual(scenario, "staging_prod_confusion")
        self.assertEqual(agent, "safe")

    def test_unsafe_override_keeps_hyphen(self) -> None:
        _, _, agent = replay._parse_run_dir_name(
            "20260504T034553_060821__staging_prod_confusion__unsafe-override"
        )
        self.assertEqual(agent, "unsafe-override")

    def test_log_injection_keeps_underscore(self) -> None:
        _, scenario, _ = replay._parse_run_dir_name(
            "20260504T034553_188515__log_injection__unsafe"
        )
        self.assertEqual(scenario, "log_injection")

    def test_malformed_name_raises(self) -> None:
        with self.assertRaises(replay.MalformedRunError):
            replay._parse_run_dir_name("not_a_valid_run_dir")
        with self.assertRaises(replay.MalformedRunError):
            replay._parse_run_dir_name("20260504T034552_938950__only-two-parts")


class ListRunsTests(unittest.TestCase):
    def test_returns_at_least_twelve_entries_newest_first(self) -> None:
        runs = replay.list_runs(ROOT)
        self.assertGreaterEqual(
            len(runs),
            12,
            "expected >= 12 runs in runs/; "
            "regenerate with: python -m agent_range.cli experiment run preflight_comparison",
        )
        timestamps = [r.timestamp for r in runs]
        self.assertEqual(
            timestamps,
            sorted(timestamps, reverse=True),
            "list_runs must return newest-first",
        )

    def test_each_run_summary_has_expected_fields(self) -> None:
        runs = replay.list_runs(ROOT)
        scenarios_seen = {r.scenario for r in runs}
        agents_seen = {r.agent for r in runs}
        self.assertEqual(
            scenarios_seen,
            {"staging_prod_confusion", "log_injection"},
        )
        self.assertEqual(
            agents_seen,
            {"safe", "unsafe", "unsafe-override"},
        )
        for r in runs:
            self.assertIn(r.policy, {"production", "permissive", "warn-only", "custom", ""})
            self.assertIsInstance(r.passed, bool)
            self.assertIsInstance(r.max_risk, int)
            self.assertIsInstance(r.total_calls, int)


class LoadRunTests(unittest.TestCase):
    def test_load_safe_run(self) -> None:
        safe = next(r for r in replay.list_runs(ROOT) if r.agent == "safe")
        run = replay.load_run(safe.run_id, ROOT)
        self.assertEqual(run.run_id, safe.run_id)
        self.assertEqual(run.agent, "safe")
        self.assertTrue(run.passed)
        self.assertEqual(run.summary["safety_failures"], [])
        self.assertGreater(len(run.events), 0)
        self.assertTrue(all(isinstance(e, replay.CallEvent) for e in run.events))
        # Every event has 5 dimensions in the breakdown.
        for e in run.events:
            self.assertEqual(len(e.risk_breakdown), 5)
            for dim in e.risk_breakdown:
                self.assertIsInstance(dim, replay.DimensionScore)

    def test_load_unsafe_run_under_production_has_hard_stop(self) -> None:
        unsafe_prod = next(
            r
            for r in replay.list_runs(ROOT)
            if r.agent == "unsafe" and r.policy == "production"
        )
        run = replay.load_run(unsafe_prod.run_id, ROOT)
        decisions = {e.decision for e in run.events}
        self.assertIn("HARD_STOP", decisions)
        # And blocked_actions in the summary should be > 0.
        self.assertGreater(run.summary["blocked_actions"], 0)
        # The error message on the blocked event explains the policy gate.
        blocked = [e for e in run.events if e.decision == "HARD_STOP"]
        self.assertTrue(blocked)
        self.assertIn("blocked by policy", blocked[0].error or "")

    def test_load_unsafe_override_run_records_override(self) -> None:
        ovr = next(
            r for r in replay.list_runs(ROOT) if r.agent == "unsafe-override"
        )
        run = replay.load_run(ovr.run_id, ROOT)
        overrides = [e for e in run.events if e.override]
        self.assertGreater(len(overrides), 0)
        self.assertEqual(run.summary["override_count"], len(overrides))
        # The overriding call must be a Critical-tier action.
        self.assertTrue(any(e.risk_tier == "Critical" for e in overrides))

    def test_load_run_includes_diff_and_trace(self) -> None:
        any_run = replay.list_runs(ROOT)[0]
        run = replay.load_run(any_run.run_id, ROOT)
        self.assertIn("cloud_resources", run.final_state_diff)
        self.assertIn("Scenario:", run.session_trace)

    def test_load_run_unknown_id_raises(self) -> None:
        with self.assertRaises(replay.RunNotFoundError):
            replay.load_run("does_not_exist", ROOT)


class ListExperimentsTests(unittest.TestCase):
    def test_returns_at_least_one_entry(self) -> None:
        exps = replay.list_experiments(ROOT)
        self.assertGreaterEqual(len(exps), 1)
        first = exps[0]
        self.assertTrue(first.experiment_id.startswith("preflight_comparison__"))
        self.assertEqual(first.total_cells, 12)
        self.assertIn("preflight_off", first.by_condition)
        self.assertIn("preflight_on", first.by_condition)


class LoadExperimentTests(unittest.TestCase):
    def _experiment_id(self) -> str:
        return replay.list_experiments(ROOT)[0].experiment_id

    def test_load_experiment_resolves_all_cells(self) -> None:
        exp = replay.load_experiment(self._experiment_id(), ROOT)
        self.assertEqual(len(exp.cells), 12)
        # Every cell's run_id resolves to a real directory.
        for cell in exp.cells:
            self.assertTrue(
                (ROOT / "runs" / cell.run_id).is_dir(),
                f"cell {cell} -> run_id {cell.run_id!r} not found",
            )
        # We get both conditions across all cells.
        condition_names = {c.condition for c in exp.cells}
        self.assertEqual(condition_names, {"preflight_off", "preflight_on"})
        # And every (scenario, agent) pair appears under both conditions.
        from collections import defaultdict
        by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
        for c in exp.cells:
            by_pair[(c.scenario, c.agent)].add(c.condition)
        for pair, conds in by_pair.items():
            self.assertEqual(conds, {"preflight_off", "preflight_on"}, pair)

    def test_load_experiment_metrics_per_cell(self) -> None:
        exp = replay.load_experiment(self._experiment_id(), ROOT)
        # Each cell should carry its per-condition metrics block.
        for cell in exp.cells:
            self.assertIn("max_risk", cell.metrics)
            self.assertIn("any_failure", cell.metrics)
            self.assertIn("any_critical_executed", cell.metrics)

    def test_load_experiment_unknown_id_raises(self) -> None:
        with self.assertRaises(replay.ExperimentNotFoundError):
            replay.load_experiment("does_not_exist", ROOT)

    def test_load_experiment_includes_table_text(self) -> None:
        exp = replay.load_experiment(self._experiment_id(), ROOT)
        self.assertIn("preflight_off", exp.by_condition_text)
        self.assertIn("preflight_on", exp.by_condition_text)
        self.assertIn("Experiment: preflight_comparison", exp.comparison_table_text)


class FallbackResolutionTests(unittest.TestCase):
    """Verify the fallback path works on an experiment with cells stripped."""

    def setUp(self) -> None:
        self._tmp_root = ROOT / "_tmp_replay_test_root"
        if self._tmp_root.exists():
            shutil.rmtree(self._tmp_root)
        self._tmp_root.mkdir()
        # Mirror runs/ and experiments/ into the tmp root.
        (self._tmp_root / "runs").mkdir()
        (self._tmp_root / "experiments").mkdir()
        for run_dir in (ROOT / "runs").iterdir():
            shutil.copytree(run_dir, self._tmp_root / "runs" / run_dir.name)
        # Copy the experiment, but strip the cells field from summary.json.
        src_exp = next((ROOT / "experiments").iterdir())
        dst_exp = self._tmp_root / "experiments" / src_exp.name
        shutil.copytree(src_exp, dst_exp)
        summary_path = dst_exp / "summary.json"
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        data.pop("cells", None)
        summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._exp_id = src_exp.name

    def tearDown(self) -> None:
        if self._tmp_root.exists():
            shutil.rmtree(self._tmp_root, ignore_errors=True)

    def test_fallback_resolves_cells_when_field_missing(self) -> None:
        exp = replay.load_experiment(self._exp_id, self._tmp_root)
        self.assertEqual(len(exp.cells), 12)
        for cell in exp.cells:
            self.assertTrue(
                (self._tmp_root / "runs" / cell.run_id).is_dir(),
                f"fallback resolved nonexistent run_id: {cell.run_id!r}",
            )


class MalformedArtifactsTests(unittest.TestCase):
    """Negative-path tests built in tmpdir (no real data)."""

    def setUp(self) -> None:
        self._tmp_root = ROOT / "_tmp_replay_neg_root"
        if self._tmp_root.exists():
            shutil.rmtree(self._tmp_root)
        (self._tmp_root / "runs" / "20260101T000000_000001__demo__safe").mkdir(
            parents=True
        )

    def tearDown(self) -> None:
        if self._tmp_root.exists():
            shutil.rmtree(self._tmp_root, ignore_errors=True)

    def test_missing_summary_json_raises_malformed(self) -> None:
        with self.assertRaises(replay.MalformedRunError) as cm:
            replay.list_runs(self._tmp_root)
        self.assertIn("summary.json", str(cm.exception))

    def test_missing_events_jsonl_raises_when_loading(self) -> None:
        run_dir = self._tmp_root / "runs" / "20260101T000000_000001__demo__safe"
        (run_dir / "summary.json").write_text(
            json.dumps({"policy": "production", "passed": True, "max_risk": 0, "total_calls": 0}),
            encoding="utf-8",
        )
        with self.assertRaises(replay.MalformedRunError) as cm:
            replay.load_run(run_dir.name, self._tmp_root)
        self.assertIn("events.jsonl", str(cm.exception))

    def test_invalid_jsonl_line_carries_line_number(self) -> None:
        run_dir = self._tmp_root / "runs" / "20260101T000000_000001__demo__safe"
        (run_dir / "summary.json").write_text(
            json.dumps({"policy": "production", "passed": True, "max_risk": 0, "total_calls": 0}),
            encoding="utf-8",
        )
        # First line valid; second line broken.
        valid = json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "tool": "list_dir",
                "args": {"path": "."},
                "agent": "x",
                "override": False,
                "decision": "ALLOW",
                "risk_score": 5,
                "risk_tier": "Low",
                "risk_breakdown": [{"name": "position", "score": 5, "reason": "x"}],
                "ok": True,
                "error": None,
                "output_preview": None,
            }
        )
        (run_dir / "events.jsonl").write_text(valid + "\n{not-json\n", encoding="utf-8")
        (run_dir / "final_state_diff.json").write_text("{}", encoding="utf-8")
        (run_dir / "session_trace.txt").write_text("", encoding="utf-8")
        with self.assertRaises(replay.MalformedRunError) as cm:
            replay.load_run(run_dir.name, self._tmp_root)
        self.assertEqual(cm.exception.line_number, 2)


if __name__ == "__main__":
    unittest.main()
