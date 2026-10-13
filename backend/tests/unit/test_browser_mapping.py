"""``app.browser.mapping`` — pure, deterministic field mapping.

The property every test ultimately checks: a field is only ever proposed a
value when a confirmed source exists and that source is not itself
``needs_human``. Everything else — no rule match, an unrecognised field, a
legal/demographic question, an ambiguous name split — comes back
``needs_review=True`` with no proposed value, never a guess.
"""

from __future__ import annotations

import pytest

from app.browser.adapters.base import ExtractedField, FieldKind
from app.browser.mapping import (
    ConfirmedAnswerSource,
    ConfirmedFieldSource,
    build_proposed_mappings,
)

pytestmark = pytest.mark.unit


def _field(
    field_id: str, label: str, *, required: bool = False, kind: FieldKind = "text"
) -> ExtractedField:
    return ExtractedField(
        field_id=field_id, label=label, field_kind=kind, required=required, locator=f"#{field_id}"
    )


def _confirmed_field(
    key: str,
    *,
    value: str | None = "x",
    needs_human: bool = False,
    provenance: str = "USER_CONFIRMED_PROFILE",
    sensitive_category: str | None = None,
) -> ConfirmedFieldSource:
    return ConfirmedFieldSource(
        field_key=key,
        label=key,
        value=value,
        provenance=provenance,
        resolution="RESOLVED" if value else "NEEDS_HUMAN",
        needs_human=needs_human,
        sensitive_category=sensitive_category,
    )


class TestRecognisedFields:
    def test_a_confirmed_email_is_proposed(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("email", "Email", required=True),),
            confirmed_fields=(_confirmed_field("email", value="a@b.com"),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value == "a@b.com"
        assert mappings[0].needs_review is False
        assert mappings[0].source_kind == "application_field"
        assert mappings[0].source_id == "email"

    def test_a_field_with_no_confirmed_value_needs_review(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("phone", "Phone"),),
            confirmed_fields=(_confirmed_field("phone", value=None, needs_human=True),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value is None
        assert mappings[0].needs_review is True

    def test_label_matching_is_case_and_whitespace_insensitive(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("q1", "  LinkedIn   Profile *  "),),
            confirmed_fields=(_confirmed_field("linkedin_url", value="https://linkedin.com/in/x"),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value == "https://linkedin.com/in/x"
        assert mappings[0].needs_review is False


class TestNameSplitting:
    def test_a_two_word_name_is_split_deterministically(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(
                _field("first_name", "First Name", required=True),
                _field("last_name", "Last Name", required=True),
            ),
            confirmed_fields=(_confirmed_field("full_name", value="Ada Lovelace"),),
            confirmed_answers=(),
            full_name="Ada Lovelace",
        )
        by_id = {m.field_id: m for m in mappings}
        assert by_id["first_name"].proposed_value == "Ada"
        assert by_id["last_name"].proposed_value == "Lovelace"
        assert by_id["first_name"].needs_review is False

    def test_a_three_word_name_is_never_guessed(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("first_name", "First Name"), _field("last_name", "Last Name")),
            confirmed_fields=(_confirmed_field("full_name", value="Mary Jane Smith"),),
            confirmed_answers=(),
            full_name="Mary Jane Smith",
        )
        assert all(m.needs_review for m in mappings)
        assert all(m.proposed_value is None for m in mappings)

    def test_no_confirmed_name_at_all_needs_review(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("first_name", "First Name"),),
            confirmed_fields=(),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].needs_review is True
        assert mappings[0].proposed_value is None


class TestApplicationAnswers:
    def test_a_confirmed_drafted_answer_is_proposed_as_medium_confidence(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(
                _field("q2", "Why are you interested in this role?", kind="textarea"),
            ),
            confirmed_fields=(),
            confirmed_answers=(
                ConfirmedAnswerSource(
                    question_key="why_interested",
                    text="Because of the Python/Kafka overlap.",
                    provenance="GENERATED_UNVERIFIED",
                    is_generated=True,
                    needs_human=False,
                ),
            ),
            full_name=None,
        )
        assert mappings[0].proposed_value == "Because of the Python/Kafka overlap."
        assert mappings[0].confidence == "medium"
        assert mappings[0].needs_review is False

    def test_a_needs_human_answer_is_never_proposed(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(
                _field("q2", "Describe your most relevant experience", kind="textarea"),
            ),
            confirmed_fields=(),
            confirmed_answers=(
                ConfirmedAnswerSource(
                    question_key="relevant_experience",
                    text="placeholder",
                    provenance="GENERATED_UNVERIFIED",
                    is_generated=True,
                    needs_human=True,
                ),
            ),
            full_name=None,
        )
        assert mappings[0].proposed_value is None
        assert mappings[0].needs_review is True


class TestNeverAutofill:
    @pytest.mark.parametrize(
        "label",
        [
            "Are you subject to any employment agreements and/or "
            "post-employment restrictive covenants?",
            "I certify that the information provided is accurate.",
            "Gender",
            "Race/Ethnicity",
            "Veteran status",
            "Do you have a disability?",
            "Have you been convicted of a criminal offense?",
            "Do you hold a security clearance?",
            "Please provide a reference.",
            "Date of birth",
        ],
    )
    def test_a_legal_or_demographic_question_is_never_given_a_value(self, label: str) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("q_legal", label, required=True),),
            # Even with a matching confirmed field present, it must never be used.
            confirmed_fields=(_confirmed_field("salary_expectation", value="100000"),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value is None
        assert mappings[0].needs_review is True
        assert mappings[0].supported_for_fill is False

    def test_work_authorisation_is_the_one_exception_with_a_real_confirmed_source(self) -> None:
        """Reusing a fact the user already confirmed once (Profile.work_authorisation)
        is not the same as inventing one — Phase 1 already treats it as authoritative."""
        mappings = build_proposed_mappings(
            extracted_fields=(
                _field(
                    "q_auth",
                    "Are you authorized to work in this location?",
                    required=True,
                ),
            ),
            confirmed_fields=(_confirmed_field("work_authorisation", value="Yes"),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value == "Yes"
        assert mappings[0].needs_review is False


class TestFileFieldsAreNeverProposedAText:
    """Regression test: a file-kind field whose label happens to match a
    text-answer rule (e.g. a cover-letter upload literally labelled "Cover
    Letter") must never be proposed that text as a fillable value — a text
    draft is not something ``adapter.fill_field`` can attach as a file, and
    the review screen must not claim it is ready to fill."""

    def test_a_cover_letter_upload_is_never_given_the_drafted_text(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("cover_letter", "Cover Letter", kind="file"),),
            confirmed_fields=(
                _confirmed_field(
                    "cover_letter", value="Dear hiring team, ...", provenance="GENERATED_UNVERIFIED"
                ),
            ),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value is None
        assert mappings[0].needs_review is True
        assert mappings[0].supported_for_fill is False

    def test_a_resume_upload_whose_label_matches_no_rule_is_also_left_alone(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("resume", "Resume", kind="file", required=True),),
            confirmed_fields=(_confirmed_field("resume", value="resume.pdf"),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].proposed_value is None
        assert mappings[0].needs_review is True
        assert mappings[0].supported_for_fill is False


class TestUnrecognisedFields:
    def test_a_field_with_no_label_is_never_resolved(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(
                ExtractedField(
                    field_id="mystery",
                    label=None,
                    field_kind="text",
                    required=True,
                    locator="#mystery",
                ),
            ),
            confirmed_fields=(),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].needs_review is True
        assert mappings[0].proposed_value is None

    def test_an_explicitly_unrecognised_field_is_never_resolved(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(
                ExtractedField(
                    field_id="widget",
                    label="Something",
                    field_kind="unknown",
                    required=False,
                    locator="#widget",
                    unrecognised=True,
                ),
            ),
            confirmed_fields=(_confirmed_field("something", value="x"),),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].needs_review is True
        assert mappings[0].supported_for_fill is False

    def test_no_rule_match_at_all_needs_review(self) -> None:
        mappings = build_proposed_mappings(
            extracted_fields=(_field("q_random", "Favourite colour"),),
            confirmed_fields=(),
            confirmed_answers=(),
            full_name=None,
        )
        assert mappings[0].needs_review is True
        assert mappings[0].rule_id is None


def test_mapping_is_deterministic() -> None:
    extracted = (_field("email", "Email", required=True),)
    confirmed = (_confirmed_field("email", value="a@b.com"),)
    first = build_proposed_mappings(
        extracted_fields=extracted, confirmed_fields=confirmed, confirmed_answers=(), full_name=None
    )
    second = build_proposed_mappings(
        extracted_fields=extracted, confirmed_fields=confirmed, confirmed_answers=(), full_name=None
    )
    assert first == second
