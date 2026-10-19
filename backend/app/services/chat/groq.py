"""A small client for Groq's OpenAI-compatible chat API, shared by the assistant's
model and its guards. Any OpenAI-compatible server (e.g. Ollama) works too."""

import logging
import time
from contextvars import ContextVar
from typing import Any

import requests

from app.core.config import Settings, get_settings

log = logging.getLogger("chat.groq")

# A rate limit asking us to wait longer than this ends the turn instead; up to
# MAX_RATE_LIMIT_WAITS waits are made for one request.
MAX_WAIT_SECONDS = 20.0
MAX_RATE_LIMIT_WAITS = 2

# The rate-limit waits of the current chat turn, for its timing log (None outside a turn).
rate_limit_waits: ContextVar[list[float] | None] = ContextVar("rate_limit_waits", default=None)


class GroqError(Exception):
    pass


class RateLimited(GroqError):
    """A request or token limit was hit; `retry_after` says how long until it resets."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited for {retry_after:.0f} s")
        self.retry_after = retry_after


class ToolCallFailed(GroqError):
    """The model produced a tool call the server could not parse (`tool_use_failed`).
    `failed_generation` is what the model wrote, for the logs."""

    def __init__(self, message: str, failed_generation: str = "") -> None:
        super().__init__(message)
        self.failed_generation = failed_generation


def _retry_after(response: requests.Response) -> float:
    for header in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = response.headers.get(header)
        if value:
            try:
                return float(value.rstrip("s"))
            except ValueError:
                continue
    return 60.0


def _error(response: requests.Response) -> tuple[str, str, str]:
    """The error's code, message and (for a rejected tool call) what the model wrote."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return "", response.text[:200], ""
    return str(error.get("code") or ""), str(error.get("message") or "")[:300], str(error.get("failed_generation") or "")[:500]


class GroqClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.session = requests.Session()

    def chat(self, model: str, messages: list[dict], *, timeout: float, **extra: Any) -> dict:
        url = self.settings.groq_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}"}
        body = {"model": model, "messages": messages, **extra}
        last: Exception | None = None
        attempts = waits = 0
        while attempts < 2:  # one retry after an error
            try:
                response = self.session.post(url, json=body, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                last = exc
                attempts += 1
                time.sleep(0.3)
                continue
            if response.status_code == 429:
                wait = _retry_after(response)
                if waits < MAX_RATE_LIMIT_WAITS and wait <= MAX_WAIT_SECONDS:
                    waits += 1
                    log.info("Rate limited by %s, waiting %.1f s", model, wait)
                    if (waited := rate_limit_waits.get()) is not None:
                        waited.append(wait)
                    time.sleep(wait)
                    continue
                raise RateLimited(wait)
            if response.status_code >= 500:
                last = GroqError(f"{model} returned {response.status_code}")
                attempts += 1
                time.sleep(0.3)
                continue
            if not response.ok:
                code, message, written = _error(response)
                if code == "tool_use_failed":
                    raise ToolCallFailed(message, written)
                raise GroqError(f"{model} returned {response.status_code}: {code} {message}")
            try:
                return response.json()
            except ValueError as exc:
                last = exc
                attempts += 1
        raise GroqError(str(last))
