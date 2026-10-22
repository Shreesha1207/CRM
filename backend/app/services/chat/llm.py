"""The model that talks to the customer and calls the assistant's tools (on Groq)."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.services.chat.groq import GroqClient

_STOP_REASONS = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


@dataclass
class ModelTurn:
    # The assistant message as it is stored and sent back on later requests:
    # role, content and (when the model called tools) tool_calls.
    message: dict[str, Any]
    stop_reason: str  # "tool_use", "end_turn", "max_tokens" or "refusal"
    input_tokens: int = 0
    output_tokens: int = 0
    # Each call as {"id", "name", "arguments"}; arguments is the raw JSON text.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return (self.message.get("content") or "").strip()


class ChatModel(Protocol):
    def run_turn(self, *, system: str, tools: list[dict], messages: list[dict]) -> ModelTurn: ...


class GroqChatModel:
    def __init__(self, settings: Settings | None = None, client: GroqClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client or GroqClient(self.settings)

    def run_turn(self, *, system: str, tools: list[dict], messages: list[dict]) -> ModelTurn:
        s = self.settings
        extra: dict[str, Any] = dict(
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=True,
            temperature=0.2,
            max_completion_tokens=s.chat_max_output_tokens,
        )
        if s.chat_reasoning_effort:
            extra["reasoning_effort"] = s.chat_reasoning_effort
        # The fixed system prompt first, so its tokens are cached from one request to the next.
        data = self.client.chat(
            s.chat_model, [{"role": "system", "content": system}, *messages], timeout=s.chat_timeout_seconds, **extra
        )
        choice = data["choices"][0]
        reply = choice.get("message") or {}
        calls = reply.get("tool_calls") or []
        message: dict[str, Any] = {"role": "assistant", "content": reply.get("content")}
        if calls:
            message["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["function"]["name"], "arguments": c["function"].get("arguments") or "{}"},
                }
                for c in calls
            ]
        stop = _STOP_REASONS.get(choice.get("finish_reason") or "", "end_turn")
        if calls and stop == "end_turn":
            stop = "tool_use"
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return ModelTurn(
            message=message,
            stop_reason=stop,
            # What the daily limit counts: cached prompt tokens are left out, as Groq does.
            input_tokens=max(0, (usage.get("prompt_tokens") or 0) - cached),
            output_tokens=usage.get("completion_tokens") or 0,
            tool_calls=[
                {"id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                for c in message.get("tool_calls", [])
            ],
        )


def get_chat_model() -> ChatModel | None:
    settings = get_settings()
    if not settings.chat_configured:
        return None
    return GroqChatModel(settings)
