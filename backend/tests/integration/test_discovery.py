"""``POST /jobs/discover`` end to end: the real endpoint, the real database, and a
mocked Greenhouse API.

The outbound HTTP call is the only thing mocked, via ``app.services.discovery``'s
one connector-construction seam (``_build_connector``) — everything downstream of
that (upsert, deduplication, closed-job detection, ``AgentRun``/``ToolExecution``
recording, the HTTP response shape) is the real code path, exercised through the
real FastAPI app and a real Postgres.

No live network call to Greenhouse is made anywhere in this suite — see the
completion report for what was, and was not, verified against the real service.
"""

from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.integration


def _board_response(jobs: list[dict[str, object]], *, status_code: int = 200) -> dict[str, object]:
    return {"status_code": status_code, "body": {"jobs": jobs}}


def _job(
    job_id: int,
    *,
    title: str | None = None,
    company: str = "Acme Corp",
    url_suffix: str | None = None,
) -> dict[str, object]:
    """A canned Greenhouse job payload.

    ``title`` defaults to something unique per ``job_id`` rather than a fixed
    string: two distinct postings at the same company with an identical
    title *and* location are exactly what the cross-source dedup heuristic
    (carried over from Phase 1) is designed to collapse into one feed row,
    which would make these fixtures look like one job to the feed/listing
    endpoints even though two separate rows exist. Giving each a distinct
    title avoids tripping that heuristic for tests that aren't about dedup.
    """
    return {
        "id": job_id,
        "title": title or f"Backend Engineer {job_id}",
        "company_name": company,
        "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{url_suffix or job_id}",
        "location": {"name": "Remote, India"},
        "content": f"<p>Description for job {job_id}.</p>",
        "first_published": "2026-08-24T13:52:04-04:00",
        "updated_at": "2026-08-24T13:52:04-04:00",
        "metadata": None,
    }


def _install_mock_connector(
    monkeypatch: pytest.MonkeyPatch, board_responses: dict[str, object]
) -> list[str]:
    """Patch the one seam that constructs a connector, so discovery fetches from
    canned per-board responses instead of the real network.

    ``board_responses`` maps a board token to either a dict from ``_board_response``
    (a canned JSON response) or the string ``"timeout"`` / ``"malformed"`` for the
    corresponding failure mode. Returns the list of request paths actually made, so
    a test can assert on *how many times* a board was hit (for idempotency and
    rate-limit-adjacent checks).
    """
    import app.services.discovery as discovery_module
    from app.connectors.greenhouse import GreenhouseConnector

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        board_token = request.url.path.rstrip("/").split("/")[-2]
        spec = board_responses.get(board_token)
        if spec is None:
            return httpx.Response(404, json={"error": "not found"})
        if spec == "timeout":
            raise httpx.ConnectTimeout("simulated timeout", request=request)
        if spec == "malformed":
            return httpx.Response(200, content=b"not valid json {{{")
        assert isinstance(spec, dict)
        return httpx.Response(spec["status_code"], json=spec["body"])

    transport = httpx.MockTransport(handler)

    def fake_build_connector(kind: str, settings: object) -> GreenhouseConnector:
        return GreenhouseConnector(
            user_agent="CareerPilot-Test/1.0",
            timeout_seconds=1.0,
            transport=transport,
        )

    monkeypatch.setattr(discovery_module, "_build_connector", fake_build_connector)
    return requested_paths


class TestSuccessfulDiscovery:
    async def test_creates_jobs_with_correct_fields(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(
            monkeypatch,
            {"acme": _board_response([_job(1), _job(2)])},
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["sources"]) == 1
        result = body["sources"][0]
        assert result["status"] == "ok"
        assert result["kind"] == "greenhouse"
        assert result["source"] == "acme"
        assert result["jobs_created"] == 2
        assert result["jobs_updated"] == 0
        assert result["jobs_closed"] == 0
        assert result["error"] is None

        feed = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        items = feed.json()["items"]
        assert len(items) == 2
        job = next(i["job"] for i in items if "1" in i["job"]["application_url"])
        assert job["source"] == "greenhouse"
        assert job["is_demo_data"] is False
        assert job["posted_at"] is not None
        assert job["posted_at_precision"] == "EXACT"

    async def test_an_empty_sources_list_with_nothing_configured_does_nothing(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {})
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": []}
        )
        assert response.status_code == 200
        assert response.json()["sources"] == []

    async def test_an_empty_sources_list_re_runs_previously_configured_boards(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested = _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1)])})

        first = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )
        assert first.json()["sources"][0]["status"] == "ok"

        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": []}
        )
        assert second.status_code == 200
        assert second.json()["sources"][0]["source"] == "acme"
        # Two real fetches happened: the explicit one, then the re-run.
        assert len(requested) == 2


class TestIdempotency:
    async def test_rerunning_the_same_board_updates_rather_than_duplicates(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1), _job(2)])})

        await client.post(f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]})  # type: ignore[attr-defined]
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )

        result = second.json()["sources"][0]
        assert result["jobs_created"] == 0
        assert result["jobs_updated"] == 2

        feed = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        assert feed.json()["total"] == 2


class TestClosedJobDetection:
    async def test_a_job_missing_from_a_later_fetch_is_closed_not_deleted(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1), _job(2)])})
        await client.post(f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]})  # type: ignore[attr-defined]

        feed = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        job_2_id = next(
            i["job"]["id"] for i in feed.json()["items"] if "2" in i["job"]["application_url"]
        )

        # Second fetch: job 2 has closed and no longer appears in the listing.
        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1)])})
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )
        assert second.json()["sources"][0]["jobs_closed"] == 1

        detail = await client.get(f"{api}/jobs/{job_2_id}", headers=auth)  # type: ignore[attr-defined]
        assert detail.status_code == 200, "the row must still exist, only its status changes"
        assert detail.json()["job"]["source_status"] == "CLOSED"

    async def test_a_source_that_fails_on_re_fetch_does_not_close_its_own_jobs(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_run_one_source`` returns on a ``ConnectorError`` before it ever reaches
        ``_close_missing_jobs`` — a board that goes from reachable to failing must
        never be read as "every posting on this board just disappeared"."""
        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1), _job(2)])})
        await client.post(f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]})  # type: ignore[attr-defined]

        feed = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        job_ids = [i["job"]["id"] for i in feed.json()["items"]]
        assert len(job_ids) == 2

        _install_mock_connector(monkeypatch, {"acme": "timeout"})
        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )
        assert second.json()["sources"][0]["status"] == "failed"

        for job_id in job_ids:
            detail = await client.get(f"{api}/jobs/{job_id}", headers=auth)  # type: ignore[attr-defined]
            assert detail.json()["job"]["source_status"] == "OPEN", (
                "a failed re-fetch must not close jobs from the same, now-unreachable source"
            )


class TestPartialFailure:
    async def test_one_failing_board_does_not_affect_another_in_the_same_request(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(
            monkeypatch,
            {
                "goodboard": _board_response([_job(1, company="Good Co")]),
                "badboard": "malformed",
            },
        )

        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["goodboard", "badboard"]}
        )
        assert response.status_code == 200, "one bad source must not fail the whole request"
        by_source = {r["source"]: r for r in response.json()["sources"]}
        assert by_source["goodboard"]["status"] == "ok"
        assert by_source["goodboard"]["jobs_created"] == 1
        assert by_source["badboard"]["status"] == "failed"
        assert by_source["badboard"]["error"]

        feed = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        assert feed.json()["total"] == 1

    async def test_a_timeout_is_recorded_as_a_clean_failure_not_a_500(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {"slowboard": "timeout"})
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["slowboard"]}
        )
        assert response.status_code == 200
        result = response.json()["sources"][0]
        assert result["status"] == "failed"
        assert "timed out" in result["error"].lower() or "reach" in result["error"].lower()

    async def test_a_malformed_response_does_not_corrupt_previously_stored_jobs(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1), _job(2)])})
        await client.post(f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]})  # type: ignore[attr-defined]

        before = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        before_items = {i["job"]["id"]: i["job"] for i in before.json()["items"]}
        assert len(before_items) == 2

        _install_mock_connector(monkeypatch, {"acme": "malformed"})
        broken = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )
        assert broken.json()["sources"][0]["status"] == "failed"

        after = await client.get(f"{api}/jobs", headers=auth, params={"source": "greenhouse"})  # type: ignore[attr-defined]
        after_items = {i["job"]["id"]: i["job"] for i in after.json()["items"]}
        assert after_items == before_items, "a malformed response must not touch existing rows"


class TestDemoDataIsolation:
    async def test_discovery_never_touches_seeded_demo_jobs(
        self,
        client: object,
        api: str,
        auth: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        seeded_job: object,
    ) -> None:
        seeded_job_id = seeded_job.id  # type: ignore[attr-defined]
        before = await client.get(f"{api}/jobs/{seeded_job_id}", headers=auth)  # type: ignore[attr-defined]
        assert before.json()["job"]["is_demo_data"] is True

        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1)])})
        await client.post(f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]})  # type: ignore[attr-defined]

        after = await client.get(f"{api}/jobs/{seeded_job_id}", headers=auth)  # type: ignore[attr-defined]
        assert after.json() == before.json(), (
            "demo data must be byte-identical after a discovery run"
        )


class TestObservability:
    async def test_a_discovery_run_is_recorded_as_an_agent_run(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1)])})
        discover = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["acme"]}
        )
        run_id = discover.json()["agent_run_id"]

        run = await client.get(f"{api}/agent-runs/{run_id}", headers=auth)  # type: ignore[attr-defined]
        assert run.status_code == 200
        body = run.json()
        assert body["kind"] == "discover"
        assert body["status"] == "succeeded"
        assert body["summary"]
        assert len(body["tool_executions"]) == 1
        tool = body["tool_executions"][0]
        assert tool["tool_name"] == "greenhouse.fetch_jobs"
        assert tool["status"] == "succeeded"
        # Redacted: counts only, never raw page content.
        assert "content" not in json.dumps(tool["result_redacted"]).lower()

    async def test_a_failed_source_is_recorded_as_a_failed_tool_execution(
        self, client: object, api: str, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mock_connector(monkeypatch, {"badboard": "malformed"})
        discover = await client.post(  # type: ignore[attr-defined]
            f"{api}/jobs/discover", headers=auth, json={"sources": ["badboard"]}
        )
        run_id = discover.json()["agent_run_id"]

        run = await client.get(f"{api}/agent-runs/{run_id}", headers=auth)  # type: ignore[attr-defined]
        tool = run.json()["tool_executions"][0]
        assert tool["status"] == "failed"
        assert tool["error"]


class TestAuthentication:
    async def test_discover_requires_authentication(self, client: object, api: str) -> None:
        response = await client.post(f"{api}/jobs/discover", json={"sources": []})  # type: ignore[attr-defined]
        assert response.status_code == 401


class TestScheduledSweep:
    """``run_discovery(user_id=None, ...)`` — Phase 4A's scheduled sweep has no
    acting user, unlike every HTTP-triggered call. There is no endpoint for this;
    it is only ever reached via ``python -m app.cli sweep``."""

    async def test_a_run_with_no_acting_user_does_not_raise_and_is_recorded_ownerless(
        self, session: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.models import AgentRun
        from app.services import discovery as discovery_service

        _install_mock_connector(monkeypatch, {"acme": _board_response([_job(1)])})

        result = await discovery_service.run_discovery(
            session,  # type: ignore[arg-type]
            user_id=None,
            settings=get_settings(),
            board_tokens=["acme"],
        )
        assert result.sources[0].status == "ok"

        row = (
            await session.execute(  # type: ignore[attr-defined]
                select(AgentRun).where(AgentRun.id == result.agent_run_id)
            )
        ).scalar_one()
        assert row.user_id is None
        assert row.status == "succeeded"
