"""Anthropic Claude adapter.

Drives a scenario by calling the Anthropic Messages API in a tool-use
loop. Each tool_use block from the model becomes a `ToolCall`; the
harness executes it through the scoring + policy pipeline and sends the
`ToolResult` back, which we wrap as a `tool_result` content block on the
next request.

Override semantics: the model can set `override: true` on any tool call
to bypass HARD_STOP/CONFIRM_REQUIRED gates. We extract that field from
the tool input and pin it on the `ToolCall`, so the override rate stays
visible in the event log.

For testability, the constructor accepts an optional `client` so tests
can pass a fake without monkey-patching the real SDK.
"""
from __future__ import annotations

import json
from typing import Any

from ..tools import ToolCall, ToolResult
from .schemas import SYSTEM_PROMPT, TOOL_SCHEMAS

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_ITERATIONS = 20


class AnthropicAdapter:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self._client = client or _build_default_client(api_key)
        self.last_final_message: str | None = None
        self.last_iterations: int = 0
        self.last_stop_reason: str | None = None

    def __call__(self, scenario: Any):
        return self._run(scenario)

    def _run(self, scenario: Any):
        messages: list[dict] = [{"role": "user", "content": scenario.task}]
        # Cache the system prompt + tools list — both are stable across
        # iterations within a run, so we save tokens after iteration 1.
        system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        tools = _tools_with_cache_breakpoint()

        iterations = 0
        stop_reason = "max_iterations"
        final_text: str | None = None

        while iterations < self.max_iterations:
            iterations += 1
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )

            assistant_blocks = list(response.content)
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_results: list[dict] = []
            text_chunks: list[str] = []

            for block in assistant_blocks:
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    raw_input = _coerce_dict(block.input)
                    override = bool(raw_input.pop("override", False))
                    call = ToolCall(
                        tool=block.name,
                        args=raw_input,
                        agent="anthropic",
                        override=override,
                    )
                    result: ToolResult = yield call
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": _format_result(result),
                            "is_error": not result.ok,
                        }
                    )
                elif btype == "text":
                    text_chunks.append(block.text)

            if text_chunks:
                final_text = "\n\n".join(text_chunks).strip() or final_text

            if not tool_results:
                stop_reason = "done"
                break

            messages.append({"role": "user", "content": tool_results})

        self.last_final_message = final_text
        self.last_iterations = iterations
        self.last_stop_reason = stop_reason


def _format_result(result: ToolResult) -> str:
    if result.ok:
        return _stringify(result.output)
    decision = result.decision or "ERROR"
    return f"[{decision}] {result.error or 'unknown error'}"


def _stringify(value: Any, limit: int = 4000) -> str:
    if value is None:
        return "(no output)"
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, indent=2, default=repr)
        except Exception:
            text = repr(value)
    if len(text) > limit:
        text = text[:limit] + f"\n... [truncated, full length {len(text)}]"
    return text


def _coerce_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    # Some SDK builds return an object with __dict__
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise TypeError(f"unexpected tool input type: {type(value).__name__}")


def _tools_with_cache_breakpoint() -> list[dict]:
    """Return TOOL_SCHEMAS with a cache_control on the last tool.

    Anthropic caches the system prompt + tool list together up to the
    last cache breakpoint, so this saves tokens on every iteration after
    the first within a single scenario run.
    """
    out = [dict(t) for t in TOOL_SCHEMAS]
    if out:
        out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


def _build_default_client(api_key: str | None):
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "anthropic SDK is not installed. Run: pip install anthropic"
        ) from exc
    return Anthropic(api_key=api_key) if api_key else Anthropic()


def create_anthropic_adapter(**kwargs: Any) -> AnthropicAdapter:
    return AnthropicAdapter(**kwargs)
