"""Provenance and redaction.

Together these implement safety requirements 4, 5 and 7: nothing about the candidate is
invented, anything unknown becomes a human's job, and no sensitive value is stored or
logged in the clear.

The provenance tests are written as a closed set over ``ProvenanceKind``, so adding a
new kind without deciding whether it is authoritative fails the suite rather than
defaulting to "yes".
"""

from __future__ import annotations

import pytest

from app.domain.provenance import (
    AUTHORITATIVE_KINDS,
    EvidenceRef,
    Fact,
    ProvenanceKind,
    SensitiveCategory,
    is_authoritative,
    require_authoritative,
    unknown_fact,
)
from app.domain.redaction import (
    MASK,
    Sensitivity,
    classify_field,
    mask_email,
    mask_value,
    redact_for_storage,
    redact_text,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenanceKinds:
    def test_every_kind_is_explicitly_classified(self) -> None:
        """A new kind must be a deliberate decision, not an accident."""
        expected_authoritative = {
            ProvenanceKind.USER_CONFIRMED_PROFILE,
            ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
            ProvenanceKind.USER_APPROVED_EVIDENCE,
            ProvenanceKind.PREVIOUSLY_APPROVED_ANSWER,
            ProvenanceKind.USER_DIRECT_INPUT,
        }
        assert expected_authoritative == AUTHORITATIVE_KINDS

        for kind in ProvenanceKind:
            assert is_authoritative(kind) == (kind in expected_authoritative)

    def test_unconfirmed_resume_extraction_is_not_authoritative(self) -> None:
        """Extraction output is a proposal until the user confirms it."""
        assert not is_authoritative(ProvenanceKind.RESUME_EXTRACTION_UNCONFIRMED)

    def test_generated_prose_is_not_authoritative(self) -> None:
        """A model-written answer can never satisfy a required field."""
        assert not is_authoritative(ProvenanceKind.GENERATED_UNVERIFIED)

    def test_unknown_is_not_authoritative(self) -> None:
        assert not is_authoritative(ProvenanceKind.UNKNOWN)


class TestFact:
    def test_a_confirmed_fact_is_usable(self) -> None:
        fact = Fact(
            key="full_name",
            value="Demo Candidate",
            provenance=ProvenanceKind.USER_CONFIRMED_PROFILE,
        )
        assert fact.is_usable
        assert not fact.needs_human
        assert require_authoritative(fact) == "Demo Candidate"

    def test_a_generated_fact_needs_a_human(self) -> None:
        fact = Fact(
            key="cover_letter",
            value="A drafted paragraph.",
            provenance=ProvenanceKind.GENERATED_UNVERIFIED,
        )
        assert not fact.is_usable
        assert fact.needs_human
        with pytest.raises(ValueError, match="cannot be submitted"):
            require_authoritative(fact)

    def test_an_empty_value_needs_a_human_even_when_confirmed(self) -> None:
        fact = Fact(key="phone", value="   ", provenance=ProvenanceKind.USER_CONFIRMED_PROFILE)
        assert fact.needs_human

    def test_unknown_fact_carries_no_value(self) -> None:
        fact = unknown_fact(
            "work_authorisation", sensitive_category=SensitiveCategory.WORK_AUTHORISATION
        )
        assert fact.value is None
        assert fact.provenance is ProvenanceKind.UNKNOWN
        assert fact.needs_human
        assert fact.sensitive_category is SensitiveCategory.WORK_AUTHORISATION

    def test_an_unknown_fact_with_a_value_is_a_contradiction(self) -> None:
        with pytest.raises(ValueError, match="marked UNKNOWN but carries a value"):
            Fact(key="phone", value="123", provenance=ProvenanceKind.UNKNOWN)

    def test_evidence_is_summarised_for_display(self) -> None:
        fact = Fact(
            key="python",
            value="yes",
            provenance=ProvenanceKind.USER_APPROVED_EVIDENCE,
            evidence=(
                EvidenceRef(kind="project", locator="Jobboard Aggregator"),
                EvidenceRef(kind="resume", locator="page=2,section=Experience"),
            ),
        )
        assert fact.evidence_summary() == [
            "project:Jobboard Aggregator",
            "resume:page=2,section=Experience",
        ]


class TestSensitiveCategories:
    @pytest.mark.parametrize(
        "category",
        [
            SensitiveCategory.WORK_AUTHORISATION,
            SensitiveCategory.VISA_SPONSORSHIP,
            SensitiveCategory.DEMOGRAPHIC,
            SensitiveCategory.DISABILITY,
            SensitiveCategory.VETERAN_STATUS,
            SensitiveCategory.SALARY_HISTORY,
            SensitiveCategory.CRIMINAL_HISTORY,
        ],
    )
    def test_a_sensitive_fact_with_no_source_needs_a_human(
        self, category: SensitiveCategory
    ) -> None:
        """Even where a plausible answer could be computed, it is left to the user."""
        assert unknown_fact("q", sensitive_category=category).needs_human


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestClassification:
    @pytest.mark.parametrize(
        "name",
        ["password", "user_password", "otp", "session_token", "card_number", "cvv"],
    )
    def test_credentials_are_never_stored(self, name: str) -> None:
        assert classify_field(name) is Sensitivity.NEVER_STORED

    @pytest.mark.parametrize(
        "name",
        ["phone", "mobile_number", "ssn", "aadhaar", "date_of_birth", "salary_expectation"],
    )
    def test_personal_identifiers_are_masked(self, name: str) -> None:
        assert classify_field(name) is Sensitivity.MASKED

    @pytest.mark.parametrize("name", ["full_name", "job_title", "city", "linkedin_url"])
    def test_ordinary_fields_are_public(self, name: str) -> None:
        assert classify_field(name) is Sensitivity.PUBLIC

    def test_the_label_is_considered_as_well_as_the_name(self) -> None:
        assert classify_field("field_7", label="Mobile Phone Number") is Sensitivity.MASKED


class TestMasking:
    def test_the_tail_stays_visible_so_the_owner_can_recognise_it(self) -> None:
        assert mask_value("9876543210") == "******3210"

    def test_short_values_are_masked_entirely(self) -> None:
        """Showing four of five characters would reveal almost all of it."""
        assert mask_value("12345") == "*****"

    def test_none_and_empty_are_preserved(self) -> None:
        assert mask_value(None) is None
        assert mask_value("   ") == ""

    def test_email_keeps_the_domain_readable(self) -> None:
        assert mask_email("demo.candidate@example.com") == "d*************@example.com"

    def test_a_malformed_email_falls_back_to_plain_masking(self) -> None:
        masked = mask_email("not-an-email")
        assert masked is not None
        assert "mail" in masked or masked.startswith("*")


class TestRedactForStorage:
    def test_a_password_is_replaced_wholesale(self) -> None:
        value, sensitivity = redact_for_storage(field_name="password", value="hunter2")
        assert value == MASK
        assert sensitivity is Sensitivity.NEVER_STORED

    def test_a_phone_number_is_masked(self) -> None:
        value, sensitivity = redact_for_storage(field_name="phone", value="9876543210")
        assert value == "******3210"
        assert sensitivity is Sensitivity.MASKED

    def test_an_email_in_a_masked_field_keeps_its_domain(self) -> None:
        value, _ = redact_for_storage(field_name="emergency_contact", value="someone@example.com")
        assert value is not None
        assert value.endswith("@example.com")
        assert "someone" not in value

    def test_an_ordinary_value_is_stored_as_is(self) -> None:
        value, sensitivity = redact_for_storage(field_name="full_name", value="Demo Candidate")
        assert value == "Demo Candidate"
        assert sensitivity is Sensitivity.PUBLIC

    def test_a_none_value_stays_none(self) -> None:
        value, _ = redact_for_storage(field_name="phone", value=None)
        assert value is None


class TestRedactText:
    def test_email_addresses_are_scrubbed_from_prose(self) -> None:
        assert redact_text("Contact demo@example.com for details") == (
            "Contact [email] for details"
        )

    def test_long_digit_runs_are_scrubbed(self) -> None:
        assert redact_text("Ref 9876543210 received") == "Ref [number] received"

    def test_short_numbers_are_left_alone(self) -> None:
        """A year or a count is not an identifier."""
        assert redact_text("2 of 4 requirements met in 2026") == ("2 of 4 requirements met in 2026")

    def test_none_passes_through(self) -> None:
        assert redact_text(None) is None
