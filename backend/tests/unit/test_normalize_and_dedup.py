"""Normalisation and deduplication.

The posting-date invariant gets the most attention here. Many ATS feeds omit a date,
and the tempting shortcut is to substitute the time we first saw the job. That converts
"we don't know when this was posted" into "posted today", which misleads the user about
how stale a listing is. :class:`NormalizedJob` makes that substitution impossible to
write by accident, and these tests hold the door shut.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.domain.dedup import (
    FINGERPRINT_VERSION,
    dedupe_keep_canonical,
    group_duplicates,
    job_fingerprint,
    materially_changed,
    source_priority,
)
from app.domain.normalize import (
    EmploymentType,
    NormalizedJob,
    PostedAtPrecision,
    SalaryRange,
    SourceStatus,
    WorkArrangement,
    classify_employment_type,
    classify_work_arrangement,
    fold_dashes,
    normalize_company_name,
    normalize_job_title,
    normalize_location,
    normalize_text,
    slugify,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------


class TestTextNormalisation:
    def test_accents_and_case_are_folded(self) -> None:
        assert normalize_text("Sénior Développeur") == "senior developpeur"

    def test_whitespace_is_collapsed(self) -> None:
        assert normalize_text("  Backend   Engineer \n") == "backend engineer"

    def test_dash_variants_fold_to_a_hyphen(self) -> None:
        en_dash, em_dash = chr(0x2013), chr(0x2014)
        assert fold_dashes(f"a{en_dash}b{em_dash}c") == "a-b-c"

    def test_none_and_empty_are_handled(self) -> None:
        assert normalize_text(None) == ""
        assert normalize_text("") == ""
        assert slugify(None) == ""

    def test_slugify_produces_a_stable_key(self) -> None:
        assert slugify("Senior  Backend / Platform Engineer!") == (
            "senior-backend-platform-engineer"
        )


class TestCompanyNames:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Acme Technologies, Inc.", "acme"),
            ("ACME", "acme"),
            ("Acme Ltd", "acme"),
            ("Acme Holdings Group", "acme"),
            ("Lumen Systems Ltd.", "lumen-systems"),
        ],
    )
    def test_legal_suffixes_are_stripped(self, raw: str, expected: str) -> None:
        assert normalize_company_name(raw) == expected

    def test_a_company_genuinely_called_group_keeps_its_name(self) -> None:
        """Suffix stripping must never reduce a name to nothing."""
        assert normalize_company_name("Group") == "group"


class TestJobTitles:
    def test_decoration_is_removed(self) -> None:
        assert normalize_job_title("Backend Engineer (Remote)") == "backend-engineer"
        assert normalize_job_title("Developer [Hybrid]") == "developer"
        assert normalize_job_title("Entwickler (m/w/d)") == "entwickler"

    def test_seniority_is_preserved(self) -> None:
        """Senior Engineer and Engineer are different jobs and must not collapse."""
        assert normalize_job_title("Senior Engineer") != normalize_job_title("Engineer")


class TestLocations:
    @pytest.mark.parametrize(
        "raw",
        ["Remote - India", "Remote (India)", "India, Remote", "india remote"],
    )
    def test_remote_phrasings_agree(self, raw: str) -> None:
        assert normalize_location(raw) == normalize_location("Remote - India")

    def test_bare_remote_is_just_remote(self) -> None:
        assert normalize_location("Remote") == "remote"

    def test_a_city_is_slugified(self) -> None:
        assert normalize_location("Bengaluru, India") == "bengaluru-india"


class TestClassification:
    def test_remote_is_detected_from_the_location(self) -> None:
        assert classify_work_arrangement(location="Remote - EU") is WorkArrangement.REMOTE

    def test_hybrid_beats_remote_when_both_appear(self) -> None:
        assert classify_work_arrangement(location="Hybrid remote, Pune") is WorkArrangement.HYBRID

    def test_an_explicit_source_value_wins(self) -> None:
        assert (
            classify_work_arrangement(location="Bengaluru", source_value="ONSITE")
            is WorkArrangement.ONSITE
        )

    def test_unknown_when_nothing_says(self) -> None:
        assert classify_work_arrangement(location="Bengaluru, India") is WorkArrangement.UNKNOWN

    def test_a_passing_mention_of_remote_in_prose_is_not_enough(self) -> None:
        """Long descriptions mention "remote" incidentally all the time."""
        assert (
            classify_work_arrangement(
                location="Bengaluru",
                description="You will collaborate with remote colleagues in other offices.",
            )
            is WorkArrangement.UNKNOWN
        )

    def test_internship_beats_full_time_in_a_mixed_title(self) -> None:
        assert (
            classify_employment_type(title="Software Engineering Intern, Full-Time")
            is EmploymentType.INTERNSHIP
        )

    def test_contract_is_detected(self) -> None:
        assert (
            classify_employment_type(title="Frontend Engineer (Contract)")
            is EmploymentType.CONTRACT
        )

    def test_unknown_when_the_title_says_nothing(self) -> None:
        assert classify_employment_type(title="Backend Engineer") is EmploymentType.UNKNOWN


# ---------------------------------------------------------------------------
# The posting-date invariant
# ---------------------------------------------------------------------------


def _normalized(**overrides: object) -> NormalizedJob:
    defaults: dict[str, object] = {
        "source": "greenhouse",
        "source_job_id": "123",
        "company_name": "Testcorp",
        "title": "Backend Engineer",
        "application_url": "https://example.invalid/apply",
    }
    defaults.update(overrides)
    return NormalizedJob(**defaults)  # type: ignore[arg-type]


class TestPostingDateInvariant:
    def test_no_date_requires_unknown_precision(self) -> None:
        job = _normalized()
        assert job.posted_at is None
        assert job.posted_at_precision is PostedAtPrecision.UNKNOWN
        assert job.has_known_posting_date is False

    def test_claiming_a_precision_without_a_date_is_refused(self) -> None:
        """This is the shape the "posted today" bug would take."""
        with pytest.raises(ValueError, match="never treated as a recent one"):
            _normalized(posted_at=None, posted_at_precision=PostedAtPrecision.EXACT)

    def test_a_date_without_a_precision_is_refused(self) -> None:
        with pytest.raises(ValueError, match="without a precision"):
            _normalized(posted_at=datetime(2026, 6, 1, tzinfo=UTC))

    def test_a_naive_date_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _normalized(
                posted_at=datetime(2026, 6, 1),
                posted_at_precision=PostedAtPrecision.DATE_ONLY,
            )

    def test_a_properly_dated_posting_is_accepted(self) -> None:
        job = _normalized(
            posted_at=datetime(2026, 6, 1, tzinfo=UTC),
            posted_at_precision=PostedAtPrecision.DATE_ONLY,
        )
        assert job.has_known_posting_date is True


class TestNormalizedJobValidation:
    @pytest.mark.parametrize("field", ["source_job_id", "company_name", "title", "application_url"])
    def test_required_fields_cannot_be_blank(self, field: str) -> None:
        with pytest.raises(ValueError, match="must not be blank"):
            _normalized(**{field: "   "})

    def test_salary_minimum_cannot_exceed_the_maximum(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            SalaryRange(minimum=100, maximum=50)

    def test_a_one_sided_salary_range_is_allowed(self) -> None:
        """A range with only a maximum must not have it copied across."""
        salary = SalaryRange(maximum=100_000, currency="USD")
        assert salary.minimum is None
        assert salary.is_empty is False

    def test_default_source_status_is_unknown(self) -> None:
        assert _normalized().source_status is SourceStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    """Minimal stand-in for either a NormalizedJob or an ORM row."""

    company_name: str
    title: str


class TestFingerprint:
    def test_the_same_role_described_differently_matches(self) -> None:
        assert job_fingerprint(
            company_name="Acme Technologies, Inc.",
            title="Software Engineer (Remote)",
            location="Remote - India",
        ) == job_fingerprint(
            company_name="ACME", title="Software Engineer", location="India, Remote"
        )

    def test_seniority_separates_jobs(self) -> None:
        assert job_fingerprint(company_name="Acme", title="Senior Engineer") != job_fingerprint(
            company_name="Acme", title="Engineer"
        )

    def test_location_separates_jobs(self) -> None:
        assert job_fingerprint(
            company_name="Acme", title="Engineer", location="Pune"
        ) != job_fingerprint(company_name="Acme", title="Engineer", location="Chennai")

    def test_different_companies_separate_jobs(self) -> None:
        assert job_fingerprint(company_name="Acme", title="Engineer") != job_fingerprint(
            company_name="Globex", title="Engineer"
        )

    def test_the_fingerprint_is_versioned(self) -> None:
        """A change to the algorithm must be able to invalidate stored values."""
        assert FINGERPRINT_VERSION


class TestSourcePriority:
    def test_an_ats_beats_a_career_page(self) -> None:
        assert source_priority("greenhouse") < source_priority("career_page")

    def test_an_unknown_source_sorts_last(self) -> None:
        assert source_priority("something_new") > source_priority("career_page")

    def test_priority_is_case_insensitive(self) -> None:
        assert source_priority("Greenhouse") == source_priority("greenhouse")


class TestGrouping:
    def test_duplicates_are_grouped_and_a_canonical_row_is_chosen(self) -> None:
        rows = [
            _Row("Lumen Systems Ltd.", "Full Stack Developer (Remote)"),
            _Row("Lumen Systems", "Full Stack Developer"),
            _Row("Acme", "Backend Engineer"),
        ]
        groups = group_duplicates(
            rows,
            locations=["India, Remote", "Remote - India", "Pune"],
            sources=["career_page", "greenhouse", "greenhouse"],
        )
        by_size = {len(g.indices): g for g in groups}
        assert set(by_size) == {1, 2}

        duplicate_group = by_size[2]
        assert duplicate_group.is_duplicate
        # Greenhouse (index 1) is preferred over the career page (index 0).
        assert duplicate_group.canonical_index == 1

    def test_canonical_selection_is_stable_on_a_tie(self) -> None:
        rows = [_Row("Acme", "Engineer"), _Row("Acme", "Engineer")]
        groups = group_duplicates(
            rows, locations=["Pune", "Pune"], sources=["greenhouse", "greenhouse"]
        )
        assert groups[0].canonical_index == 0

    def test_nothing_is_dropped_from_the_groups(self) -> None:
        """Cross-source identity is a heuristic, so no row may be discarded."""
        rows = [_Row("Acme", "Engineer"), _Row("Acme", "Engineer")]
        groups = group_duplicates(rows, locations=["Pune", "Pune"])
        assert sorted(groups[0].indices) == [0, 1]

    def test_keep_canonical_returns_one_index_per_logical_job(self) -> None:
        rows = [
            _Row("Lumen Systems Ltd.", "Full Stack Developer"),
            _Row("Lumen Systems", "Full Stack Developer"),
            _Row("Acme", "Backend Engineer"),
        ]
        kept = dedupe_keep_canonical(
            rows,
            locations=["Remote - India", "India, Remote", "Pune"],
            sources=["career_page", "greenhouse", "greenhouse"],
        )
        assert len(kept) == 2

    def test_mismatched_parallel_lists_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            group_duplicates([_Row("Acme", "Engineer")], locations=[])

    def test_two_distinct_postings_from_the_same_source_are_not_grouped(self) -> None:
        """Two separate requisitions at one company can share title/company/location
        (e.g. two 'Backend Engineer, Remote' openings). ``source_keys`` identifies the
        specific source each came from; a fingerprint bucket where one source_key
        repeats is two genuinely distinct postings, not a duplicate of each other."""
        rows = [_Row("Acme", "Backend Engineer"), _Row("Acme", "Backend Engineer")]
        groups = group_duplicates(
            rows,
            locations=["Remote", "Remote"],
            sources=["greenhouse", "greenhouse"],
            source_keys=["gh-board-acme", "gh-board-acme"],
        )
        assert len(groups) == 2
        assert all(not g.is_duplicate for g in groups)
        assert sorted(g.canonical_index for g in groups) == [0, 1]

    def test_cross_source_duplicates_still_group_when_source_keys_are_distinct(self) -> None:
        rows = [_Row("Acme", "Backend Engineer"), _Row("Acme", "Backend Engineer")]
        groups = group_duplicates(
            rows,
            locations=["Remote", "Remote"],
            sources=["career_page", "greenhouse"],
            source_keys=["acme-careers", "gh-board-acme"],
        )
        assert len(groups) == 1
        assert groups[0].is_duplicate
        # Greenhouse (index 1) is still preferred over the career page (index 0).
        assert groups[0].canonical_index == 1

    def test_a_same_source_collision_leaves_the_whole_bucket_unmerged(self) -> None:
        """Two same-source postings plus a third, different-source listing that
        shares the fingerprint: which (if either) of the two same-source postings the
        third one actually matches is unknowable, so none of the three are merged."""
        rows = [
            _Row("Acme", "Backend Engineer"),
            _Row("Acme", "Backend Engineer"),
            _Row("Acme", "Backend Engineer"),
        ]
        groups = group_duplicates(
            rows,
            locations=["Remote", "Remote", "Remote"],
            sources=["greenhouse", "greenhouse", "career_page"],
            source_keys=["gh-board-acme", "gh-board-acme", "acme-careers"],
        )
        assert len(groups) == 3
        assert all(not g.is_duplicate for g in groups)

    def test_source_keys_mismatched_length_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            group_duplicates([_Row("Acme", "Engineer")], source_keys=[])


class TestMaterialChange:
    def test_a_changed_title_is_material(self) -> None:
        changed = materially_changed(
            previous={"title": "Engineer"}, current={"title": "Senior Engineer"}
        )
        assert changed == ["title"]

    def test_a_reflowed_description_is_not_material(self) -> None:
        """ATS feeds reflow prose constantly; treating that as news buries the user."""
        changed = materially_changed(
            previous={"title": "Engineer", "description": "One wording."},
            current={"title": "Engineer", "description": "Another wording entirely."},
        )
        assert changed == []

    def test_a_changed_application_url_is_material(self) -> None:
        changed = materially_changed(
            previous={"application_url": "https://a.invalid"},
            current={"application_url": "https://b.invalid"},
        )
        assert changed == ["application_url"]

    def test_closing_a_posting_is_material(self) -> None:
        changed = materially_changed(
            previous={"source_status": "OPEN"}, current={"source_status": "CLOSED"}
        )
        assert changed == ["source_status"]
