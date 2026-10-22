"""Stand-ins for the assistant's model and guards, so chat tests never touch the network."""

import copy
import itertools
import json
from collections.abc import Callable
from typing import Any

import pytest

from app.core.config import get_settings
from app.main import app
from app.services.chat.guard import GuardClient, GuardError, get_guard
from app.services.chat.llm import ModelTurn, get_chat_model

_ids = itertools.count(1)


def say(text: str) -> ModelTurn:
    """A final answer."""
    return ModelTurn(message={"role": "assistant", "content": text}, stop_reason="end_turn", input_tokens=100, output_tokens=20)


def call_raw(*calls: tuple[str, str]) -> ModelTurn:
    """Tool calls with their arguments exactly as the model wrote them (JSON text)."""
    tool_calls = [
        {"id": f"call_{next(_ids)}", "type": "function", "function": {"name": name, "arguments": arguments}}
        for name, arguments in calls
    ]
    return ModelTurn(
        message={"role": "assistant", "content": None, "tool_calls": tool_calls},
        stop_reason="tool_use",
        input_tokens=100,
        output_tokens=20,
        tool_calls=[{"id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in tool_calls],
    )


def call(name: str, input: dict[str, Any] | None = None, *more: tuple[str, dict[str, Any]]) -> ModelTurn:
    """One or more tool calls in a single response."""
    return call_raw(*[(n, json.dumps(i or {})) for n, i in [(name, input), *more]])


def last_tool_results(messages: list[dict]) -> list[dict]:
    """The JSON results of the tool calls answered just before this request."""
    results = []
    for message in reversed(messages):
        if message["role"] != "tool":
            break
        results.insert(0, json.loads(message["content"]))
    return results


Step = ModelTurn | Callable[[list[dict]], ModelTurn]


class FakeModel:
    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []

    def run_turn(self, *, system, tools, messages) -> ModelTurn:
        self.requests.append({"system": system, "tools": tools, "messages": copy.deepcopy(messages)})
        if not self.steps:
            raise AssertionError("the model was called more often than scripted")
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


class FakeGuard(GuardClient):
    """The real guard logic (rule checks, order, parsing, fail closed) over scripted answers."""

    def __init__(self, injection: str = "0.01", policy: str = "ALLOW", reply_policy: str = "ALLOW", down: bool = False) -> None:
        super().__init__(get_settings())
        self.injection, self.policy_answer, self.reply_policy, self.down = injection, policy, reply_policy, down
        self.calls: list[tuple[str, str]] = []

    def _complete(self, model: str, messages: list[dict], **extra) -> str:
        kind = "injection" if model == self.settings.guard_injection_model else (
            "reply" if "<assistant_reply>" in messages[-1]["content"] else "message"
        )
        self.calls.append((kind, messages[-1]["content"]))
        if self.down:
            raise GuardError("guard is down")
        return {"injection": self.injection, "message": self.policy_answer, "reply": self.reply_policy}[kind]


class FakeResponse:
    """What requests returns, for tests of the Groq client."""

    def __init__(self, status: int = 200, body: dict | None = None, headers: dict | None = None) -> None:
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.ok = status < 400
        self.text = json.dumps(self._body)

    def json(self) -> dict:
        return self._body


def completion(content: str | None = None, tool_calls: list | None = None, finish: str = "stop", **usage) -> FakeResponse:
    message = {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}
    return FakeResponse(200, {"choices": [{"message": message, "finish_reason": finish}], "usage": usage})


def fake_http(monkeypatch, groq_client, answers: list[FakeResponse]) -> list[tuple[str, dict, dict]]:
    """Answer the client's requests from `answers`, in order; returns what was sent."""
    sent: list[tuple[str, dict, dict]] = []

    def post(url, json, headers, timeout):
        sent.append((url, json, headers))
        return answers.pop(0)

    monkeypatch.setattr(groq_client.session, "post", post)
    monkeypatch.setattr("app.services.chat.groq.time.sleep", lambda _: None)
    return sent


def events(response) -> list[tuple[str, dict]]:
    """The server-sent events of a streamed reply."""
    assert response.status_code == 200, response.text
    out = []
    for chunk in response.text.split("\n\n"):
        lines = [line for line in chunk.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        out.append((name, json.loads(data)))
    return out


def reply_of(response) -> dict:
    replies = [data for name, data in events(response) if name == "reply"]
    assert len(replies) == 1, events(response)
    return replies[0]


@pytest.fixture
def settings(monkeypatch):
    """Change process settings for one test."""
    def change(**values):
        for key, value in values.items():
            monkeypatch.setattr(get_settings(), key, value)

    return change


class Chat:
    def __init__(self, client, headers) -> None:
        self.client, self.headers = client, headers
        self.guard = FakeGuard()
        self.model = FakeModel()
        app.dependency_overrides[get_chat_model] = lambda: self.model
        app.dependency_overrides[get_guard] = lambda: self.guard
        assert client.post("/api/chat/subscription", headers=headers).json()["active"]
        self.conversation = client.post("/api/chat/conversations", headers=headers).json()["id"]

    def send(self, text: str, *steps, guard: FakeGuard | None = None):
        self.model.steps.extend(steps)
        if guard is not None:
            self.guard = guard
        return self.client.post(f"/api/chat/conversations/{self.conversation}/messages", headers=self.headers, json={"text": text})

    def transcript(self) -> list[dict]:
        return self.client.get(f"/api/chat/conversations/{self.conversation}", headers=self.headers).json()["items"]

    def confirm(self, card_id: str):
        return self.client.post(f"/api/chat/proposals/{card_id}/confirm", headers=self.headers)

    def decline(self, card_id: str):
        return self.client.post(f"/api/chat/proposals/{card_id}/decline", headers=self.headers)


@pytest.fixture
def chat(client, user_headers):
    yield Chat(client, user_headers)
    app.dependency_overrides.clear()
