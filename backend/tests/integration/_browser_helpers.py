"""Shared setup for the Phase 5 integration tests.

Not a ``test_*`` module itself — a helper module the browser-session and
fill-verification integration tests both import, so the "get an application
to READY, with a real resume and a real confirmed profile" dance is written
once.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.security import decode_access_token


def user_id_from_auth(auth: dict[str, str]) -> UUID:
    """The real user id behind an ``auth`` fixture's bearer token.

    ``app.browser.session.BrowserSessionManager`` is keyed by user id, not by
    a token, so tests that inject a session directly (bypassing the live
    navigation ``POST /browser-session`` needs) need the real id.
    """
    token = auth["Authorization"].removeprefix("Bearer ").strip()
    settings = get_settings()
    return decode_access_token(
        token, secret_key=settings.secret_key, algorithm=settings.jwt_algorithm
    )


async def advance_application_to_ready(
    client: object, api: str, auth: dict[str, str], job_id: object
) -> dict[str, Any]:
    """Create an application and push it to ``READY`` through the real HTTP API.

    Fills every baseline-required field from a confirmed profile (full name as
    two words, so the Greenhouse adapter's first/last-name split applies) plus
    a real uploaded resume, exactly as a user would before ever opening a
    browser session.
    """
    patch = await client.patch(  # type: ignore[attr-defined]
        f"{api}/profile",
        headers=auth,
        json={
            "full_name": "Jordan Lee",
            "linkedin_url": "https://linkedin.com/in/jordanlee",
            "work_authorisation": {"authorised": "Yes", "requires_sponsorship": "No"},
        },
    )
    assert patch.status_code == 200, patch.text

    upload = await client.post(  # type: ignore[attr-defined]
        f"{api}/resumes",
        headers=auth,
        files={"file": ("resume.txt", b"Jordan Lee. Backend engineer.", "text/plain")},
    )
    assert upload.status_code == 201, upload.text

    created = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications", headers=auth, json={"job_id": str(job_id)}
    )
    assert created.status_code == 201, created.text
    application = created.json()

    prepared = await client.post(  # type: ignore[attr-defined]
        f"{api}/applications/{application['id']}/prepare", headers=auth
    )
    assert prepared.status_code == 200, prepared.text
    body: dict[str, Any] = prepared.json()
    assert body["state"] == "READY", body
    return body
