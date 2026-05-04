"""Allowlisted shell execution.

Commands are parsed with shlex and the leading executable must be in
ALLOWED. Working directory defaults to runtime/repo. The allowlist is
permissive on purpose — scenarios test whether the agent inspects scripts
before running them, not whether the sandbox blocks every dangerous call.
"""
from __future__ import annotations

import shlex
import subprocess

from .. import paths
from .base import register

ALLOWED = {
    "ls",
    "pwd",
    "echo",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "find",
    "python",
    "git",
    "bash",
    "sh",
}


@register("run_shell")
def run_shell(args: dict) -> dict:
    command = args["command"]
    cwd_rel = args.get("cwd", "repo")
    timeout = args.get("timeout", 30)

    parts = shlex.split(command)
    if not parts:
        raise ValueError("empty command")
    if parts[0] not in ALLOWED:
        raise PermissionError(f"command not in allowlist: {parts[0]}")

    cwd = (paths.RUNTIME / cwd_rel).resolve()
    runtime_root = paths.RUNTIME.resolve()
    if cwd != runtime_root and runtime_root not in cwd.parents:
        raise PermissionError(f"cwd escapes runtime sandbox: {cwd_rel}")

    proc = subprocess.run(
        parts,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }
