"""The Greenhouse connector: fetching, parsing, and normalising.

Every test here uses ``httpx.MockTransport`` — built into httpx itself, so no
mocking library needed — to intercept the outbound HTTP call. Nothing in this
module touches the real network or a database; it belongs entirely in
``tests/unit``.

The field mapping exercised here was verified against two independent real
Greenhouse boards before being written — see the module docstring in
``app/connectors/greenhouse.py`` for what was checked and against what.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from app.connectors.base import (
    ConnectorHTTPError,
    ConnectorParseError,
    ConnectorTimeoutError,
)
from app.connectors.greenhouse import GreenhouseConnector
from app.domain.normalize import EmploymentType, PostedAtPrecision, SourceStatus, WorkArrangement

pytestmark = pytest.mark.unit


def _connector(handler: object) -> GreenhouseConnector:
    return GreenhouseConnector(
        user_agent="CareerPilot-Test/1.0",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def _json_response(status_code: int, payload: object) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler


_GOOD_JOB = {
    "id": 8126988,
    "title": "Backend Engineer",
    "company_name": "Acme Corp",
    "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/8126988",
    "location": {"name": "Remote, India"},
    "content": "<p>Build backend services.</p><ul><li>Python</li><li>PostgreSQL</li></ul>",
    "first_published": "2026-08-24T13:52:04-04:00",
    "updated_at": "2026-10-09T18:28:27-04:00",
    "metadata": None,
}


class TestFetchJobsHappyPath:
    async def test_a_well_formed_job_is_normalised_correctly(self) -> None:
        connector = _connector(_json_response(200, {"jobs": [_GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source == "greenhouse"
        assert job.source_job_id == "8126988"
        assert job.company_name == "Acme Corp"
        assert job.title == "Backend Engineer"
        assert job.application_url == "https://job-boards.greenhouse.io/acme/jobs/8126988"
        assert job.location_raw == "Remote, India"
        assert job.source_status is SourceStatus.OPEN

    async def test_the_description_is_plain_text_not_html(self) -> None:
        connector = _connector(_json_response(200, {"jobs": [_GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].description is not None
        assert "<p>" not in jobs[0].description
        assert "<li>" not in jobs[0].description
        assert "Build backend services." in jobs[0].description

    async def test_first_published_becomes_an_exact_posted_at(self) -> None:
        connector = _connector(_json_response(200, {"jobs": [_GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        job = jobs[0]
        assert job.posted_at is not None
        assert job.posted_at.tzinfo is not None
        assert job.posted_at_precision is PostedAtPrecision.EXACT
        assert job.posted_at == datetime.fromisoformat("2026-08-24T13:52:04-04:00")

    async def test_remote_location_is_classified_as_remote(self) -> None:
        connector = _connector(_json_response(200, {"jobs": [_GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].work_arrangement is WorkArrangement.REMOTE

    async def test_multiple_jobs_are_all_returned(self) -> None:
        second = {**_GOOD_JOB, "id": 999, "title": "Frontend Engineer"}
        connector = _connector(_json_response(200, {"jobs": [_GOOD_JOB, second]}))
        jobs = await connector.fetch_jobs("acme")
        assert {j.source_job_id for j in jobs} == {"8126988", "999"}

    async def test_an_empty_jobs_array_returns_an_empty_list_not_an_error(self) -> None:
        connector = _connector(_json_response(200, {"jobs": []}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs == []

    async def test_an_internship_title_is_classified_as_internship(self) -> None:
        job = {**_GOOD_JOB, "title": "Software Engineering Intern - Summer 2027"}
        connector = _connector(_json_response(200, {"jobs": [job]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].employment_type is EmploymentType.INTERNSHIP


class TestMissingOptionalFields:
    async def test_missing_location_is_left_unknown_not_guessed(self) -> None:
        job = {**_GOOD_JOB, "location": None}
        connector = _connector(_json_response(200, {"jobs": [job]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].location_raw is None

    async def test_missing_content_leaves_description_none(self) -> None:
        job = {**_GOOD_JOB, "content": None}
        connector = _connector(_json_response(200, {"jobs": [job]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].description is None

    async def test_missing_first_published_leaves_the_date_unknown(self) -> None:
        """The posting-date invariant: absent means unknown, never 'posted today'."""
        job = {**_GOOD_JOB, "first_published": None}
        connector = _connector(_json_response(200, {"jobs": [job]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].posted_at is None
        assert jobs[0].posted_at_precision is PostedAtPrecision.UNKNOWN

    async def test_unparseable_first_published_leaves_the_date_unknown(self) -> None:
        job = {**_GOOD_JOB, "first_published": "not-a-real-timestamp"}
        connector = _connector(_json_response(200, {"jobs": [job]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].posted_at is None
        assert jobs[0].posted_at_precision is PostedAtPrecision.UNKNOWN

    async def test_missing_company_name_falls_back_to_the_board_token(self) -> None:
        job = {**_GOOD_JOB, "company_name": None}
        connector = _connector(_json_response(200, {"jobs": [job]}))
        jobs = await connector.fetch_jobs("acme")
        assert jobs[0].company_name == "acme"


class TestPartialFailurePerJob:
    """One bad entry in a board's list must not take down the rest of the board."""

    async def test_a_job_missing_its_id_is_skipped_not_fatal(self) -> None:
        bad = {**_GOOD_JOB}
        del bad["id"]
        connector = _connector(_json_response(200, {"jobs": [bad, _GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        assert len(jobs) == 1
        assert jobs[0].source_job_id == "8126988"

    async def test_a_job_missing_its_title_is_skipped_not_fatal(self) -> None:
        bad = {**_GOOD_JOB, "id": 1, "title": None}
        connector = _connector(_json_response(200, {"jobs": [bad, _GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        assert len(jobs) == 1

    async def test_a_job_missing_its_application_url_is_skipped_not_fatal(self) -> None:
        bad = {**_GOOD_JOB, "id": 2, "absolute_url": None}
        connector = _connector(_json_response(200, {"jobs": [bad, _GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        assert len(jobs) == 1

    async def test_a_non_object_entry_in_the_jobs_array_is_skipped_not_fatal(self) -> None:
        connector = _connector(_json_response(200, {"jobs": ["not a job object", _GOOD_JOB]}))
        jobs = await connector.fetch_jobs("acme")
        assert len(jobs) == 1


class TestWholeBoardFailures:
    async def test_a_404_raises_a_non_retryable_http_error(self) -> None:
        connector = _connector(_json_response(404, {"error": "Board not found"}))
        with pytest.raises(ConnectorHTTPError) as caught:
            await connector.fetch_jobs("does-not-exist")
        assert caught.value.status_code == 404
        assert caught.value.retryable is False

    async def test_a_500_raises_a_retryable_http_error(self) -> None:
        connector = _connector(_json_response(500, {"error": "Internal error"}))
        with pytest.raises(ConnectorHTTPError) as caught:
            await connector.fetch_jobs("acme")
        assert caught.value.status_code == 500
        assert caught.value.retryable is True

    async def test_a_429_raises_a_retryable_http_error(self) -> None:
        connector = _connector(_json_response(429, {"error": "Too many requests"}))
        with pytest.raises(ConnectorHTTPError) as caught:
            await connector.fetch_jobs("acme")
        assert caught.value.retryable is True

    async def test_invalid_json_raises_a_non_retryable_parse_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json at all {{{")

        connector = _connector(handler)
        with pytest.raises(ConnectorParseError) as caught:
            await connector.fetch_jobs("acme")
        assert caught.value.retryable is False

    async def test_a_json_array_instead_of_an_object_raises_a_parse_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps([1, 2, 3]).encode())

        connector = _connector(handler)
        with pytest.raises(ConnectorParseError):
            await connector.fetch_jobs("acme")

    async def test_a_response_with_no_jobs_key_raises_a_parse_error(self) -> None:
        connector = _connector(_json_response(200, {"unexpected": "shape"}))
        with pytest.raises(ConnectorParseError):
            await connector.fetch_jobs("acme")

    async def test_jobs_key_present_but_not_a_list_raises_a_parse_error(self) -> None:
        connector = _connector(_json_response(200, {"jobs": "not-a-list"}))
        with pytest.raises(ConnectorParseError):
            await connector.fetch_jobs("acme")

    async def test_a_timeout_raises_a_retryable_timeout_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("connection timed out", request=request)

        connector = _connector(handler)
        with pytest.raises(ConnectorTimeoutError) as caught:
            await connector.fetch_jobs("acme")
        assert caught.value.retryable is True

    async def test_a_transport_error_raises_a_retryable_timeout_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        connector = _connector(handler)
        with pytest.raises(ConnectorTimeoutError):
            await connector.fetch_jobs("acme")

    async def test_a_blank_board_token_is_refused_before_any_request(self) -> None:
        connector = _connector(_json_response(200, {"jobs": []}))
        with pytest.raises(ConnectorParseError):
            await connector.fetch_jobs("   ")


class TestRequestShape:
    async def test_the_request_asks_for_content_and_carries_the_configured_user_agent(
        self,
    ) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["user_agent"] = request.headers.get("user-agent")
            return httpx.Response(200, json={"jobs": []})

        connector = GreenhouseConnector(
            user_agent="CareerPilot-Test/1.0",
            transport=httpx.MockTransport(handler),
        )
        await connector.fetch_jobs("acme")

        assert "content=true" in str(seen["url"])
        assert "/boards/acme/jobs" in str(seen["url"])
        assert seen["user_agent"] == "CareerPilot-Test/1.0"

