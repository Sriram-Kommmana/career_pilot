"""Binding an approval to the exact thing that was approved.

An approval is not a flag saying "the user said yes once". It is a signature over a
snapshot: every field value, every uploaded document, and every answer the user
actually saw. :func:`compute_form_state_hash` produces that signature.

If anything in the form changes afterwards — the agent refills a field, the user edits
an answer, a different resume is selected — the hash changes, the stored approval no
longer matches, and the submission gate reports ``APPROVAL_STALE``. The user reviews
again. That is safety requirement 3, implemented as an equality check rather than as a
rule someone has to remember.

The hash is deliberately insensitive to ordering and to the kind of whitespace a
browser normalises anyway, so re-reading the same form twice does not spuriously
invalidate a valid approval.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

#: Bumping this invalidates every stored approval, forcing a fresh human review.
#: That is the safe direction, so the version is part of the hash input.
FORM_STATE_HASH_VERSION: Final = "v1"


class FieldResolution(StrEnum):
    """Whether a form field is settled."""

    #: Filled from an authoritative fact and verified against the live page.
    RESOLVED = "RESOLVED"
    #: Recognised, but no authoritative value exists. A human must answer it.
    NEEDS_HUMAN = "NEEDS_HUMAN"
    #: Present on the page but not understood by any adapter.
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    #: Deliberately left blank; the field is optional and the user skipped it.
    SKIPPED = "SKIPPED"


class VerificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    #: The field was filled but could not be read back, e.g. a write-only widget.
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True, slots=True)
class FieldSnapshot:
    """One form field, as it stood when the user reviewed it.

    ``value_for_hash`` is what goes into the signature. For a sensitive field the
    caller passes a digest rather than the value itself, so the plaintext never needs
    to exist outside the browser session — the hash still changes if the value does.
    """

    field_id: str
    label: str
    resolution: FieldResolution
    required: bool
    value_for_hash: str | None
    verification: VerificationStatus = VerificationStatus.NOT_ATTEMPTED
    is_sensitive: bool = False

    @property
    def is_blocking(self) -> bool:
        """Does this field prevent submission?"""
        if self.resolution in (FieldResolution.NEEDS_HUMAN, FieldResolution.UNKNOWN_FIELD):
            return True
        if self.required and self.resolution is FieldResolution.SKIPPED:
            return True
        return self.required and self.verification is not VerificationStatus.PASSED


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    """An uploaded file, identified by content digest rather than by filename.

    Swapping a resume for a different file with the same name must invalidate the
    approval, so the digest is what matters.
    """

    slot: str
    filename: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class AnswerSnapshot:
    """A free-text answer the user reviewed, with where its content came from."""

    question_key: str
    value_sha256: str
    provenance: str


@dataclass(frozen=True, slots=True)
class FormSnapshot:
    """Everything a human saw on the review screen."""

    application_url: str
    fields: tuple[FieldSnapshot, ...] = field(default_factory=tuple)
    documents: tuple[DocumentSnapshot, ...] = field(default_factory=tuple)
    answers: tuple[AnswerSnapshot, ...] = field(default_factory=tuple)

    @property
    def blocking_fields(self) -> tuple[FieldSnapshot, ...]:
        return tuple(f for f in self.fields if f.is_blocking)


def _canonical_value(value: str | None) -> str | None:
    """Normalise a value for hashing.

    Leading and trailing whitespace is stripped and internal runs of whitespace are
    collapsed, because browsers and ATS widgets do that themselves on read-back. Case
    is preserved: "Yes" and "yes" could be different option values.
    """
    if value is None:
        return None
    return " ".join(value.split())


def compute_form_state_hash(snapshot: FormSnapshot) -> str:
    """A stable digest of a reviewed form.

    Order-independent: fields, documents and answers are sorted by their keys, so the
    order an adapter happens to discover them in does not change the result.
    """
    fields_payload = sorted(
        (
            {
                "field_id": f.field_id,
                "label": _canonical_value(f.label),
                "resolution": f.resolution.value,
                "required": f.required,
                "value": _canonical_value(f.value_for_hash),
                "verification": f.verification.value,
                "sensitive": f.is_sensitive,
            }
            for f in snapshot.fields
        ),
        key=lambda item: str(item["field_id"]),
    )
    documents_payload = sorted(
        (
            {"slot": d.slot, "filename": d.filename, "sha256": d.content_sha256}
            for d in snapshot.documents
        ),
        key=lambda item: (item["slot"], item["sha256"]),
    )
    answers_payload = sorted(
        (
            {
                "question_key": a.question_key,
                "sha256": a.value_sha256,
                "provenance": a.provenance,
            }
            for a in snapshot.answers
        ),
        key=lambda item: item["question_key"],
    )

    payload = {
        "version": FORM_STATE_HASH_VERSION,
        "application_url": snapshot.application_url.strip(),
        "fields": fields_payload,
        "documents": documents_payload,
        "answers": answers_payload,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def digest_value(value: str) -> str:
    """Digest a sensitive value so it can be hashed without being stored.

    Not a password hash and not intended to be one: this is a change-detector for
    form contents, and the values it covers are things like a phone number that the
    user themselves supplied.
    """
    return hashlib.sha256(_canonical_value(value).encode("utf-8")).hexdigest()  # type: ignore[union-attr]


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """A human's recorded consent to submit one specific form state."""

    application_id: str
    approved_by_user_id: str
    approved_at: datetime
    form_state_hash: str
    #: Free-text note the user may leave, e.g. "checked the salary question myself".
    note: str | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


def approval_matches(
    approval: ApprovalRecord | None,
    *,
    current_form_state_hash: str,
) -> bool:
    """Whether an approval still covers the form as it stands now."""
    if approval is None or not approval.is_active:
        return False
    return approval.form_state_hash == current_form_state_hash


def changed_fields(
    *,
    approved: FormSnapshot,
    current: FormSnapshot,
) -> list[str]:
    """Which fields differ between the approved snapshot and the current one.

    Used to tell the user *what* changed when their approval is invalidated, rather
    than just that it was.
    """

    def index(snapshot: FormSnapshot) -> dict[str, FieldSnapshot]:
        return {f.field_id: f for f in snapshot.fields}

    before, after = index(approved), index(current)
    differences: list[str] = []

    for field_id in sorted(set(before) | set(after)):
        old, new = before.get(field_id), after.get(field_id)
        if old is None:
            differences.append(f"{field_id}: added")
        elif new is None:
            differences.append(f"{field_id}: removed")
        elif _canonical_value(old.value_for_hash) != _canonical_value(new.value_for_hash):
            differences.append(f"{field_id}: value changed")
        elif old.resolution is not new.resolution:
            differences.append(f"{field_id}: {old.resolution.value} -> {new.resolution.value}")
        elif old.verification is not new.verification:
            differences.append(
                f"{field_id}: verification {old.verification.value} -> {new.verification.value}"
            )

    before_docs = {(d.slot, d.content_sha256) for d in approved.documents}
    after_docs = {(d.slot, d.content_sha256) for d in current.documents}
    for slot, _ in sorted(before_docs - after_docs):
        differences.append(f"document[{slot}]: removed or replaced")
    for slot, _ in sorted(after_docs - before_docs):
        differences.append(f"document[{slot}]: added or replaced")

    before_answers = {(a.question_key, a.value_sha256) for a in approved.answers}
    after_answers = {(a.question_key, a.value_sha256) for a in current.answers}
    for key, _ in sorted(before_answers ^ after_answers):
        marker = f"answer[{key}]: changed"
        if marker not in differences:
            differences.append(marker)

    return differences


def snapshot_from_pairs(
    *,
    application_url: str,
    pairs: Iterable[tuple[str, str, str | None]],
    required_field_ids: Sequence[str] = (),
) -> FormSnapshot:
    """Build a snapshot from ``(field_id, label, value)`` triples.

    A convenience for tests and for the manual-entry path, where there is no browser
    and every supplied value came straight from the user.
    """
    required = set(required_field_ids)
    fields = tuple(
        FieldSnapshot(
            field_id=field_id,
            label=label,
            resolution=(
                FieldResolution.RESOLVED if value is not None else FieldResolution.NEEDS_HUMAN
            ),
            required=field_id in required,
            value_for_hash=value,
            verification=(
                VerificationStatus.PASSED if value is not None else VerificationStatus.NOT_ATTEMPTED
            ),
        )
        for field_id, label, value in pairs
    )
    return FormSnapshot(application_url=application_url, fields=fields)
