"""Agent Range CLI — reset, snapshot, restore, status, tools, call, events."""
from __future__ import annotations

import argparse
import json
import sys

from . import (
    adapters,
    events,
    experiments as _experiments,
    paths,
    policy as _policy,
    reporting,
    reset as _reset,
    scenarios as _scenarios,
    scoring,
    tools,
    world,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-range")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("reset", help="Wipe runtime/ and rebuild from fixtures/")
    sub.add_parser("status", help="Show a summary of the current world state")

    snap = sub.add_parser("snapshot", help="Archive the current runtime/ state")
    snap.add_argument("--name", help="Optional snapshot label")

    rest = sub.add_parser("restore", help="Restore a snapshot into runtime/")
    rest.add_argument("name", help="Snapshot label to restore")

    sub.add_parser("tools", help="List registered tools")

    call = sub.add_parser("call", help="Invoke a tool")
    call.add_argument("tool", help="Tool name")
    call.add_argument(
        "--args",
        default="{}",
        help='Tool arguments as a JSON object (default "{}")',
    )
    call.add_argument(
        "--agent",
        default="cli",
        help='Calling-agent label for the event log (default "cli")',
    )
    call.add_argument(
        "--policy",
        choices=["production", "permissive", "warn-only"],
        default="production",
        help="Policy profile (default: production)",
    )
    call.add_argument(
        "--override",
        action="store_true",
        help="Bypass HARD_STOP/CONFIRM_REQUIRED (recorded in event log)",
    )

    sc = sub.add_parser(
        "score",
        help="Compute a stored-potential score for a tool call without executing",
    )
    sc.add_argument("tool", help="Tool name")
    sc.add_argument("--args", default="{}", help="Tool arguments as a JSON object")

    ev = sub.add_parser("events", help="Print the event log")
    ev.add_argument("--limit", type=int, default=None, help="Show only the last N events")
    ev.add_argument("--clear", action="store_true", help="Clear the event log instead")

    scn = sub.add_parser("scenario", help="Run or inspect adversarial scenarios")
    scn_sub = scn.add_subparsers(dest="scenario_cmd", required=True)
    scn_sub.add_parser("list", help="List registered scenarios")
    scn_show = scn_sub.add_parser("show", help="Print a scenario's spec")
    scn_show.add_argument("scenario_id")
    scn_run = scn_sub.add_parser("run", help="Run a scenario with a named agent")
    scn_run.add_argument("scenario_id")
    scn_run.add_argument(
        "--agent",
        default="safe",
        help=(
            "Agent label. Either a scripted scenario agent (safe / unsafe / "
            "unsafe-override) or a registered adapter (anthropic / fake-llm)."
        ),
    )
    scn_run.add_argument(
        "--policy",
        choices=["production", "permissive", "warn-only"],
        default="production",
    )
    scn_run.add_argument(
        "--events",
        action="store_true",
        help="Print the full event log after the run summary",
    )
    scn_run.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the run to runs/<run_id>/",
    )
    scn_run.add_argument(
        "--adapter-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Adapter constructor kwargs (repeatable). Examples: "
            "--adapter-arg model=claude-sonnet-4-5 "
            "--adapter-arg max_iterations=15. Values are JSON-decoded "
            "when possible, otherwise treated as strings."
        ),
    )

    sub.add_parser("adapters", help="List registered LLM agent adapters")

    exp = sub.add_parser(
        "experiment", help="Run or inspect comparative-evaluation experiments"
    )
    exp_sub = exp.add_subparsers(dest="experiment_cmd", required=True)
    exp_sub.add_parser("list", help="List registered experiments")
    exp_show = exp_sub.add_parser("show", help="Print an experiment's spec")
    exp_show.add_argument("experiment_id")
    exp_run = exp_sub.add_parser("run", help="Run an experiment matrix")
    exp_run.add_argument("experiment_id")
    exp_run.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the experiment to experiments/<exp_dir>/",
    )
    exp_run.add_argument(
        "--no-persist-runs",
        action="store_true",
        help="Skip persisting individual cell runs (saves disk if running large matrices)",
    )

    srv = sub.add_parser(
        "serve",
        help="Boot the local replay GUI (read-only) at http://<host>:<port>/",
    )
    srv.add_argument("--host", default="127.0.0.1", help="Default 127.0.0.1")
    srv.add_argument("--port", type=int, default=8765, help="Default 8765")
    srv.add_argument(
        "--root",
        default=None,
        help=(
            "Directory containing runs/ and experiments/. "
            "Defaults to the current working directory."
        ),
    )

    rpt = sub.add_parser("report", help="Inspect persisted scenario runs")
    rpt_sub = rpt.add_subparsers(dest="report_cmd", required=True)
    rpt_sub.add_parser("list", help="List runs in runs/")
    rpt_show = rpt_sub.add_parser("show", help="Print a run's session trace and summary")
    rpt_show.add_argument("run_id")
    rpt_show.add_argument(
        "--format",
        choices=["trace", "summary", "diff", "json"],
        default="trace",
    )

    args = parser.parse_args()

    if args.cmd == "reset":
        _reset.reset()
        print(f"World reset -> {paths.RUNTIME}")
    elif args.cmd == "snapshot":
        label = _reset.snapshot(args.name)
        print(f"Snapshot saved -> {paths.SNAPSHOTS / label}")
    elif args.cmd == "restore":
        _reset.restore(args.name)
        print(f"Restored {args.name} -> {paths.RUNTIME}")
    elif args.cmd == "status":
        _print_status()
    elif args.cmd == "tools":
        for name in tools.registered_tools():
            print(name)
    elif args.cmd == "call":
        _do_call(args)
    elif args.cmd == "score":
        _do_score(args)
    elif args.cmd == "events":
        _do_events(args)
    elif args.cmd == "scenario":
        _do_scenario(args)
    elif args.cmd == "report":
        _do_report(args)
    elif args.cmd == "adapters":
        for name in adapters.available():
            print(name)
    elif args.cmd == "experiment":
        _do_experiment(args)
    elif args.cmd == "serve":
        _do_serve(args)


def _print_status() -> None:
    if not world.is_initialized():
        print("World not initialized. Run: python -m agent_range.cli reset")
        return
    resources = world.cloud_resources()
    tokens = world.cloud_tokens()
    counts = world.table_counts()
    print(f"Runtime:         {paths.RUNTIME}")
    print(f"Cloud resources: {len(resources)}")
    print(f"Cloud tokens:    {len(tokens)}")
    print("DB tables:")
    for name, n in counts.items():
        print(f"  {name:<14} {n} rows")
    print(f"Tools:           {len(tools.registered_tools())} registered")


_POLICY_FACTORIES = {
    "production": _policy.Policy.production,
    "permissive": _policy.Policy.permissive,
    "warn-only": _policy.Policy.warn_only,
}


def _parse_adapter_args(items: list[str]) -> dict:
    parsed: dict = {}
    for item in items:
        if "=" not in item:
            print(f"--adapter-arg expects KEY=VALUE, got {item!r}", file=sys.stderr)
            sys.exit(2)
        key, raw = item.split("=", 1)
        try:
            parsed[key] = json.loads(raw)
        except json.JSONDecodeError:
            parsed[key] = raw
    return parsed


def _do_call(args) -> None:
    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"--args is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(tool_args, dict):
        print("--args must be a JSON object", file=sys.stderr)
        sys.exit(2)

    policy = _POLICY_FACTORIES[args.policy]()
    result = tools.execute(
        tools.ToolCall(
            tool=args.tool,
            args=tool_args,
            agent=args.agent,
            override=args.override,
        ),
        policy=policy,
    )
    print(
        json.dumps(
            {
                "ok": result.ok,
                "decision": result.decision,
                "risk_score": result.risk_score,
                "error": result.error,
                "output": result.output,
            },
            indent=2,
            default=repr,
        )
    )
    if not result.ok:
        sys.exit(1)


def _do_score(args) -> None:
    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"--args is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(tool_args, dict):
        print("--args must be a JSON object", file=sys.stderr)
        sys.exit(2)
    risk = scoring.score(args.tool, tool_args)
    print(f"total: {risk.total}  tier: {risk.tier()}")
    for dim in risk.dimensions:
        print(f"  {dim.name:<16} {dim.score:>3}  {dim.reason}")


def _do_scenario(args) -> None:
    if args.scenario_cmd == "list":
        for scenario in _scenarios.all_scenarios():
            print(
                f"  {scenario.id:<28} {scenario.name}"
                f"  agents={sorted(scenario.agents)}"
            )
        return
    if args.scenario_cmd == "show":
        _scenarios.all_scenarios()  # populate registry
        scenario = _scenarios.get(args.scenario_id)
        print(f"id:    {scenario.id}")
        print(f"name:  {scenario.name}")
        print(f"task:  {scenario.task}")
        print(f"adversarial condition: {scenario.adversarial_condition}")
        print("expected safe behavior:")
        for item in scenario.expected_safe_behavior:
            print(f"  - {item}")
        print("failure conditions:")
        for label, _ in scenario.failure_conditions:
            print(f"  - {label}")
        print(f"agents: {sorted(scenario.agents)}")
        return
    if args.scenario_cmd == "run":
        policy = _POLICY_FACTORIES[args.policy]()
        adapter_kwargs = _parse_adapter_args(args.adapter_arg)
        report = _scenarios.run(
            args.scenario_id,
            args.agent,
            policy=policy,
            adapter_kwargs=adapter_kwargs,
        )
        run_dir = None
        if not args.no_persist:
            run_dir = reporting.persist_run(report)
        summary = report.summary()
        if run_dir is not None:
            summary["run_dir"] = str(run_dir)
        print(json.dumps(summary, indent=2))
        if args.events:
            print("---")
            for ev in report.events:
                print(json.dumps(ev, default=repr))
        if not report.passed():
            sys.exit(1)


def _do_serve(args) -> None:
    from pathlib import Path
    import uvicorn
    from .server.app import create_app

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    if not (root / "runs").is_dir() and not (root / "experiments").is_dir():
        print(
            f"warning: {root} contains neither runs/ nor experiments/. "
            "The server will boot but pages will be empty.",
            file=sys.stderr,
        )
    app = create_app(root)
    print(f"agent-range serve  http://{args.host}:{args.port}/  (root={root})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _do_experiment(args) -> None:
    if args.experiment_cmd == "list":
        for exp in _experiments.all_experiments():
            print(
                f"  {exp.id:<28} {exp.name}  "
                f"(scenarios={len(exp.scenario_ids)}, "
                f"agents={len(exp.agent_names)}, "
                f"conditions={len(exp.conditions)}, "
                f"repeats={exp.repeats})"
            )
        return

    if args.experiment_cmd == "show":
        exp = _experiments.get(args.experiment_id)
        print(f"id:           {exp.id}")
        print(f"name:         {exp.name}")
        print(f"description:  {exp.description}")
        print(f"scenarios:    {exp.scenario_ids}")
        print(f"agents:       {exp.agent_names}")
        print(f"repeats:      {exp.repeats}")
        print("conditions:")
        for c in exp.conditions:
            print(f"  - {c.name}: {c.description}")
        return

    if args.experiment_cmd == "run":
        exp = _experiments.get(args.experiment_id)
        report = _experiments.run_experiment(
            exp, persist_each_run=not args.no_persist_runs
        )
        print(_experiments.format_condition_table(report))
        print(_experiments.format_comparison_table(exp, report))
        if not args.no_persist:
            exp_dir = _experiments.persist_experiment(exp, report)
            print(f"Experiment saved -> {exp_dir}")


def _do_report(args) -> None:
    if args.report_cmd == "list":
        runs = reporting.list_runs()
        if not runs:
            print("(no runs persisted)")
            return
        for run_dir in runs:
            try:
                summary = json.loads(
                    (run_dir / "summary.json").read_text(encoding="utf-8")
                )
            except FileNotFoundError:
                continue
            mark = "PASS" if summary.get("passed") else "FAIL"
            print(
                f"  {run_dir.name}  [{mark}] "
                f"{summary.get('scenario_id', '?'):<26} "
                f"agent={summary.get('agent', '?'):<18} "
                f"policy={summary.get('policy', '?'):<11} "
                f"max_risk={summary.get('max_risk', '?')}"
            )
        return

    if args.report_cmd == "show":
        run = reporting.load_run(args.run_id)
        if args.format == "trace":
            print(run["session_trace"])
        elif args.format == "summary":
            print(json.dumps(run["summary"], indent=2))
        elif args.format == "diff":
            print(json.dumps(run["final_state_diff"], indent=2))
        elif args.format == "json":
            print(
                json.dumps(
                    {
                        "summary": run["summary"],
                        "final_state_diff": run["final_state_diff"],
                        "events": run["events"],
                    },
                    indent=2,
                    default=repr,
                )
            )


def _do_events(args) -> None:
    if args.clear:
        events.clear()
        print("Event log cleared")
        return
    log = events.read_all()
    if args.limit is not None:
        log = log[-args.limit:]
    for entry in log:
        print(json.dumps(entry, default=repr))


if __name__ == "__main__":
    main()
