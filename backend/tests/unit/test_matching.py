"""The matching engine.

Three behaviours get the most attention, because they are the ones that would quietly
mislead a user if they broke:

* a hard-filtered job is **not scored at all** — a number beside it would imply it is
  a candidate;
* a component with nothing to go on returns ``None`` and its weight is redistributed,
  so a thin posting does not read as a bad candidate;
* a claimed skill and an evidenced skill are not the same thing.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.domain.matching import (
    CandidateProfile,
    CandidateSkill,
    ComponentScore,
    EducationLevel,
    FilterCode,
    JobPosting,
    MatchConfig,
    RequirementStatus,
    ScoreKind,
    apply_hard_filters,
    assess_match,
    assess_requirements,
    combine_components,
)
from app.domain.normalize import (
    EmploymentType,
    SalaryRange,
    SourceStatus,
    WorkArrangement,
)
from app.domain.provenance import EvidenceRef

pytestmark = pytest.mark.unit


def _profile(**overrides: object) -> CandidateProfile:
    defaults: dict[str, object] = {
        "skills": (
            CandidateSkill(
                name="Python",
                has_evidence=True,
                evidence=(EvidenceRef(kind="project", locator="Jobboard Aggregator"),),
            ),
            CandidateSkill(name="PostgreSQL", has_evidence=True),
            # Claimed, but nothing backs it up.
            CandidateSkill(name="Kubernetes", has_evidence=False),
        ),
        "highest_education": EducationLevel.BACHELORS,
        "total_experience_months": 24,
        "target_roles": ("Backend Engineer",),
        "preferred_locations": ("Bengaluru",),
        "open_to_remote": True,
        "employment_preferences": (EmploymentType.FULL_TIME,),
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)  # type: ignore[arg-type]


def _job(**overrides: object) -> JobPosting:
    defaults: dict[str, object] = {
        "title": "Backend Engineer",
        "company_name": "Testcorp",
        "location_raw": "Bengaluru, India",
        "work_arrangement": WorkArrangement.HYBRID,
        "employment_type": EmploymentType.FULL_TIME,
        "requirement_texts": ("Experience with Python", "Familiarity with PostgreSQL"),
        "source_status": SourceStatus.OPEN,
    }
    defaults.update(overrides)
    return JobPosting(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------


class TestHardFilters:
    def test_a_matching_job_passes(self) -> None:
        result = apply_hard_filters(profile=_profile(), job=_job())
        assert result.passed
        assert result.failures == ()

    def test_closed_posting_is_excluded(self) -> None:
        result = apply_hard_filters(profile=_profile(), job=_job(source_status=SourceStatus.CLOSED))
        assert not result.passed
        assert FilterCode.JOB_CLOSED in [f.code for f in result.failures]

    def test_excluded_company_is_honoured_regardless_of_spelling(self) -> None:
        """The user typed one form of the name; the posting uses another."""
        profile = _profile(excluded_companies=("testcorp ltd",))
        result = apply_hard_filters(profile=profile, job=_job(company_name="Testcorp"))
        assert not result.passed
        assert FilterCode.EXCLUDED_COMPANY in [f.code for f in result.failures]

    def test_excluded_role_keyword_is_honoured(self) -> None:
        profile = _profile(excluded_roles=("sales",))
        result = apply_hard_filters(profile=profile, job=_job(title="Sales Engineer"))
        assert not result.passed
        assert FilterCode.EXCLUDED_ROLE in [f.code for f in result.failures]

    def test_employment_type_mismatch_is_excluded_by_default(self) -> None:
        result = apply_hard_filters(
            profile=_profile(), job=_job(employment_type=EmploymentType.CONTRACT)
        )
        assert not result.passed
        assert FilterCode.EMPLOYMENT_TYPE_MISMATCH in [f.code for f in result.failures]

    def test_unknown_employment_type_is_not_excluded(self) -> None:
        """A posting that does not say must not be filtered out on a guess."""
        result = apply_hard_filters(
            profile=_profile(), job=_job(employment_type=EmploymentType.UNKNOWN)
        )
        assert result.passed

    def test_role_filter_is_off_by_default(self) -> None:
        """A false exclusion is invisible to the user, so the inferential filters
        default to off."""
        result = apply_hard_filters(
            profile=_profile(), job=_job(title="Quantum Metallurgy Technician")
        )
        assert result.passed

    def test_role_filter_excludes_when_enabled(self) -> None:
        result = apply_hard_filters(
            profile=_profile(),
            job=_job(title="Quantum Metallurgy Technician"),
            config=MatchConfig(enforce_role_filter=True),
        )
        assert not result.passed
        assert FilterCode.ROLE_MISMATCH in [f.code for f in result.failures]

    def test_salary_filter_compares_against_the_top_of_the_band(self) -> None:
        """A job offering 80-120k does meet a 100k minimum."""
        profile = _profile(minimum_salary=100_000, salary_currency="USD")
        job = _job(
            salary=SalaryRange(minimum=80_000, maximum=120_000, currency="USD", period="year")
        )
        result = apply_hard_filters(
            profile=profile, job=job, config=MatchConfig(enforce_salary_filter=True)
        )
        assert result.passed

    def test_salary_filter_excludes_a_band_entirely_below_the_minimum(self) -> None:
        profile = _profile(minimum_salary=100_000, salary_currency="USD")
        job = _job(
            salary=SalaryRange(minimum=40_000, maximum=60_000, currency="USD", period="year")
        )
        result = apply_hard_filters(
            profile=profile, job=job, config=MatchConfig(enforce_salary_filter=True)
        )
        assert not result.passed
        assert FilterCode.SALARY_BELOW_MINIMUM in [f.code for f in result.failures]

    def test_salary_in_a_different_currency_is_not_compared(self) -> None:
        """Comparing 60,000 EUR against a 100,000 INR minimum would be nonsense."""
        profile = _profile(minimum_salary=100_000, salary_currency="INR")
        job = _job(
            salary=SalaryRange(minimum=40_000, maximum=60_000, currency="EUR", period="year")
        )
        result = apply_hard_filters(
            profile=profile, job=job, config=MatchConfig(enforce_salary_filter=True)
        )
        assert result.passed


# ---------------------------------------------------------------------------
# Requirement assessment
# ---------------------------------------------------------------------------


class TestRequirements:
    def test_a_held_skill_is_met(self) -> None:
        results = assess_requirements(
            profile=_profile(), job=_job(requirement_texts=("Experience with Python",))
        )
        assert results[0].status is RequirementStatus.MET
        assert "python" in results[0].matched_skills

    def test_plural_forms_match(self) -> None:
        """Postings write "REST APIs" where a profile lists "REST API"."""
        profile = _profile(skills=(CandidateSkill(name="REST API"),))
        results = assess_requirements(
            profile=profile,
            job=_job(requirement_texts=("Experience designing and consuming REST APIs",)),
        )
        assert results[0].status is RequirementStatus.MET

    def test_implied_skills_count(self) -> None:
        """Someone who has used PostgreSQL has written SQL."""
        profile = _profile(skills=(CandidateSkill(name="PostgreSQL"),))
        results = assess_requirements(
            profile=profile, job=_job(requirement_texts=("Strong SQL skills",))
        )
        assert results[0].status is RequirementStatus.MET

    def test_a_recognised_technology_the_user_lacks_is_missing(self) -> None:
        results = assess_requirements(
            profile=_profile(), job=_job(requirement_texts=("Strong experience with Rust",))
        )
        assert results[0].status is RequirementStatus.MISSING
        assert "rust" in results[0].missing_skills

    def test_prose_with_no_recognised_technology_is_uncertain_not_missing(self) -> None:
        """The defect this guards against produced gaps like "designing".

        A confidently wrong gap is worse than no answer, because the explanation is
        the part the user is asked to trust.
        """
        results = assess_requirements(
            profile=_profile(),
            job=_job(requirement_texts=("Excellent written and verbal communication",)),
        )
        assert results[0].status is RequirementStatus.UNCERTAIN
        assert results[0].missing_skills == ()

    def test_a_shorter_name_inside_a_held_one_is_not_a_gap(self) -> None:
        """ "REST APIs" matches both `rest api` and `rest`; holding the former is enough."""
        profile = _profile(skills=(CandidateSkill(name="REST API"),))
        results = assess_requirements(
            profile=profile, job=_job(requirement_texts=("Experience with REST APIs",))
        )
        assert "rest" not in results[0].missing_skills

    def test_years_threshold_is_missing_when_history_is_short(self) -> None:
        profile = _profile(total_experience_months=6)
        results = assess_requirements(
            profile=profile,
            job=_job(requirement_texts=("5+ years of Python experience",)),
        )
        assert results[0].status is RequirementStatus.MISSING
        assert results[0].note is not None
        assert "0.5 years" in results[0].note

    def test_years_threshold_is_uncertain_when_history_is_unknown(self) -> None:
        """ "We don't know" is not "zero years"."""
        profile = _profile(total_experience_months=None)
        results = assess_requirements(
            profile=profile,
            job=_job(requirement_texts=("5+ years of Python experience",)),
        )
        assert results[0].status is RequirementStatus.UNCERTAIN
        assert results[0].note is not None
        assert "no work history" in results[0].note

    def test_optional_requirements_are_marked(self) -> None:
        results = assess_requirements(
            profile=_profile(),
            job=_job(requirement_texts=("Kubernetes experience is a nice to have",)),
        )
        assert results[0].is_optional is True

    def test_evidence_is_attached_only_for_evidenced_skills(self) -> None:
        results = assess_requirements(
            profile=_profile(),
            job=_job(
                requirement_texts=(
                    "Experience with Python",  # evidenced
                    "Experience with Kubernetes",  # claimed only
                )
            ),
        )
        assert results[0].evidence != ()
        assert results[1].evidence == ()

    def test_blank_requirement_lines_are_skipped(self) -> None:
        results = assess_requirements(
            profile=_profile(), job=_job(requirement_texts=("", "   ", "Python"))
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Component combination
# ---------------------------------------------------------------------------


class TestCombineComponents:
    WEIGHTS: ClassVar[dict[str, float]] = {"a": 0.5, "b": 0.3, "c": 0.2}

    def test_all_assessed_components_are_weighted_normally(self) -> None:
        components = (
            ComponentScore(name="a", raw=1.0, weight=0.0, rationale=""),
            ComponentScore(name="b", raw=0.0, weight=0.0, rationale=""),
            ComponentScore(name="c", raw=0.5, weight=0.0, rationale=""),
        )
        score, assessed_weight, _ = combine_components(components, weights=self.WEIGHTS)
        assert assessed_weight == pytest.approx(1.0)
        assert score == pytest.approx(0.5 * 1.0 + 0.3 * 0.0 + 0.2 * 0.5)

    def test_unassessed_components_have_their_weight_redistributed(self) -> None:
        """A thin posting must not read as a bad candidate.

        With `c` unassessable, the score is the weighted mean of what *could* be
        judged, not a figure dragged down by the posting's own gaps.
        """
        components = (
            ComponentScore(name="a", raw=1.0, weight=0.0, rationale=""),
            ComponentScore(name="b", raw=1.0, weight=0.0, rationale=""),
            ComponentScore(name="c", raw=None, weight=0.0, rationale=""),
        )
        score, assessed_weight, _ = combine_components(components, weights=self.WEIGHTS)
        assert assessed_weight == pytest.approx(0.8)
        assert score == pytest.approx(1.0)

    def test_nothing_assessable_gives_none_not_zero(self) -> None:
        components = (
            ComponentScore(name="a", raw=None, weight=0.0, rationale=""),
            ComponentScore(name="b", raw=None, weight=0.0, rationale=""),
        )
        score, assessed_weight, _ = combine_components(components, weights=self.WEIGHTS)
        assert score is None
        assert assessed_weight == 0.0

    def test_score_is_clamped_into_range(self) -> None:
        components = (ComponentScore(name="a", raw=1.0, weight=0.0, rationale=""),)
        score, _, _ = combine_components(components, weights={"a": 1.0})
        assert score is not None
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestAssessMatch:
    def test_a_good_match_scores_and_explains(self) -> None:
        assessment = assess_match(profile=_profile(), job=_job())
        assert assessment.score is not None
        assert 0.0 <= assessment.score <= 1.0
        assert assessment.score_kind is ScoreKind.RANKING_HEURISTIC
        assert assessment.explanation.suggested_next_step
        assert assessment.matched_requirements

    def test_a_filtered_job_is_not_scored(self) -> None:
        """A number beside an excluded job would imply it is a candidate."""
        assessment = assess_match(profile=_profile(), job=_job(source_status=SourceStatus.CLOSED))
        assert assessment.excluded is True
        assert assessment.score is None
        assert assessment.components == ()
        assert assessment.requirements == ()
        assert assessment.explanation.eligibility_concerns

    def test_the_result_is_deterministic(self) -> None:
        """No model is involved, so the explanation is the actual computation."""
        first = assess_match(profile=_profile(), job=_job())
        second = assess_match(profile=_profile(), job=_job())
        assert first.score == second.score
        assert first.explanation == second.explanation

    def test_evidence_separates_claimed_from_demonstrated(self) -> None:
        """Two identical skill lists, differing only in whether anything backs them."""
        evidenced = _profile(
            skills=(
                CandidateSkill(
                    name="Python",
                    has_evidence=True,
                    evidence=(EvidenceRef(kind="project", locator="Thing"),),
                ),
            )
        )
        claimed_only = _profile(skills=(CandidateSkill(name="Python", has_evidence=False),))
        job = _job(requirement_texts=("Experience with Python",))

        with_evidence = assess_match(profile=evidenced, job=job)
        without_evidence = assess_match(profile=claimed_only, job=job)

        assert with_evidence.score is not None
        assert without_evidence.score is not None
        assert with_evidence.score > without_evidence.score
        assert with_evidence.explanation.evidenced_qualifications
        assert not without_evidence.explanation.evidenced_qualifications

    def test_posting_with_no_requirements_does_not_penalise_the_candidate(self) -> None:
        assessment = assess_match(profile=_profile(), job=_job(requirement_texts=()))
        skill_overlap = assessment.component("skill_overlap")
        assert skill_overlap is not None
        assert skill_overlap.assessed is False
        # Still rankable on the components that could be judged.
        assert assessment.score is not None

    def test_education_is_not_assessed_when_the_posting_is_silent(self) -> None:
        assessment = assess_match(profile=_profile(), job=_job())
        education = assessment.component("education")
        assert education is not None
        assert education.assessed is False

    def test_education_shortfall_is_scored_and_flagged(self) -> None:
        profile = _profile(highest_education=EducationLevel.HIGH_SCHOOL)
        job = _job(requirement_texts=("Master's degree in Computer Science required",))
        assessment = assess_match(profile=profile, job=job)
        education = assessment.component("education")
        assert education is not None
        assert education.raw is not None
        assert education.raw < 1.0
        assert assessment.explanation.eligibility_concerns

    def test_unset_target_roles_do_not_count_as_a_mismatch(self) -> None:
        profile = _profile(target_roles=())
        assessment = assess_match(profile=profile, job=_job())
        relevance = assessment.component("role_relevance")
        assert relevance is not None
        assert relevance.assessed is False

    def test_senior_title_with_little_experience_is_flagged(self) -> None:
        profile = _profile(total_experience_months=6)
        assessment = assess_match(profile=profile, job=_job(title="Senior Staff Engineer"))
        assert any(
            "senior-level" in concern for concern in assessment.explanation.eligibility_concerns
        )

    def test_remote_role_is_acceptable_when_remote_preference_is_unset(self) -> None:
        """`None` means unstated, and an unstated preference excludes nothing."""
        profile = _profile(open_to_remote=None, preferred_locations=("Chennai",))
        assessment = assess_match(
            profile=profile,
            job=_job(location_raw="Remote", work_arrangement=WorkArrangement.REMOTE),
        )
        location = assessment.component("location")
        assert location is not None
        assert location.raw == 1.0

    def test_remote_role_is_penalised_when_the_user_declines_remote(self) -> None:
        profile = _profile(open_to_remote=False)
        assessment = assess_match(
            profile=profile,
            job=_job(location_raw="Remote", work_arrangement=WorkArrangement.REMOTE),
        )
        location = assessment.component("location")
        assert location is not None
        assert location.raw == 0.0

    def test_missing_requirements_drive_the_suggested_next_step(self) -> None:
        assessment = assess_match(
            profile=_profile(),
            job=_job(requirement_texts=("Deep experience with Rust and Elixir",)),
        )
        assert assessment.missing_requirements
        assert "apply now" not in assessment.explanation.suggested_next_step.lower()

    def test_engine_version_is_recorded(self) -> None:
        """A stored assessment must say which version produced it."""
        assessment = assess_match(profile=_profile(), job=_job())
        assert assessment.engine_version
