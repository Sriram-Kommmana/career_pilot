"""Shared schema building blocks."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for every response model.

    ``from_attributes`` lets a response be built from an ORM row.
    ``extra="forbid"`` on requests (see :class:`ApiRequest`) means a typo in a client
    payload is an error rather than a silently ignored field.
    """

    model_config = ConfigDict(from_attributes=True)


class ApiRequest(BaseModel):
    """Base for every request model. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page[ItemT](ApiModel):
    """A page of results, with enough information to build a pager."""

    items: list[ItemT]
    total: int = Field(description="Total rows matching the filter, ignoring pagination.")
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PaginationParams(ApiRequest):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0)] = 0


class ErrorDetail(ApiModel):
    code: str
    message: str
    correlation_id: str
    details: object | None = None


class ErrorResponse(ApiModel):
    """The single error envelope every failure uses."""

    error: ErrorDetail


class MessageResponse(ApiModel):
    message: str


class HealthResponse(ApiModel):
    status: str
    version: str
    environment: str
    database: str = Field(description='"ok" or "unreachable".')
