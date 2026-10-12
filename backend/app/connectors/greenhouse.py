"""The Greenhouse public job-board connector.

Fetches ``https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true``
— Greenhouse's own public, unauthenticated, documented JSON API, built for exactly
this kind of programmatic consumption. It is not a page we scrape: there is no HTML
to parse for the *listing*, no login, no access control to defeat. The per-job
``content`` field is HTML (the posting's own description) and is converted to plain
text with ``app.connectors.html_text``.

Field mapping was verified against two independent real boards
(greenhouse.io's own and gitlab's) before writing this, not assumed from
documentation alone:

====================  =======================================================
Greenhouse field      Maps to
====================  =======================================================
``id``                ``source_job_id`` (stable; Greenhouse's own primary key)
``title``             ``title``
``company_name``      ``company_name``
``absolute_url``      ``application_url`` — the canonical place to apply
``location.name``     ``location_raw``
``first_published``   ``posted_at`` (precision ``EXACT`` — a real source timestamp)
``content``            -> plain text -> ``description``
====================  =======================================================

Deliberately **not** mapped, because Greenhouse's public listing API does not
reliably provide them and guessing would mean inventing data:

- **Salary** — occasionally mentioned in free-text prose inside ``content``, but
  parsing a dollar figure out of unstructured text is exactly the kind of brittle
  heuristic that produces confidently wrong numbers. Left ``None``.
- **Structured requirements** — Greenhouse gives one HTML blob, not separate
  requirement/responsibility lists. Splitting ``<li>`` items into "requirements"
  would mislabel responsibility and benefits bullets as requirements. Left empty;
  the matching engine already handles "no stated requirements" honestly (every
  component that depends on them reports "not assessed", never zero — see
  ``app.domain.matching``).
- **Employment type / work arrangement** — inferred the same way the Phase 1 demo
  seed data is: ``domain.normalize.classify_employment_type`` and
  ``classify_work_arrangement`` over the title and location text. This is reuse,
  not a new heuristic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.connectors.base import (
    Connector,
    ConnectorHTTPError,
    ConnectorParseError,
    ConnectorTimeoutError,
)
from app.connectors.html_text import html_to_text
from app.core.logging import get_logger
from app.domain.normalize import (
    NormalizedJob,
    PostedAtPrecision,
    SourceStatus,
    classify_employment_type,
    classify_work_arrangement,
)

logger = get_logger(__name__)

SOURCE_KIND = "greenhouse"
_BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseConnector(Connector):
    """Fetches and normalises one board's public postings.

    ``transport`` is accepted so tests can inject ``httpx.MockTransport`` and
    exercise the real request/parse/normalise path without any network access —
    see ``tests/unit/test_greenhouse_connector.py``. Production code never passes
    it, so the real path always uses a genuine network transport.
    """

    kind = SOURCE_KIND

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def fetch_jobs(self, external_key: str) -> list[NormalizedJob]:
        """Fetch one board's postings. ``external_key`` is the Greenhouse board token."""
        board_token = external_key.strip()
        if not board_token:
            raise ConnectorParseError("A Greenhouse board token is required.")

        url = f"{_BASE_URL}/{board_token}/jobs"
        payload = await self._get_json(url, params={"content": "true"})
        return self._normalise(payload, board_token=board_token)

    async def _get_json(self, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
                headers=headers,
            ) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ConnectorTimeoutError(f"Timed out contacting {url}.") from exc
        except httpx.TransportError as exc:
            # DNS failure, connection refused, TLS error, and similar: treat as
            # retryable, since these are frequently transient.
            raise ConnectorTimeoutError(f"Could not reach {url}: {exc}.") from exc

        if response.status_code != httpx.codes.OK:
            raise ConnectorHTTPError(
                f"{url} returned HTTP {response.status_code}.",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectorParseError(f"{url} did not return valid JSON.") from exc

        if not isinstance(data, dict):
            raise ConnectorParseError(
                f"{url} returned a JSON {type(data).__name__}, expected an object."
            )
        return data

    def _normalise(self, payload: dict[str, Any], *, board_token: str) -> list[NormalizedJob]:
        raw_jobs = payload.get("jobs")
        if not isinstance(raw_jobs, list):
            raise ConnectorParseError(
                f"Expected a 'jobs' array in the response for board '{board_token}'."
            )

        now = datetime.now().astimezone()
        normalized: list[NormalizedJob] = []
        for index, raw_job in enumerate(raw_jobs):
            if not isinstance(raw_job, dict):
                logger.warning(
                    "greenhouse_job_skipped",
                    board_token=board_token,
                    index=index,
                    reason="not_an_object",
                )
                continue
            try:
                job = self._normalise_one(raw_job, board_token=board_token, fetched_at=now)
            except ConnectorParseError as exc:
                # One malformed posting must not abort the whole board. Skip it,
                # keep going — this is the "partial source failure" the backend
                # service layer expects connectors to already have handled per item.
                logger.warning(
                    "greenhouse_job_skipped",
                    board_token=board_token,
                    index=index,
                    reason=str(exc),
                )
                continue
            normalized.append(job)
        return normalized

    def _normalise_one(
        self, raw: dict[str, Any], *, board_token: str, fetched_at: datetime
    ) -> NormalizedJob:
        source_job_id = raw.get("id")
        if source_job_id is None:
            raise ConnectorParseError("Job is missing 'id'.")
        title = _clean_str(raw.get("title"))
        if not title:
            raise ConnectorParseError(f"Job {source_job_id} is missing 'title'.")

        company_name = _clean_str(raw.get("company_name")) or board_token
        application_url = _clean_str(raw.get("absolute_url"))
        if not application_url:
            raise ConnectorParseError(f"Job {source_job_id} is missing 'absolute_url'.")

        location_raw = _clean_str(_dig(raw, "location", "name"))
        description = html_to_text(
            raw.get("content") if isinstance(raw.get("content"), str) else None
        )

        posted_at, precision = _parse_posted_at(raw.get("first_published"))

        return NormalizedJob(
            source=SOURCE_KIND,
            source_job_id=str(source_job_id),
            company_name=company_name,
            title=title,
            location_raw=location_raw,
            application_url=application_url,
            description=description,
            work_arrangement=classify_work_arrangement(
                location=location_raw, title=title, description=description
            ),
            employment_type=classify_employment_type(title=title, description=description),
            posted_at=posted_at,
            posted_at_precision=precision,
            source_status=SourceStatus.OPEN,
            last_observed_at=fetched_at,
            raw_payload=None,
        )


def _dig(obj: dict[str, Any], *keys: str) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _parse_posted_at(value: Any) -> tuple[datetime | None, PostedAtPrecision]:
    """Greenhouse's ``first_published`` is a real source timestamp, or it's absent.

    Never defaulted to "now" — an absent or unparseable value means the posting
    date is unknown, exactly as ``NormalizedJob`` requires.
    """
    if not isinstance(value, str) or not value.strip():
        return None, PostedAtPrecision.UNKNOWN
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, PostedAtPrecision.UNKNOWN
    if parsed.tzinfo is None:
        return None, PostedAtPrecision.UNKNOWN
    return parsed, PostedAtPrecision.EXACT
