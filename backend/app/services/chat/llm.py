"""The model that talks to the customer and calls the assistant's tools."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings, get_settings

# Models whose refusals the API can retry on another model server-side.
_SERVER_FALLBACK_PREFIXES = ("claude-opus-", "claude-sonnet-5-5", "claude-fable-")


@dataclass
class ModelTurn:
    # The response's content blocks as plain JSON, exactly as they are sent back
    # on later requests (thinking blocks included, unchanged).
    content: list[dict[str, Any]]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_uses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(b["text"].strip() for b in self.content if b.get("type") == "text" and b.get("text", "").strip())


class ChatModel(Protocol):
    def run_turn(self, *, system: list[dict], tools: list[dict], messages: list[dict]) -> ModelTurn: ...


class AnthropicChatModel:
    def __init__(self, settings: Settings | None = None, http_client=None) -> None:
        import anthropic  # imported here so the rest of the app never needs it

        self.settings = settings or get_settings()
        self.client = anthropic.Anthropic(
            api_key=self.settings.anthropic_api_key, timeout=60.0, max_retries=2, http_client=http_client
        )

    def run_turn(self, *, system: list[dict], tools: list[dict], messages: list[dict]) -> ModelTurn:
        s = self.settings
        kwargs: dict[str, Any] = dict(
            model=s.chat_model,
            max_tokens=s.chat_max_output_tokens,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": s.chat_effort},
            # Caches the tools, the system prompt and the conversation so far.
            cache_control={"type": "ephemeral"},
        )
        betas: list[str] = []
        if s.chat_model.startswith(_SERVER_FALLBACK_PREFIXES):
            # A declined request is retried on the model Anthropic recommends for it.
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"
        response = self.client.beta.messages.create(**kwargs, betas=betas)
        content = [block.model_dump(mode="json", by_alias=True, exclude_none=True) for block in response.content]
        usage = response.usage
        return ModelTurn(
            content=content,
            stop_reason=response.stop_reason or "end_turn",
            # What the daily limit counts: everything except cheap cache reads.
            input_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=usage.output_tokens or 0,
            tool_uses=[b for b in content if b.get("type") == "tool_use"],
        )


def get_chat_model() -> ChatModel | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    return AnthropicChatModel(settings)
