"""Deterministic mapping from an extracted employer field to confirmed data.

Pure: no Playwright import, no database, no clock — same discipline as
``app.domain.resume_extraction``. Inputs are plain data the caller (the
browser service) already assembled from ``ApplicationField``/``ApplicationAnswer``
rows; this module never reads the database itself.

The one rule everything here is written to honour: a field with no rule match,
an ambiguous match, or a sensitive/legal classification gets no proposed value
and ``needs_review=True``. Nothing here is permitted to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from app.browser.adapters.base import ExtractedField

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True, slots=True)
class ConfirmedFieldSource:
    """A baseline ``ApplicationField`` row, as plain data.

    Carries its existing resolution/provenance forward unchanged — this module
    never re-decides whether something is confirmed, it only decides which
    browser field a given source corresponds to.
    """

    field_key: str
    label: str | None
    value: str | None
    provenance: str
    resolution: str
    needs_human: bool
    sensitive_category: str | None


@dataclass(frozen=True, slots=True)
class ConfirmedAnswerSource:
    """An ``ApplicationAnswer`` row, as plain data."""

    question_key: str
    text: str | None
    provenance: str
    is_generated: bool
    needs_human: bool


@dataclass(frozen=True, slots=True)
class ProposedMapping:
    """One row of the review screen: what CareerPilot proposes, and why."""

    field_id: str
    label: str | None
    field_kind: str
    required: bool
    proposed_value: str | None
    #: "application_field" | "application_answer" | None
    source_kind: str | None
    source_id: str | None
    #: Carried forward from the source row — never recomputed here.
    source_provenance: str | None
    confidence: Confidence
    rule_id: str | None
    needs_review: bool
    #: False for a field kind this phase cannot safely fill (e.g. an unrecognised
    #: custom widget) even when a value is otherwise proposed.
    supported_for_fill: bool
    sensitive_category: str | None = None
    reason: str = ""


_WHITESPACE_RE: Final = re.compile(r"\s+")


def _normalise_label(label: str | None) -> str:
    if not label:
        return ""
    return _WHITESPACE_RE.sub(" ", label).strip().rstrip("*").strip().lower()


#: Label-text fragments (already lower-cased) mapped to a baseline field key.
#: Matched with "in", so "LinkedIn Profile" and "LinkedIn URL" both resolve.
_LABEL_TO_FIELD_KEY: Final[tuple[tuple[str, str], ...]] = (
    ("email", "email"),
    ("phone", "phone"),
    ("linkedin", "linkedin_url"),
    ("github", "github_url"),
    ("portfolio", "portfolio_url"),
    ("website", "portfolio_url"),
    ("location", "location"),
    ("current location", "location"),
    ("cover letter", "cover_letter"),
    ("salary", "salary_expectation"),
    ("compensation expectation", "salary_expectation"),
    ("sponsorship", "requires_sponsorship"),
    ("visa", "requires_sponsorship"),
    ("authorized to work", "work_authorisation"),
    ("authorised to work", "work_authorisation"),
    ("work authorization", "work_authorisation"),
    ("work authorisation", "work_authorisation"),
)

#: Label-text fragments mapped to a drafted application-question key.
_LABEL_TO_QUESTION_KEY: Final[tuple[tuple[str, str], ...]] = (
    ("why are you interested", "why_interested"),
    ("why do you want", "why_interested"),
    ("most relevant experience", "relevant_experience"),
    ("relevant experience", "relevant_experience"),
)

#: Fragments that mean "never auto-answer this, regardless of any label match
#: above" — legal declarations, consent, and anything with no confirmed-data
#: path in this project at all. Checked first, so a question containing both a
#: benign word and a legal-ish phrase is still excluded.
_NEVER_AUTOFILL_FRAGMENTS: Final[tuple[str, ...]] = (
    "employment agreement",
    "non-compete",
    "non compete",
    "restrictive covenant",
    "certify",
    "certification",
    "acknowledge",
    "equal opportunity",
    "eeo",
    "gender",
    "race",
    "ethnicity",
    "veteran",
    "disability",
    "criminal",
    "security clearance",
    "reference",
    "date of birth",
    "government id",
    "social security",
)
# Note: "authorized to work"/"work authorization" are intentionally handled by
# _LABEL_TO_FIELD_KEY instead, because this project already has a genuinely
# confirmed source for exactly that question (Profile.work_authorisation,
# answered once by the user, reused the same way prepare() already reuses it)
# — reusing a real confirmed answer is not the same as inventing one. Every
# other legal/demographic fragment above has no confirmed source anywhere in
# this project and is always excluded.

_UNSUPPORTED_FILL_KINDS: Final[frozenset[str]] = frozenset({"unknown"})


def _find_field_source(
    label: str, sources: tuple[ConfirmedFieldSource, ...]
) -> tuple[ConfirmedFieldSource, str] | None:
    for fragment, field_key in _LABEL_TO_FIELD_KEY:
        if fragment in label:
            match = next((s for s in sources if s.field_key == field_key), None)
            if match is not None:
                return match, fragment
    return None


def _find_answer_source(
    label: str, sources: tuple[ConfirmedAnswerSource, ...]
) -> tuple[ConfirmedAnswerSource, str] | None:
    for fragment, question_key in _LABEL_TO_QUESTION_KEY:
        if fragment in label:
            match = next((s for s in sources if s.question_key == question_key), None)
            if match is not None:
                return match, fragment
    return None


def _split_name(full_name: str) -> tuple[str, str] | None:
    """A simple, explainable first/last split — never attempted for anything
    more ambiguous than exactly two words."""
    parts = full_name.strip().split()
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def build_proposed_mappings(
    *,
    extracted_fields: tuple[ExtractedField, ...],
    confirmed_fields: tuple[ConfirmedFieldSource, ...],
    confirmed_answers: tuple[ConfirmedAnswerSource, ...],
    full_name: str | None,
) -> tuple[ProposedMapping, ...]:
    """The review screen's entire content, one row per extracted field."""
    results: list[ProposedMapping] = []
    full_name_source = next(
        (s for s in confirmed_fields if s.field_key == "full_name"), None
    )

    for extracted in extracted_fields:
        label_norm = _normalise_label(extracted.label)
        supported_for_fill = extracted.field_kind not in _UNSUPPORTED_FILL_KINDS

        if extracted.unrecognised or not label_norm:
            results.append(
                ProposedMapping(
                    field_id=extracted.field_id,
                    label=extracted.label,
                    field_kind=extracted.field_kind,
                    required=extracted.required,
                    proposed_value=None,
                    source_kind=None,
                    source_id=None,
                    source_provenance=None,
                    confidence="low",
                    rule_id=None,
                    needs_review=True,
                    supported_for_fill=False,
                    reason="This field was not recognised. Review and fill it yourself.",
                )
            )
            continue

        if any(fragment in label_norm for fragment in _NEVER_AUTOFILL_FRAGMENTS):
            results.append(
                ProposedMapping(
                    field_id=extracted.field_id,
                    label=extracted.label,
                    field_kind=extracted.field_kind,
                    required=extracted.required,
                    proposed_value=None,
                    source_kind=None,
                    source_id=None,
                    source_provenance=None,
                    confidence="high",
                    rule_id="never_autofill",
                    needs_review=True,
                    supported_for_fill=False,
                    reason=(
                        "This looks like a legal, consent, or demographic question. "
                        "CareerPilot never answers these for you."
                    ),
                )
            )
            continue

        if extracted.field_kind == "file":
            # A label-text rule below (e.g. "cover letter" -> cover_letter)
            # exists for *text* answers. Matched against a file input, it
            # would offer a text draft as something to "fill" a file upload
            # with -- never attempted, not even flagged ready-to-fill.
            results.append(
                ProposedMapping(
                    field_id=extracted.field_id,
                    label=extracted.label,
                    field_kind=extracted.field_kind,
                    required=extracted.required,
                    proposed_value=None,
                    source_kind=None,
                    source_id=None,
                    source_provenance=None,
                    confidence="low",
                    rule_id=None,
                    needs_review=True,
                    supported_for_fill=False,
                    reason=(
                        "File uploads are not attached automatically in this "
                        "version. Attach it yourself."
                    ),
                )
            )
            continue

        # Greenhouse's own stable split fields — matched by id, not label.
        if extracted.field_id in ("first_name", "last_name") and full_name_source is not None:
            if full_name_source.needs_human or not full_name:
                results.append(
                    ProposedMapping(
                        field_id=extracted.field_id,
                        label=extracted.label,
                        field_kind=extracted.field_kind,
                        required=extracted.required,
                        proposed_value=None,
                        source_kind="application_field",
                        source_id="full_name",
                        source_provenance=full_name_source.provenance,
                        confidence="low",
                        rule_id="full_name_split",
                        needs_review=True,
                        supported_for_fill=supported_for_fill,
                        reason="No confirmed full name on file.",
                    )
                )
                continue
            split = _split_name(full_name)
            if split is None:
                results.append(
                    ProposedMapping(
                        field_id=extracted.field_id,
                        label=extracted.label,
                        field_kind=extracted.field_kind,
                        required=extracted.required,
                        proposed_value=None,
                        source_kind="application_field",
                        source_id="full_name",
                        source_provenance=full_name_source.provenance,
                        confidence="low",
                        rule_id="full_name_split",
                        needs_review=True,
                        supported_for_fill=supported_for_fill,
                        reason=(
                            "Your confirmed name has more than two parts — split it "
                            "yourself rather than risk guessing wrong."
                        ),
                    )
                )
                continue
            value = split[0] if extracted.field_id == "first_name" else split[1]
            results.append(
                ProposedMapping(
                    field_id=extracted.field_id,
                    label=extracted.label,
                    field_kind=extracted.field_kind,
                    required=extracted.required,
                    proposed_value=value,
                    source_kind="application_field",
                    source_id="full_name",
                    source_provenance=full_name_source.provenance,
                    confidence="medium",
                    rule_id="full_name_split",
                    needs_review=False,
                    supported_for_fill=supported_for_fill,
                    reason="Derived from your confirmed full name.",
                )
            )
            continue

        answer_match = _find_answer_source(label_norm, confirmed_answers)
        if answer_match is not None:
            answer_source, answer_rule_id = answer_match
            needs_review = answer_source.needs_human or not answer_source.text
            draft_or_confirmed = "drafted" if answer_source.is_generated else "confirmed"
            results.append(
                ProposedMapping(
                    field_id=extracted.field_id,
                    label=extracted.label,
                    field_kind=extracted.field_kind,
                    required=extracted.required,
                    proposed_value=answer_source.text if not needs_review else None,
                    source_kind="application_answer",
                    source_id=answer_source.question_key,
                    source_provenance=answer_source.provenance,
                    confidence="high" if not answer_source.is_generated else "medium",
                    rule_id=answer_rule_id,
                    needs_review=needs_review,
                    supported_for_fill=supported_for_fill,
                    reason=(
                        "No confirmed draft available yet."
                        if needs_review
                        else f"From your {draft_or_confirmed} answer."
                    ),
                )
            )
            continue

        field_match = _find_field_source(label_norm, confirmed_fields)
        if field_match is not None:
            field_source, field_rule_id = field_match
            needs_review = field_source.needs_human or not field_source.value
            results.append(
                ProposedMapping(
                    field_id=extracted.field_id,
                    label=extracted.label,
                    field_kind=extracted.field_kind,
                    required=extracted.required,
                    proposed_value=field_source.value if not needs_review else None,
                    source_kind="application_field",
                    source_id=field_source.field_key,
                    source_provenance=field_source.provenance,
                    confidence="high",
                    rule_id=field_rule_id,
                    needs_review=needs_review,
                    supported_for_fill=supported_for_fill,
                    sensitive_category=field_source.sensitive_category,
                    reason=(
                        "No confirmed value on file."
                        if needs_review
                        else "From your confirmed profile."
                    ),
                )
            )
            continue

        # No rule matched at all.
        results.append(
            ProposedMapping(
                field_id=extracted.field_id,
                label=extracted.label,
                field_kind=extracted.field_kind,
                required=extracted.required,
                proposed_value=None,
                source_kind=None,
                source_id=None,
                source_provenance=None,
                confidence="low",
                rule_id=None,
                needs_review=True,
                supported_for_fill=False,
                reason="No rule matches this question. Answer it yourself.",
            )
        )

    return tuple(results)
