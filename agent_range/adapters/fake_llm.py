"""Deterministic fake LLM for adapter tests.

`FakeAnthropicClient` mimics the small slice of the Anthropic SDK the
real adapter uses (`client.messages.create(...)` returning an object
with a `.content` list of `tool_use` / `text` blocks). Tests construct
it with a scripted sequence of responses and pass it into
`AnthropicAdapter(client=fake_client)`. No network or API key required.

`create_fake_llm_adapter` is registered as the `fake-llm` adapter in
`adapters/__init__.py` so it can be invoked from the CLI for sanity
checks (`scenario run ... --agent fake-llm --adapter-arg script=...`),
though that path is mostly useful inside tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .anthropic_adapter import AnthropicAdapter


@dataclass
class FakeToolUse:
    name: str
    input: dict
    id: str = "tu_fake"
    type: str = "tool_use"


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list


@dataclass
class FakeMessages:
    responses: list[FakeResponse]
    received_calls: list[dict] = field(default_factory=list)
    _index: int = 0

    def create(self, **kwargs: Any) -> FakeResponse:
        self.received_calls.append(kwargs)
        if self._index >= len(self.responses):
            raise RuntimeError(
                f"FakeAnthropicClient exhausted after {self._index} responses"
            )
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class FakeAnthropicClient:
    messages: FakeMessages

    @classmethod
    def from_script(cls, script: Iterable[Any]) -> "FakeAnthropicClient":
        """Build a client from a list of response specs.

        Each entry is one of:
          - a string -> the model returns just that text and stops
          - a list of (tool_name, args_dict) tuples -> the model emits
            one tool_use block per tuple
          - a tuple (text, [(tool_name, args), ...]) -> text + tool_uses
        """
        responses = []
        for spec in script:
            blocks: list = []
            if isinstance(spec, str):
                blocks.append(FakeText(text=spec))
            elif isinstance(spec, list):
                for i, (name, args) in enumerate(spec):
                    blocks.append(FakeToolUse(name=name, input=dict(args), id=f"tu_{i}"))
            elif isinstance(spec, tuple) and len(spec) == 2:
                text, tool_calls = spec
                if text:
                    blocks.append(FakeText(text=text))
                for i, (name, args) in enumerate(tool_calls):
                    blocks.append(FakeToolUse(name=name, input=dict(args), id=f"tu_{i}"))
            else:
                raise TypeError(f"unsupported script entry: {spec!r}")
            responses.append(FakeResponse(content=blocks))
        return cls(messages=FakeMessages(responses=responses))


def create_fake_llm_adapter(
    script: list | str | None = None, **kwargs: Any
) -> AnthropicAdapter:
    """Build an AnthropicAdapter wired to a FakeAnthropicClient."""
    if script is None:
        # Default: model immediately says "done" with no tool calls.
        script = ["done"]
    if isinstance(script, str):
        # Allow CLI to pass a JSON-encoded script.
        script = json.loads(script)
    client = FakeAnthropicClient.from_script(script)
    kwargs.setdefault("model", "fake-model")
    return AnthropicAdapter(client=client, **kwargs)
