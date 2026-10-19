"""The requests sent to Groq for the assistant's model, and the replies read back, without the network."""

import json

import pytest

from app.core.config import Settings
from app.services.chat import tools
from app.services.chat.groq import RateLimited, ToolCallFailed
from app.services.chat.llm import GroqChatModel
from tests.chat_fakes import FakeResponse, completion, fake_http

MESSAGES = [{"role": "user", "content": "Any yoga on Tuesday?"}, {"role": "system", "content": "Current time: ..."}]
CALL = {"id": "call_1", "type": "function", "function": {"name": "list_services", "arguments": '{"query": "yoga"}'}}


def model(**settings) -> GroqChatModel:
    return GroqChatModel(Settings(groq_api_key="gsk_test", **settings))


def test_the_request_uses_the_system_prompt_tools_and_reasoning_effort(monkeypatch) -> None:
    m = model()
    sent = fake_http(monkeypatch, m.client, [completion(None, [CALL], "tool_calls", prompt_tokens=3000, completion_tokens=40, prompt_tokens_details={"cached_tokens": 2500})])
    turn = m.run_turn(system="You are the booking assistant.", tools=tools.definitions(), messages=MESSAGES)

    url, body, headers = sent[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert headers["Authorization"] == "Bearer gsk_test"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"] == [{"role": "system", "content": "You are the booking assistant."}, *MESSAGES]
    assert body["tool_choice"] == "auto" and body["parallel_tool_calls"] is True
    assert body["reasoning_effort"] == "low"
    assert [t["function"]["name"] for t in body["tools"]] == [t.name for t in tools.TOOLS]
    assert all(t["type"] == "function" and t["function"]["parameters"]["additionalProperties"] is False for t in body["tools"])

    assert turn.stop_reason == "tool_use"
    assert turn.message == {"role": "assistant", "content": None, "tool_calls": [CALL]}
    assert turn.tool_calls == [{"id": "call_1", "name": "list_services", "arguments": '{"query": "yoga"}'}]
    assert turn.input_tokens == 500 and turn.output_tokens == 40  # cached prompt tokens don't count


def test_a_final_answer_and_extra_fields_are_left_out(monkeypatch) -> None:
    m = model(chat_reasoning_effort="")
    reply = completion("Yoga is at 6 PM.", finish="stop", prompt_tokens=100, completion_tokens=10)
    reply._body["choices"][0]["message"]["reasoning"] = "the model's private reasoning"
    sent = fake_http(monkeypatch, m.client, [reply])
    turn = m.run_turn(system="s", tools=[], messages=MESSAGES)
    assert "reasoning_effort" not in sent[0][1]
    assert turn.stop_reason == "end_turn" and turn.text == "Yoga is at 6 PM."
    assert turn.message == {"role": "assistant", "content": "Yoga is at 6 PM."}  # nothing else is stored or resent


@pytest.mark.parametrize("finish, stop", [("length", "max_tokens"), ("content_filter", "refusal")])
def test_other_finish_reasons(monkeypatch, finish, stop) -> None:
    m = model()
    fake_http(monkeypatch, m.client, [completion("", finish=finish)])
    assert m.run_turn(system="s", tools=[], messages=MESSAGES).stop_reason == stop


def test_a_short_rate_limit_is_waited_out_and_a_long_one_is_reported(monkeypatch) -> None:
    m = model()
    answers = [FakeResponse(429, headers={"retry-after": "2"}), completion("Hi")]
    sent = fake_http(monkeypatch, m.client, answers)
    assert m.run_turn(system="s", tools=[], messages=MESSAGES).text == "Hi"
    assert len(sent) == 2

    # A wait of up to 20 s is made, twice at most; a longer one is reported at once.
    answers[:] = [FakeResponse(429, headers={"retry-after": "15"}), FakeResponse(429, headers={"retry-after": "15"}), completion("Hi again")]
    assert m.run_turn(system="s", tools=[], messages=MESSAGES).text == "Hi again"

    answers[:] = [FakeResponse(429, headers={"retry-after": "15"})] * 3
    with pytest.raises(RateLimited):
        m.run_turn(system="s", tools=[], messages=MESSAGES)

    answers[:] = [FakeResponse(429, headers={"retry-after": "3600"})]
    with pytest.raises(RateLimited) as limited:
        m.run_turn(system="s", tools=[], messages=MESSAGES)
    assert limited.value.retry_after == 3600


def test_server_errors_are_retried_once_and_bad_tool_calls_are_reported(monkeypatch) -> None:
    m = model()
    answers = [FakeResponse(503), completion("Hi")]
    fake_http(monkeypatch, m.client, answers)
    assert m.run_turn(system="s", tools=[], messages=MESSAGES).text == "Hi"

    answers[:] = [FakeResponse(400, {"error": {"code": "tool_use_failed", "message": "Failed to call a function", "failed_generation": "..."}})]
    with pytest.raises(ToolCallFailed):
        m.run_turn(system="s", tools=[], messages=MESSAGES)


def test_the_stored_message_is_valid_to_send_back(monkeypatch) -> None:
    """A tool-calling reply, stored and sent again with its tool result, keeps the OpenAI shape."""
    m = model()
    fake_http(monkeypatch, m.client, [completion(None, [CALL], "tool_calls")])
    turn = m.run_turn(system="s", tools=[], messages=MESSAGES)
    history = [*MESSAGES, turn.message, {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"services": []})}]
    assert history[-2]["tool_calls"][0]["id"] == history[-1]["tool_call_id"]
    assert json.loads(history[-2]["tool_calls"][0]["function"]["arguments"]) == {"query": "yoga"}
