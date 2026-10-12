"""``app.domain.application_package`` — pure, deterministic, evidence-only.

The one property every test here ultimately checks: nothing in the generated
text or guidance can name a skill, company, or highlight that was not present in
the confirmed input. When there is no confirmed overlap with the job, the
functions say so plainly rather than writing something generic.
"""

from __future__ import annotations

import uuid

import pytest

from app.domain.application_package import (
    ConfirmedExperienceItem,
    build_cover_letter_draft,
    build_question_draft,
    build_readiness_checklist,
    build_resume_guidance,
)

pytestmark = pytest.mark.unit


def _item(
    *, kind: str = "project", title: str, skills: frozenset[str], highlights: tuple[str, ...] = ()
) -> ConfirmedExperienceItem:
    return ConfirmedExperienceItem(
        kind=kind,  # type: ignore[arg-type]
        id=uuid.uuid4(),
        title=title,
        organisation="Acme Corp" if kind == "experience" else None,
        skills=skills,
        highlights=highlights,
    )


class TestResumeGuidance:
    def test_an_item_with_no_overlap_is_never_recommended(self) -> None:
        item = _item(title="Irrelevant Project", skills=frozenset({"cobol"}))
        result = build_resume_guidance(
            candidate_items=(item,), job_requirement_skills=frozenset({"python", "kafka"})
        )
        assert result == ()

    def test_items_are_ranked_by_overlap_count_descending(self) -> None:
        weak = _item(title="Weak Match", skills=frozenset({"python"}))
        strong = _item(title="Strong Match", skills=frozenset({"python", "kafka", "go"}))
        result = build_resume_guidance(
            candidate_items=(weak, strong),
            job_requirement_skills=frozenset({"python", "kafka", "go"}),
        )
        assert [r.title for r in result] == ["Strong Match", "Weak Match"]

    def test_matched_skills_are_exactly_the_overlap_not_the_full_skill_set(self) -> None:
        item = _item(title="Platform Work", skills=frozenset({"python", "kafka", "rust"}))
        result = build_resume_guidance(
            candidate_items=(item,), job_requirement_skills=frozenset({"python", "kafka"})
        )
        assert set(result[0].matched_skills) == {"python", "kafka"}
        assert "rust" not in result[0].matched_skills

    def test_result_is_capped_at_the_given_limit(self) -> None:
        items = tuple(
            _item(title=f"Project {i}", skills=frozenset({"python"})) for i in range(10)
        )
        result = build_resume_guidance(
            candidate_items=items, job_requirement_skills=frozenset({"python"}), limit=3
        )
        assert len(result) == 3

    def test_is_deterministic(self) -> None:
        items = (
            _item(title="A", skills=frozenset({"python"})),
            _item(title="B", skills=frozenset({"python", "kafka"})),
        )
        skills = frozenset({"python", "kafka"})
        first = build_resume_guidance(candidate_items=items, job_requirement_skills=skills)
        second = build_resume_guidance(candidate_items=items, job_requirement_skills=skills)
        assert first == second


class TestCoverLetterDraft:
    def test_no_confirmed_match_produces_a_placeholder_not_a_guess(self) -> None:
        draft = build_cover_letter_draft(
            full_name="Ada Lovelace", job_title="Backend Engineer", company_name="Acme",
            best_match=None,
        )
        assert draft.needs_human is True
        assert "Acme" not in draft.text
        assert draft.evidence_refs == ()

    def test_a_confirmed_match_is_cited_by_name(self) -> None:
        item = _item(
            kind="experience",
            title="Platform Engineer",
            skills=frozenset({"python", "kafka"}),
            highlights=("Reduced pipeline latency by 40%.",),
        )
        guidance = build_resume_guidance(
            candidate_items=(item,), job_requirement_skills=frozenset({"python", "kafka"})
        )
        draft = build_cover_letter_draft(
            full_name="Ada Lovelace",
            job_title="Backend Engineer",
            company_name="Acme",
            best_match=guidance[0],
        )
        assert draft.needs_human is False
        assert "Platform Engineer" in draft.text
        assert "Acme Corp" in draft.text  # the item's own organisation, not the employer
        assert "Reduced pipeline latency by 40%." in draft.text
        assert "Ada Lovelace" in draft.text
        assert len(draft.evidence_refs) == 1

    def test_never_cites_a_highlight_that_was_not_in_the_confirmed_item(self) -> None:
        item = _item(title="Side Project", skills=frozenset({"python"}), highlights=())
        guidance = build_resume_guidance(
            candidate_items=(item,), job_requirement_skills=frozenset({"python"})
        )
        with_highlight = _item(
            title="Other Project",
            skills=frozenset({"python"}),
            highlights=("Shipped a thing nobody asked for.",),
        )
        guidance_with_highlight = build_resume_guidance(
            candidate_items=(with_highlight,), job_requirement_skills=frozenset({"python"})
        )
        draft = build_cover_letter_draft(
            full_name="Ada", job_title="Engineer", company_name="Acme", best_match=guidance[0]
        )
        draft_with_highlight = build_cover_letter_draft(
            full_name="Ada",
            job_title="Engineer",
            company_name="Acme",
            best_match=guidance_with_highlight[0],
        )
        # No highlight paragraph is fabricated when the confirmed record had none —
        # the draft without a highlight has one fewer paragraph than one with.
        assert draft.text.count("\n\n") < draft_with_highlight.text.count("\n\n")


class TestQuestionDraft:
    def test_unknown_question_key_raises(self) -> None:
        with pytest.raises(ValueError):
            build_question_draft(
                "not_a_real_key", job_title="Engineer", company_name="Acme", best_match=None
            )

    def test_no_confirmed_match_needs_human(self) -> None:
        draft = build_question_draft(
            "why_interested", job_title="Engineer", company_name="Acme", best_match=None
        )
        assert draft.needs_human is True

    def test_relevant_experience_cites_the_matched_record(self) -> None:
        item = _item(
            kind="experience", title="Data Engineer", skills=frozenset({"airflow"})
        )
        guidance = build_resume_guidance(
            candidate_items=(item,), job_requirement_skills=frozenset({"airflow"})
        )
        draft = build_question_draft(
            "relevant_experience", job_title="Engineer", company_name="Acme",
            best_match=guidance[0],
        )
        assert "Data Engineer" in draft.text
        assert draft.needs_human is False


class TestReadinessChecklist:
    def test_no_outstanding_fields_reports_ready(self) -> None:
        items = build_readiness_checklist(
            outstanding_field_labels=(),
            missing_requirement_texts=(),
            uncertain_requirement_texts=(),
            cover_letter_status="ready",
            question_statuses=(),
        )
        profile_item = next(i for i in items if i.category == "profile")
        assert profile_item.status == "ready"

    def test_outstanding_fields_and_missing_requirements_are_both_surfaced(self) -> None:
        items = build_readiness_checklist(
            outstanding_field_labels=("Phone number",),
            missing_requirement_texts=("5+ years of Go",),
            uncertain_requirement_texts=(),
            cover_letter_status="missing",
            question_statuses=(("why_interested", "missing"),),
        )
        labels_and_status = {(i.label, i.status) for i in items}
        assert ("Phone number", "missing") in labels_and_status
        assert ("5+ years of Go", "missing") in labels_and_status
        assert any(i.category == "cover_letter" and i.status == "missing" for i in items)
        assert any(i.category == "questions" and i.status == "missing" for i in items)

    def test_never_invents_a_requirement_or_field_not_passed_in(self) -> None:
        items = build_readiness_checklist(
            outstanding_field_labels=(),
            missing_requirement_texts=(),
            uncertain_requirement_texts=(),
            cover_letter_status="ready",
            question_statuses=(),
        )
        assert all(i.category in ("profile", "cover_letter") for i in items)
