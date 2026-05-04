"""FastAPI app factory for the replay GUI.

Read-only. Boots `agent-range serve`. Reads runs/ and experiments/ from
disk on every request — no caching, no background workers, no live runs.

The factory takes `root: Path` so it can be pointed at any directory
that contains `runs/` and `experiments/`. Tests call
`create_app(paths.ROOT)` directly via `TestClient` without spinning up
uvicorn.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import replay


_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


def create_app(root: Path) -> FastAPI:
    """Build a fully-wired FastAPI app rooted at `root`.

    `root` is the directory that contains `runs/` and `experiments/`. The
    app reads from those paths on every request. No paths.ROOT references.
    """
    root = Path(root).resolve()
    app = FastAPI(title="Agent Range Replay", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["call_detail"] = _call_detail
    templates.env.filters["tier_class"] = _tier_class
    templates.env.filters["decision_class"] = _decision_class
    templates.env.filters["json_dump"] = _json_dump
    templates.env.filters["output_render"] = _output_render
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.state.replay_root = root

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        runs = replay.list_runs(root)
        experiments = replay.list_experiments(root)
        runs_by_scenario: dict[str, list[replay.RunSummary]] = defaultdict(list)
        for r in runs:
            runs_by_scenario[r.scenario].append(r)
        # Within each scenario the runs are already newest-first because the
        # input list was sorted that way.
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "runs_by_scenario": dict(sorted(runs_by_scenario.items())),
                "experiments": experiments,
            },
        )

    @app.get("/run/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str) -> HTMLResponse:
        try:
            run = replay.load_run(run_id, root)
        except replay.RunNotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            {"run": run},
        )

    @app.get("/run/{run_id}/call/{call_index}", response_class=HTMLResponse)
    def call_inspector(
        request: Request, run_id: str, call_index: int
    ) -> HTMLResponse:
        """Return the inspector partial for one call.

        call_index is 1-indexed (matches the visible card number). Returns
        404 if the run doesn't exist or the index is out of range.
        """
        try:
            run = replay.load_run(run_id, root)
        except replay.RunNotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        if call_index < 1 or call_index > len(run.events):
            raise HTTPException(
                status_code=404,
                detail=(
                    f"call_index {call_index} out of range "
                    f"(run has {len(run.events)} calls)"
                ),
            )
        return templates.TemplateResponse(
            request,
            "_call_inspector.html",
            {"event": run.events[call_index - 1], "index": call_index, "run_id": run_id},
        )

    return app


# ---------------------------------------------------------------------------
# Jinja filters
# ---------------------------------------------------------------------------


def _call_detail(event: replay.CallEvent) -> str:
    """One-line summary of a call's args, tuned per tool."""
    tool = event.tool
    args = event.args or {}
    if tool == "call_cloud_api":
        return (
            f"{args.get('method', '?')} "
            f"{args.get('endpoint', '?')} "
            f"token={args.get('token', '?')}"
        )
    if tool == "query_db":
        return (args.get("sql") or "")
    if tool in ("read_file", "write_file", "list_dir"):
        return f"path={args.get('path', '?')}"
    if tool == "run_shell":
        return f"cmd={args.get('command', '')}"
    if tool.startswith("git_"):
        return ", ".join(f"{k}={v}" for k, v in args.items())
    return repr(args)


def _tier_class(score: int) -> str:
    """Return the CSS class for a 0-100 score's tier."""
    if score <= 20:
        return "tier-low"
    if score <= 40:
        return "tier-elevated"
    if score <= 60:
        return "tier-medium"
    if score <= 80:
        return "tier-high"
    return "tier-critical"


def _decision_class(decision: str) -> str:
    """Return the CSS class for a decision string.

    ALLOW -> 'decision-allow'
    ALLOW_AND_LOG -> 'decision-allow-and-log'
    HARD_STOP -> 'decision-hard-stop'
    etc.
    """
    return "decision-" + decision.lower().replace("_", "-")


def _json_dump(value: Any) -> str:
    """Pretty-print a JSON-serializable value for the inspector."""
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def _output_render(value: Any) -> str:
    """Render an event's output_preview verbatim.

    Strings are returned as-is (preserving newlines and any pre-existing
    truncation marker like '... [truncated, full length 730]'). Dicts and
    lists are pretty-printed JSON. Scalars are stringified.

    The harness writes whatever it stored at the time; we never try to
    'recover' content that wasn't saved.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)
