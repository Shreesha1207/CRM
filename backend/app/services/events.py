"""Tiny in-process domain event bus.

The booking engine publishes what happened; other modules (notifications)
subscribe. Handlers run inside the publisher's transaction, so the records
they write (e.g. queued notifications) commit or roll back with the booking
change itself -- a transactional outbox without extra infrastructure.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DomainEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[Session, DomainEvent], None]
_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(name: str, handler: Handler) -> None:
    if handler not in _handlers[name]:
        _handlers[name].append(handler)


def publish(db: Session, name: str, **payload: Any) -> None:
    event = DomainEvent(name, payload)
    for handler in _handlers.get(name, []):
        handler(db, event)
