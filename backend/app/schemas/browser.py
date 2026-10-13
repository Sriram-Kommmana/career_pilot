"""Phase 5's browser-assistance contracts.

``mapping_token`` is Approval A — see ``app.services.browser_fill``'s module
docstring for why it is deliberately not the submission gate's
``ApprovalRecordRow``/``form_state_hash``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from app.schemas.common import ApiModel, ApiRequest


class ExtractedFieldOut(ApiModel):
    field_id: str
    label: str | None
    field_kind: str
    required: bool
    unrecognised: bool


class ProposedMappingOut(ApiModel):
    field_id: str
    label: str | None
    field_kind: str
    required: bool
    proposed_value: str | None
    source_kind: str | None
    source_id: str | None
    source_provenance: str | None
    confidence: str
    rule_id: str | None
    needs_review: bool
    supported_for_fill: bool
    sensitive_category: str | None
    reason: str


class BrowserSessionOut(ApiModel):
    session_id: str
    origin: str


class InspectionResultOut(ApiModel):
    session_id: str
    origin: str
    supported: bool
    reason: str | None
    fields: list[ExtractedFieldOut] = Field(default_factory=list)
    mappings: list[ProposedMappingOut] = Field(default_factory=list)
    #: Required back on the fill request — proves the user reviewed *this*
    #: exact proposal, not an earlier or different one.
    mapping_token: str | None


class FillRequest(ApiRequest):
    mapping_token: Annotated[str, Field(min_length=64, max_length=64)]
    #: Field ids the user explicitly excluded from this fill, even though a
    #: value was proposed for them.
    excluded_field_ids: list[str] = Field(default_factory=list)


class FieldFillOutcomeOut(ApiModel):
    field_id: str
    action_result: str
    verification: str
    verification_note: str | None


class FillValidationReportOut(ApiModel):
    session_id: str
    outcomes: list[FieldFillOutcomeOut] = Field(default_factory=list)
    filled_count: int
    failed_count: int
    still_needs_human: list[str] = Field(default_factory=list)
    application_state: str
    verification_status: str
