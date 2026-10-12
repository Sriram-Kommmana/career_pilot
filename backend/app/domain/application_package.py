"""Deterministic generation of a job-specific application package.

Pure, no I/O — same layering as ``app.domain.resume_extraction``: every input here
is a plain dataclass the caller already assembled from confirmed data, and every
output is a plain dataclass. Nothing here touches the database, the network, or a
model. ``app.services.application_package`` is the one seam where a future LLM
provider would plug in (mirroring ``Settings.llm_provider``'s existing
``Literal["none", "anthropic", "openai"]`` from Phase 3) — not built now, since
wiring a paid provider is out of scope, but swapping the deterministic functions
below for a provider-backed version would not require touching this module's
callers.

The one rule every function here is written to honour: never state a specific
(a company, a skill, a highlight) that was not in the confirmed input. When there
is nothing to cite, the result says so plainly (``needs_human=True``) rather than
writing something generic that reads like a fact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Final, Literal

from app.domain.provenance import EvidenceRef

ReadinessStatus = Literal["ready", "needs_attention", "missing"]

#: Fixed, reviewable set of application-question drafts this phase generates.
#: Deliberately not LLM-chosen — see the module docstring.
QUESTION_TEXTS: Final[dict[str, str]] = {
    "why_interested": "Why are you interested in this role?",
    "relevant_experience": "Describe your most relevant experience for this role.",
}

_INSUFFICIENT_EVIDENCE_TEXT: Final[str] = (
    "There isn't enough confirmed profile or resume evidence yet to draft this. "
    "Add confirmed skills, projects, or experience to your profile, then "
    "regenerate."
)


@dataclass(frozen=True, slots=True)
class ConfirmedExperienceItem:
    """One confirmed ``Project`` or ``ExperienceRecord``, as plain data."""

    kind: Literal["project", "experience"]
    id: uuid.UUID
    title: str
    organisation: str | None
    skills: frozenset[str]
    highlights: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ResumeGuidanceItem:
    """One confirmed record recommended for emphasis on this job."""

    kind: Literal["project", "experience"]
    id: uuid.UUID
    title: str
    organisation: str | None
    matched_skills: tuple[str, ...]
    highlights: tuple[str, ...]
    reason: str
    evidence_ref: EvidenceRef


@dataclass(frozen=True, slots=True)
class DraftContent:
    """One generated piece of prose, and whether it is trustworthy as-is."""

    text: str
    needs_human: bool
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    """One line of the readiness checklist."""

    category: Literal["profile", "requirements", "cover_letter", "questions"]
    label: str
    status: ReadinessStatus
    detail: str


def build_resume_guidance(
    *,
    candidate_items: tuple[ConfirmedExperienceItem, ...],
    job_requirement_skills: frozenset[str],
    limit: int = 5,
) -> tuple[ResumeGuidanceItem, ...]:
    """Rank confirmed projects/experience by overlap with the job's requirements.

    An item with no matched skill is never returned — recommending it would be a
    guess, not guidance. Ties break on title for a deterministic, stable order.
    """
    scored: list[tuple[int, ConfirmedExperienceItem, tuple[str, ...]]] = []
    for item in candidate_items:
        matched = tuple(sorted(item.skills & job_requirement_skills))
        if not matched:
            continue
        scored.append((len(matched), item, matched))

    scored.sort(key=lambda row: (-row[0], row[1].title))

    results: list[ResumeGuidanceItem] = []
    for _, item, matched in scored[:limit]:
        results.append(
            ResumeGuidanceItem(
                kind=item.kind,
                id=item.id,
                title=item.title,
                organisation=item.organisation,
                matched_skills=matched,
                highlights=item.highlights,
                reason=f"Matches {', '.join(matched)} from your confirmed {item.kind}.",
                evidence_ref=EvidenceRef(
                    kind=item.kind, id=item.id, locator=f"{item.kind}:{item.title}"
                ),
            )
        )
    return tuple(results)


def build_cover_letter_draft(
    *,
    full_name: str | None,
    job_title: str,
    company_name: str,
    best_match: ResumeGuidanceItem | None,
) -> DraftContent:
    """A deterministic cover-letter paragraph citing one confirmed record.

    Only ever cites the single best-matched record passed in — never invents a
    second one to sound more impressive than the evidence supports.
    """
    if best_match is None:
        return DraftContent(text=_INSUFFICIENT_EVIDENCE_TEXT, needs_human=True)

    name = full_name or "the candidate"
    skills_text = ", ".join(best_match.matched_skills)
    org_clause = f" at {best_match.organisation}" if best_match.organisation else ""
    paragraphs = [
        "Dear Hiring Team,",
        (
            f"I'm writing to apply for the {job_title} position at {company_name}. "
            f"In my {best_match.kind} as {best_match.title}{org_clause}, I worked "
            f"directly with {skills_text}, which this role also calls for."
        ),
    ]
    if best_match.highlights:
        paragraphs.append(best_match.highlights[0])
    paragraphs.append(
        f"I'd welcome the chance to discuss how this background could contribute "
        f"to {company_name}.\n\nSincerely,\n{name}"
    )
    return DraftContent(
        text="\n\n".join(paragraphs),
        needs_human=False,
        evidence_refs=(best_match.evidence_ref,),
    )


def build_question_draft(
    key: str,
    *,
    job_title: str,
    company_name: str,
    best_match: ResumeGuidanceItem | None,
) -> DraftContent:
    """A deterministic draft answer for one of ``QUESTION_TEXTS``'s fixed keys."""
    if key not in QUESTION_TEXTS:
        raise ValueError(f"Unknown question key: {key!r}")

    if best_match is None:
        return DraftContent(text=_INSUFFICIENT_EVIDENCE_TEXT, needs_human=True)

    skills_text = ", ".join(best_match.matched_skills)
    org_clause = f" at {best_match.organisation}" if best_match.organisation else ""

    if key == "why_interested":
        text = (
            f"I'm interested in the {job_title} role at {company_name} because it "
            f"builds directly on my experience with {skills_text}, which I "
            f"developed as {best_match.title}{org_clause}."
        )
    else:  # "relevant_experience"
        highlight = f" {best_match.highlights[0]}" if best_match.highlights else ""
        text = (
            f"As {best_match.title}{org_clause}, I worked with {skills_text}."
            f"{highlight}"
        )

    return DraftContent(
        text=text, needs_human=False, evidence_refs=(best_match.evidence_ref,)
    )


def build_readiness_checklist(
    *,
    outstanding_field_labels: tuple[str, ...],
    missing_requirement_texts: tuple[str, ...],
    uncertain_requirement_texts: tuple[str, ...],
    cover_letter_status: ReadinessStatus,
    question_statuses: tuple[tuple[str, ReadinessStatus], ...],
) -> tuple[ReadinessItem, ...]:
    """Aggregate existing signals into one reviewable checklist.

    Invents nothing new: every status here is read from an already-computed
    ``ApplicationField``/``MatchAssessment`` signal, not a fresh judgment.
    """
    items: list[ReadinessItem] = []

    if outstanding_field_labels:
        for label in outstanding_field_labels:
            items.append(
                ReadinessItem(
                    category="profile",
                    label=label,
                    status="missing",
                    detail="Needs your answer before this application is complete.",
                )
            )
    else:
        items.append(
            ReadinessItem(
                category="profile",
                label="Application fields",
                status="ready",
                detail="All required fields are answered.",
            )
        )

    for text in missing_requirement_texts:
        items.append(
            ReadinessItem(
                category="requirements",
                label=text,
                status="missing",
                detail="No confirmed evidence meets this requirement.",
            )
        )
    for text in uncertain_requirement_texts:
        items.append(
            ReadinessItem(
                category="requirements",
                label=text,
                status="needs_attention",
                detail="Could not be assessed from your confirmed profile.",
            )
        )

    _STATUS_DETAIL: Final[dict[ReadinessStatus, str]] = {
        "ready": "Reviewed and confirmed.",
        "needs_attention": "AI-drafted — review before using.",
        "missing": "Not enough confirmed evidence yet.",
    }
    items.append(
        ReadinessItem(
            category="cover_letter",
            label="Cover letter",
            status=cover_letter_status,
            detail=_STATUS_DETAIL[cover_letter_status],
        )
    )
    for key, status in question_statuses:
        items.append(
            ReadinessItem(
                category="questions",
                label=QUESTION_TEXTS.get(key, key),
                status=status,
                detail=_STATUS_DETAIL[status],
            )
        )
    return tuple(items)
