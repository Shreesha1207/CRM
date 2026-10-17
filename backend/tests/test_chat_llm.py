"""The request sent to the Claude API, and the reply read back, without the network."""

import json

import httpx2
import pytest

from app.core.config import Settings
from app.services.chat import tools
from app.services.chat.llm import AnthropicChatModel

REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "c2lnbmF0dXJl"},
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_1", "name": "list_services", "input": {"query": "yoga"}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000},
}


def model_for(name: str, seen: list) -> AnthropicChatModel:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.headers, json.loads(request.content)))
        return httpx2.Response(200, json=REPLY)

    settings = Settings(anthropic_api_key="test-key", chat_model=name)
    return AnthropicChatModel(settings, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


MESSAGES = [{"role": "user", "content": "Any yoga?"}, {"role": "system", "content": "Current time: ..."}]


def test_the_request_uses_adaptive_thinking_low_effort_caching_and_fallbacks() -> None:
    seen: list = []
    turn = model_for("claude-opus-5-5", seen).run_turn(
        system=[{"type": "text", "text": "You are the booking assistant."}], tools=tools.definitions(), messages=MESSAGES
    )
    headers, body = seen[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert body["messages"] == MESSAGES
    assert [t["name"] for t in body["tools"]] == [t.name for t in tools.TOOLS]
    assert all(t["input_schema"]["additionalProperties"] is False for t in body["tools"])
    assert "tool_choice" not in body  # forcing a tool is refused by current models

    # The reply comes back exactly as the API sent it, to be replayed unchanged.
    assert turn.content == REPLY["content"]
    assert turn.stop_reason == "tool_use"
    assert turn.tool_uses == [REPLY["content"][2]]
    assert turn.input_tokens == 110 and turn.output_tokens == 5  # cache reads are not counted


@pytest.mark.parametrize("name", ["claude-haiku-5-5"])
def test_models_without_server_side_fallback_do_not_ask_for_it(name) -> None:
    seen: list = []
    model_for(name, seen).run_turn(system=[], tools=[], messages=MESSAGES)
    headers, body = seen[0]
    assert "fallbacks" not in body
    assert "server-side-fallback" not in headers.get("anthropic-beta", "")
