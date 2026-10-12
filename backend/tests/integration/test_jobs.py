"""Job feed, job detail, and analysis.

Exercises the pieces that only show up with a real database: cross-source
deduplication actually collapsing rows in a SQL query (the domain-level grouping
logic is unit tested separately), the demo-data flag surviving the full round trip,
and a hard-filtered job never carrying a score.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.normalize import (
    EmploymentType,
    NormalizedJob,
    PostedAtPrecision,
    SourceStatus,
    WorkArrangement,
)
from app.models import Job
from app.services.jobs import upsert_job

pytestmark = pytest.mark.integration


async def _seed(
    session: object,
    *,
    source: str = "demo_seed",
    source_job_id: str,
    company_name: str = "Testcorp Limited",
    title: str = "Backend Engineer",
    location_raw: str | None = "Bengaluru, India",
    requirements: tuple[str, ...] = ("Experience with Python",),
    employment_type: EmploymentType = EmploymentType.FULL_TIME,
    work_arrangement: WorkArrangement = WorkArrangement.HYBRID,
    source_status: SourceStatus = SourceStatus.OPEN,
    posted_at: datetime | None = datetime(2026, 6, 1, tzinfo=UTC),
    posted_at_precision: PostedAtPrecision = PostedAtPrecision.DATE_ONLY,
    application_url: str | None = None,
    external_key: str = "test-suite",
) -> Job:
    normalized = NormalizedJob(
        source=source,
        source_job_id=source_job_id,
        company_name=company_name,
        title=title,
        location_raw=location_raw,
        application_url=application_url or f"https://example.invalid/apply/{source_job_id}",
        requirements=requirements,
        work_arrangement=work_arrangement,
        employment_type=employment_type,
        posted_at=posted_at,
        posted_at_precision=posted_at_precision,
        source_status=source_status,
    )
    job, _ = await upsert_job(
        session,  # type: ignore[arg-type]
        normalized=normalized,
        source_external_key=external_key,
        is_demo_data=True,
    )
    await session.flush()  # type: ignore[attr-defined]
    return job


class TestFeed:
    async def test_the_seeded_job_appears_flagged_as_demo(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["job"]["is_demo_data"] is True

    async def test_an_unknown_posting_date_is_not_rendered_as_recent(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        await _seed(
            session,
            source_job_id="no-date",
            posted_at=None,
            posted_at_precision=PostedAtPrecision.UNKNOWN,
        )
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        job = response.json()["items"][0]["job"]
        assert job["posted_at"] is None
        assert job["posted_at_precision"] == "UNKNOWN"

    async def test_every_job_carries_a_stored_match_assessment(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        match = response.json()["items"][0]["match"]
        assert match is not None
        assert match["score_kind"] == "RANKING_HEURISTIC"
        # The label explicitly disclaims being a probability; it legitimately
        # contains that word as part of saying so.
        assert "ranking heuristic" in match["score_meaning"].lower()
        assert "not a probability" in match["score_meaning"].lower()

    async def test_cross_source_duplicates_collapse_to_one_feed_row(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        """The same role, listed on two sources, must appear once in the feed."""
        await _seed(
            session,
            source="greenhouse",
            source_job_id="gh-1",
            company_name="Lumen Systems",
            title="Full Stack Developer",
            location_raw="Remote - India",
            external_key="gh-board",
        )
        await _seed(
            session,
            source="career_page",
            source_job_id="cp-1",
            company_name="Lumen Systems Ltd.",
            title="Full Stack Developer (Remote)",
            location_raw="India, Remote",
            external_key="lumen-careers",
        )
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["job"]["source_count"] == 2
        # Greenhouse is preferred over a career-page wrapper.
        assert items[0]["job"]["source"] == "greenhouse"

    async def test_two_distinct_postings_from_the_same_source_stay_separate(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        """Two separate requisitions at one company, from the same source, can share
        title/company/location (e.g. two "Backend Engineer, Remote" openings). They
        have different ``source_job_id``s, so they are genuinely different postings
        and must not be collapsed into one feed row by the cross-source dedup
        heuristic — that heuristic is only for the same role listed on *different*
        sources (see ``test_cross_source_duplicates_collapse_to_one_feed_row``)."""
        await _seed(
            session,
            source="greenhouse",
            source_job_id="gh-req-1",
            company_name="Lumen Systems",
            title="Backend Engineer",
            location_raw="Remote - India",
            external_key="gh-board",
        )
        await _seed(
            session,
            source="greenhouse",
            source_job_id="gh-req-2",
            company_name="Lumen Systems",
            title="Backend Engineer",
            location_raw="Remote - India",
            external_key="gh-board",
        )
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        items = response.json()["items"]
        assert len(items) == 2
        # Neither is reported as "also listed elsewhere" — both are from one source.
        assert {i["job"]["source_count"] for i in items} == {1}

        detail = await client.get(  # type: ignore[attr-defined]
            f"{api}/jobs/{items[0]['job']['id']}", headers=auth
        )
        assert detail.json()["duplicates"] == []

    async def test_refetching_the_same_posting_does_not_create_a_duplicate_row(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        """Re-ingesting the same (source, source_job_id) twice — as a connector
        re-run would — must update the one existing row, not add a second one that
        would then look like a same-source fingerprint collision."""
        for _ in range(2):
            await _seed(
                session,
                source="greenhouse",
                source_job_id="gh-req-1",
                company_name="Lumen Systems",
                title="Backend Engineer",
                location_raw="Remote - India",
                external_key="gh-board",
            )
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["job"]["source_count"] == 1

    async def test_the_non_canonical_duplicate_is_kept_not_deleted(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        canonical = await _seed(
            session,
            source="greenhouse",
            source_job_id="gh-2",
            company_name="Lumen Systems",
            title="Full Stack Developer",
            external_key="gh-board",
        )
        await _seed(
            session,
            source="career_page",
            source_job_id="cp-2",
            company_name="Lumen Systems",
            title="Full Stack Developer",
            external_key="lumen-careers",
        )
        await session.commit()  # type: ignore[attr-defined]

        detail = await client.get(  # type: ignore[attr-defined]
            f"{api}/jobs/{canonical.id}", headers=auth
        )
        assert detail.status_code == 200
        assert len(detail.json()["duplicates"]) == 1
        # Each duplicate keeps its own application URL.
        assert (
            detail.json()["duplicates"][0]["application_url"]
            != detail.json()["job"]["application_url"]
        )

    async def test_a_closed_job_has_no_score_and_a_filter_failure(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        await _seed(session, source_job_id="closed-1", source_status=SourceStatus.CLOSED)
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(f"{api}/jobs", headers=auth)  # type: ignore[attr-defined]
        item = response.json()["items"][0]
        assert item["match"]["score"] is None
        assert item["match"]["passed_hard_filters"] is False
        codes = {f["code"] for f in item["match"]["hard_filter_failures"]}
        assert "JOB_CLOSED" in codes

    async def test_min_score_excludes_unscored_jobs_not_just_low_scored_ones(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        """A job that could not be scored is excluded, not treated as a zero."""
        await _seed(session, source_job_id="closed-2", source_status=SourceStatus.CLOSED)
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/jobs", headers=auth, params={"min_score": 0.0}
        )
        assert response.json()["items"] == []

    async def test_filter_by_employment_type(
        self, client: object, session: object, api: str, auth: dict[str, str]
    ) -> None:
        await _seed(session, source_job_id="intern-1", employment_type=EmploymentType.INTERNSHIP)
        await _seed(session, source_job_id="fulltime-1", employment_type=EmploymentType.FULL_TIME)
        await session.commit()  # type: ignore[attr-defined]

        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/jobs", headers=auth, params={"employment_type": "INTERNSHIP"}
        )
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["job"]["employment_type"] == "INTERNSHIP"

    async def test_unauthenticated_access_is_refused(
        self, client: object, api: str, seeded_job: object
    ) -> None:
        response = await client.get(f"{api}/jobs")  # type: ignore[attr-defined]
        assert response.status_code == 401


class TestDetailAndAnalyze:
    async def test_job_detail_includes_requirements(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/jobs/{seeded_job.id}",  # type: ignore[attr-defined]
            headers=auth,
        )
        assert response.status_code == 200
        assert response.json()["requirements"]

    async def test_a_missing_job_is_404(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/jobs/{uuid.uuid4()}", headers=auth
        )
        assert response.status_code == 404

    async def test_analyze_is_deterministic_across_calls(
        self, client: object, api: str, auth: dict[str, str], seeded_job: object
    ) -> None:
        job_id = seeded_job.id  # type: ignore[attr-defined]
        first = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/{job_id}/analyze", headers=auth
        )
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/{job_id}/analyze", headers=auth
        )
        assert first.json()["score"] == second.json()["score"]
        assert first.json()["explanation"] == second.json()["explanation"]

    async def test_discover_with_nothing_configured_returns_an_empty_result(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        """Phase 2's live discovery, with no board tokens and nothing pre-configured.

        An empty ``sources`` list means "re-run whatever is already configured" —
        with nothing configured yet, that is honestly an empty result, not an error
        and not a fabricated search. The full Greenhouse connector behaviour (mocked
        HTTP responses, retries, partial failures, closed-job detection) is covered
        in ``tests/integration/test_discovery.py``.
        """
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": []}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sources"] == []
        assert "agent_run_id" in body
