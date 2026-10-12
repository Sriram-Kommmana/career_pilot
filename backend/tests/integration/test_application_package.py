"""``POST /applications/{id}/prepare-package`` and ``POST /applications/{id}/answers``.

Exercises the real HTTP endpoints, the real database, and the real
``app.services.applications`` machinery (``prepare``, ``_recompute_form_state``,
the submission gate) — none of it mocked. The one thing this module exists to
prove above everything else: generating or editing a draft never weakens the
existing approval-invalidation or ownership safeguards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.normalize import (
    EmploymentType,
    NormalizedJob,
    PostedAtPrecision,
    SourceStatus,
    WorkArrangement,
)
from app.services.jobs import upsert_job
from tests.conftest import advance_to_waiting_for_approval

pytestmark = pytest.mark.integration


async def _user_id(client: object, api: str, headers: dict[str, str]) -> uuid.UUID:
    response = await client.get(f"{api}/auth/me", headers=headers)  # type: ignore[attr-defined]
    return uuid.UUID(response.json()["id"])


async def _seed_job(
    session: object,
    *,
    source_job_id: str,
    requirements: tuple[str, ...] = ("Experience with Python", "Experience with Kafka"),
    company_name: str = "Acme Corp",
    title: str = "Backend Engineer",
) -> Any:
    normalized = NormalizedJob(
        source="demo_seed",
        source_job_id=source_job_id,
        company_name=company_name,
        title=title,
        location_raw="Remote",
        application_url=f"https://example.invalid/apply/{source_job_id}",
        requirements=requirements,
        work_arrangement=WorkArrangement.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        posted_at=datetime(2026, 6, 1, tzinfo=UTC),
        posted_at_precision=PostedAtPrecision.DATE_ONLY,
        source_status=SourceStatus.OPEN,
    )
    job, _ = await upsert_job(
        session,  # type: ignore[arg-type]
        normalized=normalized,
        source_external_key="test-suite",
        is_demo_data=False,
    )
    # Committed, not just flushed: the HTTP `client` fixture issues requests on a
    # separate session/connection, which under READ COMMITTED cannot see this job
    # until the transaction that created it actually commits.
    await session.commit()  # type: ignore[attr-defined]
    return job


async def _give_confirmed_experience(
    client: object,
    api: str,
    auth: dict[str, str],
    *,
    title: str = "Platform Engineer",
    company: str = "Fixture Systems",
    skills_used: tuple[str, ...] = ("Python", "Kafka"),
    highlights: tuple[str, ...] = ("Cut pipeline latency by 40%.",),
) -> None:
    response = await client.post(  # type: ignore[attr-defined]
        f"{api}/profile/experience",
        headers=auth,
        json={
            "company": company,
            "title": title,
            "skills_used": list(skills_used),
            "highlights": list(highlights),
        },
    )
    assert response.status_code == 201, response.text


async def _create_and_prepare(
    client: object, api: str, auth: dict[str, str], job_id: object
) -> dict[str, Any]:
    created = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications", headers=auth, json={"job_id": str(job_id)}
    )
    assert created.status_code == 201, created.text
    return created.json()  # type: ignore[no-any-return]


class TestPackageGeneration:
    async def test_confirmed_evidence_produces_a_grounded_package(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-grounded")
        await _give_confirmed_experience(client, api, auth)
        application = await _create_and_prepare(client, api, auth, job.id)

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["fit_summary"]["score_kind"] == "RANKING_HEURISTIC"
        assert "probability" in body["fit_summary"]["score_meaning"].lower()

        assert len(body["resume_guidance"]) == 1
        guidance_item = body["resume_guidance"][0]
        assert guidance_item["title"] == "Platform Engineer"
        assert set(guidance_item["matched_skills"]) == {"python", "kafka"}

        cover_letter = next(
            f for f in body["application"]["fields"] if f["field_key"] == "cover_letter"
        )
        assert cover_letter["value_provenance"] == "GENERATED_UNVERIFIED"
        # A real draft is resolution=RESOLVED (there IS a value now) even though
        # it isn't yet confirmed — resolution and provenance are independent.
        assert cover_letter["resolution"] == "RESOLVED"
        assert "Platform Engineer" in cover_letter["value_masked"]
        assert "Cut pipeline latency by 40%." in cover_letter["value_masked"]

        answers = {a["question_key"]: a for a in body["application"]["answers"]}
        assert set(answers) == {"why_interested", "relevant_experience"}
        for answer in answers.values():
            assert answer["is_generated"] is True
            assert answer["provenance"] == "GENERATED_UNVERIFIED"
            assert "Platform Engineer" in answer["answer_text"]

        statuses = {item["category"]: item["status"] for item in body["readiness_checklist"]}
        assert statuses["cover_letter"] == "needs_attention"

    async def test_insufficient_evidence_produces_a_placeholder_not_a_guess(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-insufficient")
        application = await _create_and_prepare(client, api, auth, job.id)

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        assert response.status_code == 200
        body = response.json()

        assert body["resume_guidance"] == []
        cover_letter = next(
            f for f in body["application"]["fields"] if f["field_key"] == "cover_letter"
        )
        assert "Acme Corp" not in cover_letter["value_masked"]
        assert cover_letter["value_provenance"] == "GENERATED_UNVERIFIED"
        # A placeholder is NEEDS_HUMAN, not RESOLVED — it must prompt the user to
        # write it themselves, not look like a usable draft.
        assert cover_letter["resolution"] == "NEEDS_HUMAN"
        for answer in body["application"]["answers"]:
            assert answer["needs_human"] is True

    async def test_unconfirmed_resume_evidence_never_leaks_into_the_package(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """An extracted-but-never-confirmed resume fact must not read as a fact."""
        job = await _seed_job(session, source_job_id="pkg-unconfirmed")
        resume_text = (
            "Experience\n"
            "Staff Secret Weapon Engineer - Totally Unconfirmed Inc\n"
            "Mar 2020 - Present\n"
            "Operated a Rustlang quantum pipeline.\n"
        )
        upload = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes",
            headers=auth,
            files={"file": ("resume.txt", resume_text.encode(), "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        extract = await client.post(  # type: ignore[attr-defined]
            f"{api}/resumes/{upload.json()['id']}/extract", headers=auth
        )
        assert extract.status_code == 200
        # Deliberately never confirmed.

        application = await _create_and_prepare(client, api, auth, job.id)
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        assert response.status_code == 200
        body = response.json()

        rendered = (
            str(body["application"]["fields"])
            + str(body["application"]["answers"])
            + str(body["resume_guidance"])
        )
        assert "Totally Unconfirmed Inc" not in rendered
        assert "Staff Secret Weapon Engineer" not in rendered

    async def test_regenerating_never_overwrites_a_user_edited_draft(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-no-clobber")
        await _give_confirmed_experience(client, api, auth)
        application = await _create_and_prepare(client, api, auth, job.id)

        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answer",
            headers=auth,
            json={"field_key": "cover_letter", "value": "My own hand-written letter."},
        )
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answers",
            headers=auth,
            json={"question_key": "why_interested", "value": "My own reason."},
        )

        again = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        body = again.json()
        cover_letter = next(
            f for f in body["application"]["fields"] if f["field_key"] == "cover_letter"
        )
        assert cover_letter["value_masked"] == "My own hand-written letter."
        assert cover_letter["value_provenance"] == "USER_DIRECT_INPUT"
        why_interested = next(
            a for a in body["application"]["answers"] if a["question_key"] == "why_interested"
        )
        assert why_interested["answer_text"] == "My own reason."
        assert why_interested["provenance"] == "USER_DIRECT_INPUT"
        # The other, un-edited question draft is still (re)generated.
        relevant = next(
            a
            for a in body["application"]["answers"]
            if a["question_key"] == "relevant_experience"
        )
        assert relevant["provenance"] == "GENERATED_UNVERIFIED"

    async def test_job_specific_tailoring_cites_different_evidence_per_job(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        python_job = await _seed_job(
            session, source_job_id="pkg-tailor-python", requirements=("Experience with Python",)
        )
        go_job = await _seed_job(
            session, source_job_id="pkg-tailor-go", requirements=("Experience with Go",)
        )
        await _give_confirmed_experience(
            client, api, auth, title="Python Engineer", skills_used=("Python",)
        )
        await _give_confirmed_experience(
            client, api, auth, title="Go Engineer", company="Gopher Inc", skills_used=("Go",)
        )

        python_app = await _create_and_prepare(client, api, auth, python_job.id)
        go_app = await _create_and_prepare(client, api, auth, go_job.id)

        python_pkg = (
            await client.post(  # type: ignore[attr-defined]
                f"{api}/applications/{python_app['id']}/prepare-package", headers=auth
            )
        ).json()
        go_pkg = (
            await client.post(  # type: ignore[attr-defined]
                f"{api}/applications/{go_app['id']}/prepare-package", headers=auth
            )
        ).json()

        assert python_pkg["resume_guidance"][0]["title"] == "Python Engineer"
        assert go_pkg["resume_guidance"][0]["title"] == "Go Engineer"

    async def test_an_application_that_was_never_prepared_is_prepared_automatically(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-auto-prepare")
        created = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications", headers=auth, json={"job_id": str(job.id)}
        )
        assert created.json()["fields"] == []

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{created.json()['id']}/prepare-package", headers=auth
        )
        assert response.status_code == 200
        assert len(response.json()["application"]["fields"]) > 0

    async def test_a_terminal_application_refuses_package_generation(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-terminal")
        application = await _create_and_prepare(client, api, auth, job.id)
        cancelled = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/cancel", headers=auth, json={}
        )
        assert cancelled.status_code == 200

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        assert response.status_code == 409

    async def test_a_missing_application_is_404(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{uuid.uuid4()}/prepare-package", headers=auth
        )
        assert response.status_code == 404


class TestOwnership:
    async def test_a_second_user_cannot_generate_or_view_anothers_package(
        self,
        session: object,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-ownership")
        application = await _create_and_prepare(client, api, auth, job.id)

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=second_auth
        )
        assert response.status_code == 404

    async def test_a_second_user_cannot_answer_anothers_question(
        self,
        session: object,
        client: object,
        api: str,
        auth: dict[str, str],
        second_auth: dict[str, str],
    ) -> None:
        job = await _seed_job(session, source_job_id="pkg-ownership-answer")
        application = await _create_and_prepare(client, api, auth, job.id)
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answers",
            headers=second_auth,
            json={"question_key": "why_interested", "value": "Not mine to answer."},
        )
        assert response.status_code == 404


class TestAnswerQuestion:
    async def test_answering_sets_user_direct_input(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="answer-happy-path")
        application = await _create_and_prepare(client, api, auth, job.id)
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answers",
            headers=auth,
            json={"question_key": "why_interested", "value": "Because I said so."},
        )
        assert response.status_code == 200
        answer = next(
            a
            for a in response.json()["answers"]
            if a["question_key"] == "why_interested"
        )
        assert answer["answer_text"] == "Because I said so."
        assert answer["provenance"] == "USER_DIRECT_INPUT"
        assert answer["is_generated"] is False
        assert answer["user_edited_at"] is not None

    async def test_an_unknown_question_key_is_404(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="answer-unknown-key")
        application = await _create_and_prepare(client, api, auth, job.id)
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answers",
            headers=auth,
            json={"question_key": "not_a_real_question", "value": "Anything."},
        )
        assert response.status_code == 404

    async def test_an_empty_value_is_rejected(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        job = await _seed_job(session, source_job_id="answer-empty-value")
        application = await _create_and_prepare(client, api, auth, job.id)
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answers",
            headers=auth,
            json={"question_key": "why_interested", "value": "   "},
        )
        assert response.status_code == 422


class TestApprovalSafeguardRegression:
    async def test_editing_a_drafted_answer_after_approval_revokes_it(
        self, session: object, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """The sharpest test in this module: Phase 4B must not weaken
        docs/SAFETY.md section 3. Editing a drafted answer after approval must
        invalidate that approval exactly like editing a field already does."""
        job = await _seed_job(session, source_job_id="pkg-approval-safeguard")
        await _give_confirmed_experience(client, api, auth)
        application = await _create_and_prepare(client, api, auth, job.id)
        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/prepare-package", headers=auth
        )
        user_id = await _user_id(client, api, auth)

        first_hash = await advance_to_waiting_for_approval(
            session,
            user_id=user_id,
            application_id=uuid.UUID(application["id"]),
        )
        await session.commit()  # type: ignore[attr-defined]

        await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/approve",
            headers=auth,
            json={"form_state_hash": first_hash},
        )

        changed = await client.post(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/answers",
            headers=auth,
            json={"question_key": "why_interested", "value": "A rewritten answer."},
        )
        assert changed.status_code == 200
        assert changed.json()["form_state_hash"] != first_hash

        review = await client.get(  # type: ignore[attr-defined]
            f"{api}/applications/{application['id']}/review", headers=auth
        )
        assert review.json()["decision"]["allowed"] is False
        assert "NO_APPROVAL" in [b["code"] for b in review.json()["decision"]["blockers"]]
