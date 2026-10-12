"""Provenance: where every candidate fact came from.

CareerPilot does not invent anything about a candidate. The mechanism behind that
promise is simple and enforced in one place: a value that will be typed into an
employer's form must carry a :class:`ProvenanceKind` that is *authoritative*. If it
does not, the field becomes ``NEEDS_HUMAN`` and a person fills it in.

There is no "best guess" path and no confidence threshold that lets a generated value
through. ``GENERATED_UNVERIFIED`` exists so drafted prose can be stored and shown as a
draft; :func:`is_authoritative` returns ``False`` for it, and the submission gate
rejects any required field resting on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID


class ProvenanceKind(StrEnum):
    """How a fact came to be known."""

    #: A profile field the user entered or explicitly confirmed.
    USER_CONFIRMED_PROFILE = "USER_CONFIRMED_PROFILE"
    #: Extracted from a resume **and then confirmed by the user**. Raw, unconfirmed
    #: extraction output is never authoritative; see RESUME_EXTRACTION_UNCONFIRMED.
    RESUME_EXTRACTION_CONFIRMED = "RESUME_EXTRACTION_CONFIRMED"
    #: A project or experience record the user approved as evidence.
    USER_APPROVED_EVIDENCE = "USER_APPROVED_EVIDENCE"
    #: An answer the user approved on an earlier application, reused verbatim.
    PREVIOUSLY_APPROVED_ANSWER = "PREVIOUSLY_APPROVED_ANSWER"
    #: The user typed this value into CareerPilot for this specific application.
    USER_DIRECT_INPUT = "USER_DIRECT_INPUT"

    # --- not authoritative -------------------------------------------------
    #: Pulled out of a resume but not yet shown to and confirmed by the user.
    RESUME_EXTRACTION_UNCONFIRMED = "RESUME_EXTRACTION_UNCONFIRMED"
    #: Model-written prose, e.g. a cover letter draft. Always labelled as a draft in
    #: the UI and never sufficient for a required field.
    GENERATED_UNVERIFIED = "GENERATED_UNVERIFIED"
    #: The value is genuinely unknown. Carried explicitly rather than left empty, so
    #: "we don't know" is distinguishable from "nobody looked".
    UNKNOWN = "UNKNOWN"


#: Kinds that may be typed into an employer's form without further human action.
AUTHORITATIVE_KINDS: Final[frozenset[ProvenanceKind]] = frozenset(
    {
        ProvenanceKind.USER_CONFIRMED_PROFILE,
        ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
        ProvenanceKind.USER_APPROVED_EVIDENCE,
        ProvenanceKind.PREVIOUSLY_APPROVED_ANSWER,
        ProvenanceKind.USER_DIRECT_INPUT,
    }
)


def is_authoritative(kind: ProvenanceKind) -> bool:
    """Whether a fact with this provenance may be submitted to an employer."""
    return kind in AUTHORITATIVE_KINDS


class SensitiveCategory(StrEnum):
    """Categories that are never inferred, defaulted, or guessed.

    Even when a plausible answer could be computed, these are left to the user. A
    wrong answer to a work-authorisation or demographic question is a
    misrepresentation, not a UX inconvenience.
    """

    WORK_AUTHORISATION = "WORK_AUTHORISATION"
    VISA_SPONSORSHIP = "VISA_SPONSORSHIP"
    SALARY_HISTORY = "SALARY_HISTORY"
    SALARY_EXPECTATION = "SALARY_EXPECTATION"
    DEMOGRAPHIC = "DEMOGRAPHIC"
    DISABILITY = "DISABILITY"
    VETERAN_STATUS = "VETERAN_STATUS"
    CRIMINAL_HISTORY = "CRIMINAL_HISTORY"
    SECURITY_CLEARANCE = "SECURITY_CLEARANCE"
    REFERENCES = "REFERENCES"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    GOVERNMENT_ID = "GOVERNMENT_ID"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A pointer to the stored thing that supports a fact.

    ``locator`` is deliberately free-form but must be specific enough for a human to
    check: ``"resume:page=2,section=Experience"``, ``"project:redis-clone"``.
    """

    kind: str
    id: UUID | None = None
    locator: str | None = None
    excerpt: str | None = None

    def describe(self) -> str:
        parts = [self.kind]
        if self.locator:
            parts.append(self.locator)
        elif self.id is not None:
            parts.append(str(self.id))
        return ":".join(parts)


@dataclass(frozen=True, slots=True)
class Fact:
    """A single candidate fact together with where it came from.

    Constructing a ``Fact`` requires a provenance kind, so there is no way to produce
    one that has lost track of its source.
    """

    key: str
    value: str | None
    provenance: ProvenanceKind
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    sensitive_category: SensitiveCategory | None = None
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.provenance == ProvenanceKind.UNKNOWN and self.value is not None:
            raise ValueError(
                f"Fact {self.key!r} is marked UNKNOWN but carries a value. "
                "Use a real provenance kind or set value to None."
            )

    @property
    def is_usable(self) -> bool:
        """May this be typed into a form without asking the user again?"""
        return (
            self.value is not None
            and self.value.strip() != ""
            and is_authoritative(self.provenance)
        )

    @property
    def needs_human(self) -> bool:
        return not self.is_usable

    def evidence_summary(self) -> list[str]:
        return [ref.describe() for ref in self.evidence]


def unknown_fact(key: str, *, sensitive_category: SensitiveCategory | None = None) -> Fact:
    """A fact we do not know. Explicit, so it can be surfaced rather than skipped."""
    return Fact(
        key=key,
        value=None,
        provenance=ProvenanceKind.UNKNOWN,
        sensitive_category=sensitive_category,
    )


def require_authoritative(fact: Fact) -> str:
    """Return the value, or raise if this fact must not be used.

    Call this at the point where a value is about to be written into a form field, so
    a non-authoritative fact cannot slip through by accident.
    """
    if not fact.is_usable:
        raise ValueError(
            f"Fact {fact.key!r} has provenance {fact.provenance} and cannot be "
            "submitted to an employer. It must be resolved by the user."
        )
    if fact.value is None:  # pragma: no cover - narrowed by is_usable
        raise ValueError(f"Fact {fact.key!r} has no value.")
    return fact.value
