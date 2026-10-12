"""Resume contracts: upload, the staged extraction proposal, and confirmation.

The shape of :class:`ExtractionItemOut` deliberately mirrors
``app.domain.resume_extraction.ExtractedItem`` field for field — the API response is
exactly what the extractor produced, with nothing added and nothing hidden. ``index``
is the one thing the domain dataclass doesn't carry: it is assigned when a proposal is
serialised, and the client uses it to refer back to a specific item when confirming.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import ApiModel, ApiRequest

ItemField = Literal["skill", "education", "project", "experience", "certification", "achievement"]
Confidence = Literal["high", "medium", "low"]


class ResumeOut(ApiModel):
    id: uuid.UUID
    label: str
    original_filename: str
    content_type: str
    size_bytes: int
    content_sha256: str
    is_default: bool
    #: "pending" | "succeeded" | "failed". Separate from extraction_confirmed_at,
    #: which only tracks whether the user has acted on a staged proposal.
    extraction_status: str
    extraction_error: str | None
    #: Until this is set, facts extracted from the file are not authoritative.
    extraction_confirmed_at: datetime | None
    created_at: datetime


class ExtractionItemOut(ApiModel):
    index: int
    field: ItemField
    data: dict[str, object]
    source_excerpt: str
    locator: str
    confidence: Confidence
    needs_review: bool
    #: Set when this item looks like it might describe the same education,
    #: experience, or certification record already on the profile. Advisory only —
    #: confirming never merges unless the request names this id explicitly.
    possible_duplicate_of: uuid.UUID | None = None


class ExtractionProposalOut(ApiModel):
    resume_id: uuid.UUID
    extraction_status: str
    items: list[ExtractionItemOut] = Field(default_factory=list)
    unparsed_sections: list[str] = Field(default_factory=list)


class ExtractionDecision(ApiRequest):
    """One item from the last staged proposal, and what to do with it.

    ``existing_record_id``, when set, is the only way an accepted item may update a
    row that already exists rather than creating a new one — silence always means
    "add as new" (or, for a skill that already exists, "leave it alone"), never
    "replace what's there".
    """

    index: Annotated[int, Field(ge=0)]
    accept: bool
    existing_record_id: uuid.UUID | None = None


class ConfirmExtractionRequest(ApiRequest):
    decisions: Annotated[list[ExtractionDecision], Field(min_length=1, max_length=200)]


class ConfirmExtractionResultOut(ApiModel):
    index: int
    field: ItemField
    status: Literal["created", "updated", "skipped", "rejected"]
    record_id: uuid.UUID | None = None
    message: str | None = None


class ConfirmExtractionResponse(ApiModel):
    resume_id: uuid.UUID
    results: list[ConfirmExtractionResultOut] = Field(default_factory=list)
