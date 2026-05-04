"""LLM agent adapters.

An adapter is a callable `(scenario) -> Generator[ToolCall, ToolResult, None]`
that drives a real model through the scenario's task, yielding tool calls
to the harness and consuming results sent back via `.send()`. This is the
same protocol the v0.5 scripted agents use, so the harness drives both
without special cases.

This package adds three named factories:

    "anthropic"   -> AnthropicAdapter (Claude via Anthropic Messages API)
    "fake-llm"    -> FakeLLMAdapter (deterministic, for tests)

The CLI exposes them as `--agent <name>` on `scenario run`. Adapter
options (model, max_iterations, ...) are passed via `--adapter-arg key=value`.
"""
from __future__ import annotations

from typing import Any, Callable

from .schemas import SYSTEM_PROMPT, TOOL_SCHEMAS

_FACTORIES: dict[str, Callable[..., Any]] = {}


def register(name: str, factory: Callable[..., Any]) -> None:
    if name in _FACTORIES:
        raise RuntimeError(f"adapter already registered: {name}")
    _FACTORIES[name] = factory


def get(name: str, **kwargs: Any) -> Any:
    if name not in _FACTORIES:
        raise KeyError(
            f"unknown adapter: {name!r}; available: {sorted(_FACTORIES)}"
        )
    return _FACTORIES[name](**kwargs)


def available() -> list[str]:
    return sorted(_FACTORIES)


def _register_builtins() -> None:
    from .anthropic_adapter import create_anthropic_adapter
    from .fake_llm import create_fake_llm_adapter

    register("anthropic", create_anthropic_adapter)
    register("fake-llm", create_fake_llm_adapter)


_register_builtins()

__all__ = [
    "SYSTEM_PROMPT",
    "TOOL_SCHEMAS",
    "register",
    "get",
    "available",
]
