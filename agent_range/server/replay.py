"""Pure read-only loader for runs/ and experiments/ artifacts.

This module is unit-testable in isolation: it has no FastAPI imports, no
global state, no implicit `paths.ROOT` reference. Every public entry
takes `root: Path` explicitly.

Run directory naming
--------------------
Each run directory is named ``<timestamp>__<scenario>__<agent>`` where
``<timestamp>`` is ``YYYYMMDDTHHMMSS_microseconds`` (single underscore is
a valid character inside the timestamp suffix), ``<scenario>`` may
contain single underscores (e.g. ``staging_prod_confusion``), and
``<agent>`` may contain hyphens (e.g. ``unsafe-override``). The double
underscore ``__`` is the unambiguous delimiter; we split on it and
expect exactly 3 parts. Anything else is a `MalformedRunError`.

Cell -> run resolution
----------------------
Primary path: read ``experiment_summary["cells"]`` directly. This field
is written by ``experiments.py::ExperimentReport.summary()`` and carries
``run_id`` per cell.

Fallback (for experiments persisted before the cells field existed):
group runs by ``(scenario, agent)`` within the experiment time window,
sort each bucket by timestamp, and zip with the experiment's conditions
in declaration order. If sizes don't match, raise `UnresolvedCellError`
loudly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ReplayError(Exception):
    """Base class for all loader failures."""


class RunNotFoundError(ReplayError):
    """Raised when a requested run_id does not exist on disk."""


class ExperimentNotFoundError(ReplayError):
    """Raised when a requested experiment_id does not exist on disk."""


class MalformedRunError(ReplayError):
    """A run directory exists but its contents are unreadable.

    Carries the offending file path and (when applicable) the line number.
    Loud failure: never swallow.
    """

    def __init__(self, path: Path, reason: str, line_number: int | None = None) -> None:
        self.path = path
        self.reason = reason
        self.line_number = line_number
        suffix = f":{line_number}" if line_number is not None else ""
        super().__init__(f"{path}{suffix}: {reason}")


class UnresolvedCellError(ReplayError):
    """An experiment cell could not be matched to a single run directory."""

    def __init__(
        self,
        experiment_id: str,
        scenario: str,
        agent: str,
        condition: str,
        candidates: list[str],
    ) -> None:
        self.experiment_id = experiment_id
        self.scenario = scenario
        self.agent = agent
        self.condition = condition
        self.candidates = candidates
        super().__init__(
            f"experiment {experiment_id!r}: cell "
            f"(scenario={scenario!r}, agent={agent!r}, condition={condition!r}) "
            f"could not be resolved; saw candidates={candidates!r}"
        )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int
    reason: str


@dataclass(frozen=True)
class CallEvent:
    timestamp: str
    tool: str
    args: dict
    agent: str
    override: bool
    decision: str
    risk_score: int
    risk_tier: str
    risk_breakdown: list[DimensionScore]
    ok: bool
    error: str | None
    output_preview: Any  # may be str (often pre-truncated by harness),
    # dict, list, scalar, or None — shown verbatim


@dataclass(frozen=True)
class Run:
    run_id: str
    timestamp: str  # ISO 8601 UTC, parsed from dir name
    scenario: str
    agent: str
    policy: str
    passed: bool
    summary: dict
    events: list[CallEvent]
    final_state_diff: dict
    session_trace: str


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    timestamp: str
    scenario: str
    agent: str
    policy: str
    passed: bool
    max_risk: int
    total_calls: int


@dataclass(frozen=True)
class ExperimentCell:
    scenario: str
    agent: str
    condition: str
    repeat_index: int
    run_id: str
    metrics: dict  # comparison_row[condition] block


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    timestamp: str
    name: str
    spec: dict
    summary: dict
    comparison_table_text: str
    by_condition_text: str
    cells: list[ExperimentCell]


@dataclass(frozen=True)
class ExperimentSummary:
    experiment_id: str
    timestamp: str
    name: str
    total_cells: int
    by_condition: dict


# ---------------------------------------------------------------------------
# Public API: lists
# ---------------------------------------------------------------------------


def list_runs(root: Path) -> list[RunSummary]:
    runs_dir = Path(root) / "runs"
    if not runs_dir.is_dir():
        return []
    out: list[RunSummary] = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            timestamp, scenario, agent = _parse_run_dir_name(child.name)
        except MalformedRunError:
            # Surface the bad directory so the operator can fix it; do not
            # silently skip.
            raise
        summary_path = child / "summary.json"
        if not summary_path.is_file():
            raise MalformedRunError(summary_path, "summary.json is missing")
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MalformedRunError(summary_path, f"invalid JSON: {exc}") from exc
        out.append(
            RunSummary(
                run_id=child.name,
                timestamp=timestamp,
                scenario=scenario,
                agent=agent,
                policy=str(data.get("policy", "")),
                passed=bool(data.get("passed", False)),
                max_risk=int(data.get("max_risk", 0)),
                total_calls=int(data.get("total_calls", 0)),
            )
        )
    out.sort(key=lambda r: r.timestamp, reverse=True)
    return out


def list_experiments(root: Path) -> list[ExperimentSummary]:
    exp_root = Path(root) / "experiments"
    if not exp_root.is_dir():
        return []
    out: list[ExperimentSummary] = []
    for child in exp_root.iterdir():
        if not child.is_dir():
            continue
        timestamp = _parse_experiment_dir_name(child.name)
        summary_path = child / "summary.json"
        if not summary_path.is_file():
            raise MalformedRunError(summary_path, "experiment summary.json is missing")
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MalformedRunError(summary_path, f"invalid JSON: {exc}") from exc
        # Pull the human-friendly name from experiment.json if present.
        spec_path = child / "experiment.json"
        name = ""
        if spec_path.is_file():
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                name = str(spec.get("name", ""))
            except json.JSONDecodeError as exc:
                raise MalformedRunError(spec_path, f"invalid JSON: {exc}") from exc
        out.append(
            ExperimentSummary(
                experiment_id=child.name,
                timestamp=timestamp,
                name=name,
                total_cells=int(data.get("total_cells", 0)),
                by_condition=dict(data.get("by_condition", {})),
            )
        )
    out.sort(key=lambda e: e.timestamp, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Public API: detail
# ---------------------------------------------------------------------------


def load_run(run_id: str, root: Path) -> Run:
    run_dir = Path(root) / "runs" / run_id
    if not run_dir.is_dir():
        raise RunNotFoundError(f"run not found: {run_id}")

    timestamp, scenario, agent = _parse_run_dir_name(run_id)

    summary = _read_json(run_dir / "summary.json")
    events_path = run_dir / "events.jsonl"
    if not events_path.is_file():
        raise MalformedRunError(events_path, "events.jsonl is missing")
    events = _read_events(events_path)

    diff_path = run_dir / "final_state_diff.json"
    if not diff_path.is_file():
        raise MalformedRunError(diff_path, "final_state_diff.json is missing")
    diff = _read_json(diff_path)

    trace_path = run_dir / "session_trace.txt"
    if not trace_path.is_file():
        raise MalformedRunError(trace_path, "session_trace.txt is missing")
    trace = trace_path.read_text(encoding="utf-8")

    return Run(
        run_id=run_id,
        timestamp=timestamp,
        scenario=scenario,
        agent=agent,
        policy=str(summary.get("policy", "")),
        passed=bool(summary.get("passed", False)),
        summary=summary,
        events=events,
        final_state_diff=diff,
        session_trace=trace,
    )


def load_experiment(experiment_id: str, root: Path) -> Experiment:
    exp_dir = Path(root) / "experiments" / experiment_id
    if not exp_dir.is_dir():
        raise ExperimentNotFoundError(f"experiment not found: {experiment_id}")

    timestamp = _parse_experiment_dir_name(experiment_id)

    summary = _read_json(exp_dir / "summary.json")
    spec_path = exp_dir / "experiment.json"
    if not spec_path.is_file():
        raise MalformedRunError(spec_path, "experiment.json is missing")
    spec = _read_json(spec_path)

    comparison_table_path = exp_dir / "comparison_table.txt"
    by_condition_path = exp_dir / "by_condition.txt"
    if not comparison_table_path.is_file():
        raise MalformedRunError(comparison_table_path, "comparison_table.txt is missing")
    if not by_condition_path.is_file():
        raise MalformedRunError(by_condition_path, "by_condition.txt is missing")

    comparison_table_text = comparison_table_path.read_text(encoding="utf-8")
    by_condition_text = by_condition_path.read_text(encoding="utf-8")

    cells = _resolve_cells(experiment_id, summary, spec, root)

    return Experiment(
        experiment_id=experiment_id,
        timestamp=timestamp,
        name=str(spec.get("name", "")),
        spec=spec,
        summary=summary,
        comparison_table_text=comparison_table_text,
        by_condition_text=by_condition_text,
        cells=cells,
    )


# ---------------------------------------------------------------------------
# Cell resolution
# ---------------------------------------------------------------------------


def _resolve_cells(
    experiment_id: str, summary: dict, spec: dict, root: Path
) -> list[ExperimentCell]:
    comparison_index = _index_comparison(summary.get("comparison", []))

    raw_cells = summary.get("cells")
    if isinstance(raw_cells, list) and raw_cells:
        return _resolve_cells_primary(
            experiment_id, raw_cells, comparison_index, root
        )

    # Fallback for experiments persisted before the cells field existed.
    return _resolve_cells_fallback(
        experiment_id, summary, spec, comparison_index, root
    )


def _index_comparison(comparison: list[dict]) -> dict[tuple[str, str], dict]:
    """Map (scenario, agent) -> the full comparison row dict."""
    out: dict[tuple[str, str], dict] = {}
    for row in comparison:
        scenario = row.get("scenario")
        agent = row.get("agent")
        if scenario is None or agent is None:
            continue
        out[(scenario, agent)] = row
    return out


def _resolve_cells_primary(
    experiment_id: str,
    raw_cells: list[dict],
    comparison_index: dict[tuple[str, str], dict],
    root: Path,
) -> list[ExperimentCell]:
    runs_root = Path(root) / "runs"
    out: list[ExperimentCell] = []
    for raw in raw_cells:
        scenario = raw.get("scenario")
        agent = raw.get("agent")
        condition = raw.get("condition")
        repeat_index = int(raw.get("repeat_index", 0))
        run_id = raw.get("run_id")
        if scenario is None or agent is None or condition is None or run_id is None:
            raise UnresolvedCellError(
                experiment_id,
                str(scenario),
                str(agent),
                str(condition),
                candidates=[],
            )
        if not (runs_root / run_id).is_dir():
            raise UnresolvedCellError(
                experiment_id, scenario, agent, condition, candidates=[]
            )
        comparison_row = comparison_index.get((scenario, agent), {})
        metrics = dict(comparison_row.get(condition, {}))
        out.append(
            ExperimentCell(
                scenario=scenario,
                agent=agent,
                condition=condition,
                repeat_index=repeat_index,
                run_id=run_id,
                metrics=metrics,
            )
        )
    return out


def _resolve_cells_fallback(
    experiment_id: str,
    summary: dict,
    spec: dict,
    comparison_index: dict[tuple[str, str], dict],
    root: Path,
) -> list[ExperimentCell]:
    """Single inference rule: per (scenario, agent), sort matching runs in
    the experiment time window by timestamp ascending, and zip with the
    spec's conditions in declaration order. Repeats > 1 walk in repeat
    order within each (condition).
    """
    started = _parse_iso(summary.get("started_at"))
    finished = _parse_iso(summary.get("finished_at"))
    if started is None or finished is None:
        raise UnresolvedCellError(
            experiment_id, "?", "?", "?", candidates=["missing started_at/finished_at"]
        )

    scenario_ids = list(spec.get("scenario_ids", []))
    agent_names = list(spec.get("agent_names", []))
    repeats = int(spec.get("repeats", 1)) or 1
    conditions = [c.get("name") for c in spec.get("conditions", []) if c.get("name")]

    # Bucket runs by (scenario, agent) using their dir-name suffix.
    runs_root = Path(root) / "runs"
    buckets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    if runs_root.is_dir():
        for child in runs_root.iterdir():
            if not child.is_dir():
                continue
            try:
                ts, scenario, agent = _parse_run_dir_name(child.name)
            except MalformedRunError:
                continue
            if not (started <= _parse_dir_timestamp(ts) <= finished):
                continue
            buckets.setdefault((scenario, agent), []).append((ts, child.name))
    for key in buckets:
        buckets[key].sort(key=lambda pair: pair[0])

    out: list[ExperimentCell] = []
    for condition in conditions:
        for scenario in scenario_ids:
            for agent in agent_names:
                for repeat in range(repeats):
                    bucket = buckets.get((scenario, agent), [])
                    expected_index = (
                        conditions.index(condition) * repeats + repeat
                    )
                    if expected_index >= len(bucket):
                        raise UnresolvedCellError(
                            experiment_id,
                            scenario,
                            agent,
                            condition,
                            candidates=[name for _, name in bucket],
                        )
                    _, run_id = bucket[expected_index]
                    comparison_row = comparison_index.get((scenario, agent), {})
                    metrics = dict(comparison_row.get(condition, {}))
                    out.append(
                        ExperimentCell(
                            scenario=scenario,
                            agent=agent,
                            condition=condition,
                            repeat_index=repeat,
                            run_id=run_id,
                            metrics=metrics,
                        )
                    )
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_RUN_DIR_RE = re.compile(
    r"^(?P<ts>\d{8}T\d{6}_\d+)__(?P<scenario>.+?)__(?P<agent>.+)$"
)
_EXPERIMENT_DIR_RE = re.compile(r"^(?P<id>.+)__(?P<ts>\d{8}T\d{6}_\d+)$")


def _parse_run_dir_name(name: str) -> tuple[str, str, str]:
    """Return (timestamp, scenario, agent). Splits on '__' (double
    underscore), since scenario names contain single underscores.
    """
    m = _RUN_DIR_RE.match(name)
    if m is None:
        raise MalformedRunError(
            Path(name),
            f"run directory name does not match <ts>__<scenario>__<agent>: {name!r}",
        )
    parts = name.split("__")
    if len(parts) != 3:
        raise MalformedRunError(
            Path(name),
            f"expected exactly 3 '__'-delimited parts, got {len(parts)}: {name!r}",
        )
    return parts[0], parts[1], parts[2]


def _parse_experiment_dir_name(name: str) -> str:
    """Return the timestamp suffix from an experiment dir name."""
    m = _EXPERIMENT_DIR_RE.match(name)
    if m is None:
        # Don't fail — experiments could be named anything in the future.
        # Fall back to the name itself as the "timestamp" sort key.
        return name
    return m.group("ts")


def _parse_dir_timestamp(ts: str) -> datetime:
    """Parse 'YYYYMMDDTHHMMSS_NNNNNN' into a UTC datetime."""
    base, _, frac = ts.partition("_")
    if len(base) != 15 or base[8] != "T":
        raise MalformedRunError(
            Path(ts), f"unparseable timestamp prefix: {ts!r}"
        )
    dt = datetime.strptime(base, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    if frac:
        try:
            us = int(frac)
        except ValueError as exc:
            raise MalformedRunError(
                Path(ts), f"non-integer microsecond suffix: {ts!r}"
            ) from exc
        dt = dt.replace(microsecond=us)
    return dt


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise MalformedRunError(path, "file is missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedRunError(path, f"invalid JSON: {exc}") from exc


def _read_events(path: Path) -> list[CallEvent]:
    out: list[CallEvent] = []
    text = path.read_text(encoding="utf-8")
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedRunError(path, f"invalid JSON: {exc}", line_number=line_no) from exc
        try:
            breakdown = [
                DimensionScore(
                    name=dim["name"],
                    score=int(dim["score"]),
                    reason=str(dim["reason"]),
                )
                for dim in data.get("risk_breakdown", [])
            ]
            out.append(
                CallEvent(
                    timestamp=str(data["timestamp"]),
                    tool=str(data["tool"]),
                    args=dict(data.get("args") or {}),
                    agent=str(data.get("agent", "")),
                    override=bool(data.get("override", False)),
                    decision=str(data["decision"]),
                    risk_score=int(data["risk_score"]),
                    risk_tier=str(data["risk_tier"]),
                    risk_breakdown=breakdown,
                    ok=bool(data["ok"]),
                    error=data.get("error"),
                    output_preview=data.get("output_preview"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedRunError(
                path, f"event missing required field: {exc}", line_number=line_no
            ) from exc
    return out
