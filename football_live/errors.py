from __future__ import annotations

from uuid import UUID

from pydantic import Field

from .domain import StrictDomainModel


class ApiError(Exception):
    """An intentionally small, client-safe API error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class ErrorDetail(StrictDomainModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    request_id: UUID


class ErrorEnvelope(StrictDomainModel):
    error: ErrorDetail


def error_body(
    status_code: int,
    code: str,
    message: str,
    request_id: UUID,
) -> tuple[int, dict]:
    return (
        status_code,
        ErrorEnvelope(
            error=ErrorDetail(
                code=code,
                message=message,
                request_id=request_id,
            )
        ).model_dump(mode="json"),
    )
