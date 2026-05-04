"""Run persistence, world diffs, and session traces.

A scenario run produces a `ScenarioReport` (see agent_range/scenarios).
v0.6 adds:

- Persistence: write the report and event log to runs/<run_id>/, plus a
  `final_state_diff.json` capturing what changed in the world during the
  run, plus a human-readable session_trace.txt.
- World diff: compares cloud resources and DB row counts between fixtures
  and the current runtime to surface what the run actually mutated.
- Trace formatter: a tabular per-call view (blueprint section 15.4),
  good for skimming a run without parsing JSON.

Persistence is opt-in for callers (tests pass `persist=False`); the CLI
opts in by default.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths, world


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_run(report: Any) -> Path:
    """Write a ScenarioReport to runs/<run_id>/ and return the path."""
    run_id = _make_run_id(report)
    run_dir = paths.RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "summary.json").write_text(
        json.dumps(report.summary(), indent=2), encoding="utf-8"
    )
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        for ev in report.events:
            fh.write(json.dumps(ev, default=repr) + "\n")
    (run_dir / "session_trace.txt").write_text(
        format_trace(report), encoding="utf-8"
    )
    (run_dir / "final_state_diff.json").write_text(
        json.dumps(world_diff(), indent=2), encoding="utf-8"
    )
    return run_dir


def list_runs() -> list[Path]:
    if not paths.RUNS.exists():
        return []
    return sorted([p for p in paths.RUNS.iterdir() if p.is_dir()])


def load_run(run_id: str) -> dict:
    run_dir = paths.RUNS / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run not found: {run_id}")
    return {
        "run_id": run_id,
        "path": str(run_dir),
        "summary": json.loads((run_dir / "summary.json").read_text(encoding="utf-8")),
        "session_trace": (run_dir / "session_trace.txt").read_text(encoding="utf-8"),
        "final_state_diff": json.loads(
            (run_dir / "final_state_diff.json").read_text(encoding="utf-8")
        ),
        "events": _read_events(run_dir / "events.jsonl"),
    }


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_run_id(report: Any) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    safe_scenario = _slug(report.scenario_id)
    safe_agent = _slug(report.agent_name)
    return f"{stamp}__{safe_scenario}__{safe_agent}"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "x"


# ---------------------------------------------------------------------------
# World diff
# ---------------------------------------------------------------------------


def world_diff() -> dict:
    return {
        "cloud_resources": _diff_cloud_resources(),
        "db_row_counts": _diff_db_counts(),
    }


def _diff_cloud_resources() -> dict:
    fixture = json.loads(
        (paths.CLOUD_FIXTURES / "resources.json").read_text(encoding="utf-8")
    )
    current = json.loads(
        (paths.CLOUD_RUNTIME / "resources.json").read_text(encoding="utf-8")
    )
    fixture_by_id = {r["id"]: r for r in fixture}
    current_by_id = {r["id"]: r for r in current}

    removed = sorted(set(fixture_by_id) - set(current_by_id))
    added = sorted(set(current_by_id) - set(fixture_by_id))
    changed: list[dict] = []
    for rid in sorted(set(fixture_by_id) & set(current_by_id)):
        if fixture_by_id[rid] != current_by_id[rid]:
            changed.append(
                {
                    "id": rid,
                    "before": fixture_by_id[rid],
                    "after": current_by_id[rid],
                }
            )
    return {"removed": removed, "added": added, "changed": changed}


def _diff_db_counts() -> dict:
    runtime_counts = world.table_counts()
    seed = json.loads(
        (paths.DB_FIXTURES / "seed.json").read_text(encoding="utf-8")
    )
    fixture_counts = {table: len(rows) for table, rows in seed.items()}
    diff: dict[str, dict] = {}
    all_tables = set(runtime_counts) | set(fixture_counts)
    for table in sorted(all_tables):
        before = fixture_counts.get(table, 0)
        after = runtime_counts.get(table, 0)
        if before != after:
            diff[table] = {"before": before, "after": after, "delta": after - before}
    return diff


# ---------------------------------------------------------------------------
# Session trace
# ---------------------------------------------------------------------------


def format_trace(report: Any) -> str:
    lines = [
        f"Scenario: {report.scenario_id}",
        f"Agent:    {report.agent_name}",
        f"Policy:   {report.policy_name}",
        f"Result:   {'PASSED' if report.passed() else 'FAILED'}",
        "",
        f"  total_calls={report.total_calls}"
        f"  blocked={report.blocked_actions}"
        f"  override={report.override_count}"
        f"  max_risk={report.max_risk}"
        f"  avg_risk={report.avg_risk:.1f}",
        "",
        "Calls:",
        f"  {'#':>3}  {'tool':<16} {'detail':<48} {'tier':<9} {'risk':>4} {'decision':<18} ok",
        "  " + "-" * 100,
    ]
    for i, ev in enumerate(report.events, start=1):
        tool = ev.get("tool", "?")
        decision = ev.get("decision", "?")
        risk = ev.get("risk_score", 0)
        tier = ev.get("risk_tier", "?")
        ok = "ok" if ev.get("ok") else "FAIL"
        detail = _format_detail(tool, ev.get("args") or {})
        lines.append(
            f"  {i:>3}. {tool:<16} {detail:<48} {tier:<9} {risk:>4} {decision:<18} {ok}"
        )
    lines.append("")
    lines.append("Findings:")
    if report.safety_failures:
        for failure in report.safety_failures:
            lines.append(f"  ! {failure}")
    else:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


def _format_detail(tool: str, args: dict) -> str:
    if tool == "call_cloud_api":
        return (
            f"{args.get('method', '?')} "
            f"{args.get('endpoint', '?')} "
            f"token={args.get('token', '?')}"
        )[:48]
    if tool == "query_db":
        return (args.get("sql") or "")[:48]
    if tool in ("read_file", "write_file", "list_dir"):
        return f"path={args.get('path', '?')}"[:48]
    if tool == "run_shell":
        return f"cmd={args.get('command', '')}"[:48]
    if tool.startswith("git_"):
        return ", ".join(f"{k}={v}" for k, v in args.items())[:48]
    return repr(args)[:48]
