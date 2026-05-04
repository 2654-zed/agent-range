"""Tool execution pipeline.

Every tool call flows through:

    proposal -> normalization -> scoring -> policy decision -> execute or block

v0.3 wires in the stored-potential scorer (agent_range/scoring.py).
v0.4 wires in the policy engine (agent_range/policy.py): scores translate
into decisions and CONFIRM_REQUIRED/HARD_STOP block execution unless the
caller passes override=True. Override flips the decision to ALLOW_AND_LOG
and is recorded in the event so analyses can measure how often an agent
self-overrides safety boundaries.

The pipeline always emits a structured event regardless of outcome, so the
event log is the source of truth for analysis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .. import events, policy as _policy, scoring

DECISIONS = {"ALLOW", "ALLOW_AND_LOG", "WARN", "CONFIRM_REQUIRED", "HARD_STOP"}


@dataclass
class ToolCall:
    tool: str
    args: dict
    agent: str = "anonymous"
    override: bool = False


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    decision: str = "ALLOW"
    risk_score: int = 0


Handler = Callable[[dict], Any]
_HANDLERS: dict[str, Handler] = {}


def register(name: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        if name in _HANDLERS:
            raise RuntimeError(f"tool already registered: {name}")
        _HANDLERS[name] = fn
        return fn

    return decorator


def registered_tools() -> list[str]:
    return sorted(_HANDLERS)


def execute(call: ToolCall, policy: _policy.Policy | None = None) -> ToolResult:
    active_policy = policy or _policy.get_default_policy()
    handler = _HANDLERS.get(call.tool)
    risk = scoring.score(call.tool, call.args)

    if handler is None:
        # Unknown tools are unconditionally blocked — there is nothing to
        # execute and override should not synthesize a handler.
        result = ToolResult(
            ok=False,
            error=f"unknown tool: {call.tool}",
            decision="HARD_STOP",
            risk_score=risk.total,
        )
        _log(call, result, risk)
        return result

    decision = active_policy.decide(risk.total, override=call.override)

    if decision in ("HARD_STOP", "CONFIRM_REQUIRED"):
        result = ToolResult(
            ok=False,
            error=_block_message(decision, risk),
            decision=decision,
            risk_score=risk.total,
        )
        _log(call, result, risk)
        return result

    try:
        output = handler(call.args)
        result = ToolResult(
            ok=True, output=output, decision=decision, risk_score=risk.total
        )
    except Exception as exc:
        result = ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            decision=decision,
            risk_score=risk.total,
        )

    _log(call, result, risk)
    return result


def _block_message(decision: str, risk: scoring.RiskScore) -> str:
    if decision == "HARD_STOP":
        return (
            f"blocked by policy: {risk.tier()} risk ({risk.total}). "
            "Pass override=True to bypass."
        )
    return (
        f"confirmation required: {risk.tier()} risk ({risk.total}). "
        "Pass override=True to confirm."
    )


def _log(call: ToolCall, result: ToolResult, risk: scoring.RiskScore) -> None:
    events.emit(
        {
            "tool": call.tool,
            "args": call.args,
            "agent": call.agent,
            "override": call.override,
            "decision": result.decision,
            "risk_score": result.risk_score,
            "risk_tier": risk.tier(),
            "risk_breakdown": [d.to_dict() for d in risk.dimensions],
            "ok": result.ok,
            "error": result.error,
            "output_preview": _preview(result.output),
        }
    )


def _preview(value: Any, limit: int = 500) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (dict, list)):
        text = repr(value)
        if len(text) <= limit:
            return value
        return text[:limit] + f"... [truncated, full repr length {len(text)}]"
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, full length {len(text)}]"
