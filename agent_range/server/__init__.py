"""Replay GUI server (v1).

v1 of the server is read-only: it loads runs/ and experiments/
artifacts from disk and renders a local web UI. There are no live runs,
no editing, no background tasks, no database. See replay.py for the
loader and app.py (Phase 2+) for the FastAPI app factory.
"""
from __future__ import annotations

from .replay import (
    CallEvent,
    DimensionScore,
    Experiment,
    ExperimentCell,
    ExperimentNotFoundError,
    ExperimentSummary,
    MalformedRunError,
    ReplayError,
    Run,
    RunNotFoundError,
    RunSummary,
    UnresolvedCellError,
    list_experiments,
    list_runs,
    load_experiment,
    load_run,
)

__all__ = [
    "CallEvent",
    "DimensionScore",
    "Experiment",
    "ExperimentCell",
    "ExperimentNotFoundError",
    "ExperimentSummary",
    "MalformedRunError",
    "ReplayError",
    "Run",
    "RunNotFoundError",
    "RunSummary",
    "UnresolvedCellError",
    "list_experiments",
    "list_runs",
    "load_experiment",
    "load_run",
]
