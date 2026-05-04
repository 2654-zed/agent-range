"""Git tools — operate on runtime/repo with a local bare remote.

The first git_* call lazily initializes the working repo and a fake bare
remote at runtime/git_remote, then makes the initial commit on main.
Both live inside runtime/, so reset() wipes them together.
"""
from __future__ import annotations

import subprocess

from .. import paths
from .base import register

GIT_REMOTE = paths.RUNTIME / "git_remote"


def _git(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *argv],
        cwd=paths.REPO_RUNTIME,
        capture_output=True,
        text=True,
        check=check,
    )


def _ensure_repo() -> None:
    if not (paths.REPO_RUNTIME / ".git").exists():
        _git("init", "-q", "-b", "main")
        _git("config", "user.email", "northstar-dev@example.local")
        _git("config", "user.name", "Northstar Dev")
        _git("add", "-A")
        _git("commit", "-q", "-m", "Initial commit")
    if not GIT_REMOTE.exists():
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(GIT_REMOTE)],
            check=True,
        )
        existing = _git("remote").stdout.split()
        if "origin" in existing:
            _git("remote", "set-url", "origin", str(GIT_REMOTE))
        else:
            _git("remote", "add", "origin", str(GIT_REMOTE))
        _git("push", "-q", "origin", "main")


@register("git_status")
def git_status(args: dict) -> dict:  # noqa: ARG001
    _ensure_repo()
    proc = _git("status", "--porcelain=v1", "-b")
    return {"output": proc.stdout}


@register("git_commit")
def git_commit(args: dict) -> dict:
    _ensure_repo()
    if args.get("add_all"):
        _git("add", "-A")
    elif args.get("paths"):
        _git("add", *args["paths"])
    proc = _git("commit", "-m", args["message"], check=False)
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@register("git_push")
def git_push(args: dict) -> dict:
    _ensure_repo()
    branch = args.get("branch", "main")
    cmd = ["push", "origin", branch]
    if args.get("force"):
        cmd.append("--force")
    proc = _git(*cmd, check=False)
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "remote": str(GIT_REMOTE),
    }
