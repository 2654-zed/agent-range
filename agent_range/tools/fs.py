"""Filesystem tools: read_file, write_file, list_dir.

Paths are resolved relative to runtime/ and rejected if they escape the
sandbox via .. or absolute paths.
"""
from __future__ import annotations

from pathlib import Path

from .. import paths
from .base import register


def _resolve(rel_path: str) -> Path:
    candidate = (paths.RUNTIME / rel_path).resolve()
    runtime_root = paths.RUNTIME.resolve()
    if candidate != runtime_root and runtime_root not in candidate.parents:
        raise PermissionError(f"path escapes runtime sandbox: {rel_path}")
    return candidate


@register("read_file")
def read_file(args: dict) -> str:
    path = _resolve(args["path"])
    return path.read_text(encoding="utf-8")


@register("write_file")
def write_file(args: dict) -> dict:
    path = _resolve(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args["content"]
    path.write_text(content, encoding="utf-8")
    return {
        "path": str(path.relative_to(paths.RUNTIME)).replace("\\", "/"),
        "bytes": len(content.encode("utf-8")),
    }


@register("list_dir")
def list_dir(args: dict) -> list[str]:
    path = _resolve(args.get("path", "."))
    return sorted(p.name for p in path.iterdir())
