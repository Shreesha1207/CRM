from typing import Any


class AppError(Exception):
    """An expected failure that maps to a structured HTTP error response."""

    status_code = 400

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code


class NotFound(AppError):
    status_code = 404

    def __init__(self, entity: str) -> None:
        super().__init__("NOT_FOUND", f"{entity} not found")


class Forbidden(AppError):
    status_code = 403

    def __init__(self, message: str = "You do not have permission to do that") -> None:
        super().__init__("FORBIDDEN", message)


class BookingRejected(AppError):
    """A booking rule stopped the request (clash, capacity, availability...)."""

    status_code = 409
