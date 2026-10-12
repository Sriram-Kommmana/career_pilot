"""Test fixtures.

The suite is split so that the safety-critical logic can always be tested:

* ``tests/unit`` exercises ``app.domain``, which is pure. No database, no network, no
  running services. These tests run anywhere, including in a clean checkout.
* ``tests/integration`` exercises the API against a real PostgreSQL. If no database is
  reachable, those tests **skip with an actionable message** rather than failing, so a
  missing local server never looks like broken code.

The integration database is a dedicated one (``careerpilot_test`` by default) whose
schema is created and dropped per session, so a test run cannot touch development data.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest

# ---------------------------------------------------------------------------
# Database availability
# ---------------------------------------------------------------------------

_SKIP_REASON = (
    "No PostgreSQL reachable for integration tests.\n"
    "CareerPilot can start one for you with no Docker and no system service:\n"
    "    python -m app.cli db-start\n"
    "Or set CAREERPILOT_TEST_DATABASE_URL to a server you run yourself."
)


def _probe_database() -> tuple[str | None, str | None]:
    """Return ``(url, error)`` for the test database.

    Resolving the URL may *start* the embedded server, which is the intended
    behaviour: a developer running the suite should not have to start it by hand.
    """
    try:
        from app.core.config import resolve_database_url
        from app.core.devdb import to_psycopg_dsn

        url = resolve_database_url(for_tests=True)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    try:
        import psycopg

        with psycopg.connect(to_psycopg_dsn(url), connect_timeout=5) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return url, None


@pytest.fixture(scope="session")
def database_url() -> str:
    url, error = _probe_database()
    if url is None:
        pytest.skip(f"{_SKIP_REASON}\nLast error: {error}", allow_module_level=True)
    return url


# ---------------------------------------------------------------------------
# Schema and session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _schema(database_url: str) -> Iterator[str]:
    """Create the schema once per session, and drop it afterwards.

    ``Base.metadata.create_all`` is used rather than running Alembic, so a schema
    mistake in a migration cannot make the whole suite unrunnable. A dedicated
    migration test (``tests/integration/test_migrations.py``) checks that the
    migrations and the models agree.
    """
    from sqlalchemy import create_engine, text

    from app.core.devdb import to_sync_url
    from app.models import Base

    engine = create_engine(to_sync_url(database_url), future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    engine.dispose()

    yield database_url

    engine = create_engine(to_sync_url(database_url), future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
async def db_engine(_schema: str) -> AsyncIterator[object]:
    """The engine for one test, truncated clean on the way out.

    Truncation lives in *this* fixture's teardown, not in a separate autouse
    fixture, because a separate fixture with no explicit dependency on ``db_engine``
    has no guaranteed ordering against it: pytest tore ``db_engine`` down (disposing
    the pool) before such a fixture's own teardown ran, and the truncate then failed
    with "fixture value ... is not available". Doing the cleanup here, before
    disposal, removes the ordering question entirely. Unit tests never request this
    fixture, so they stay infrastructure-free.
    """
    from app.db import session as session_module

    engine = session_module.create_engine_from_settings(
        __import__("app.core.config", fromlist=["get_settings"]).get_settings(),
        url=_schema,
    )
    session_module.configure(engine)
    try:
        yield engine
    finally:
        from sqlalchemy import text

        from app.models import Base

        table_names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
        await engine.dispose()


@pytest.fixture
async def session(db_engine: object) -> AsyncIterator[object]:
    """A session for tests that work below the HTTP layer."""
    from app.db.session import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as active:
        yield active
        await active.commit()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db_engine: object) -> AsyncIterator[object]:
    """An HTTP client bound to the app in-process, with no network listener."""
    import httpx

    from app.core.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def api() -> str:
    from app.core.config import get_settings

    return get_settings().api_prefix


async def register_user(
    client: object,
    *,
    email: str | None = None,
    password: str = "test-password-123",  # noqa: S107 - a fixture default, not a real credential
) -> tuple[str, str]:
    """Register a user and return ``(email, bearer_token)``."""
    from app.core.config import get_settings

    address = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(  # type: ignore[attr-defined]
        f"{get_settings().api_prefix}/auth/register",
        json={"email": address, "password": password},
    )
    assert response.status_code == 201, response.text
    return address, response.json()["access_token"]


@pytest.fixture
async def auth(client: object) -> dict[str, str]:
    """Headers for a freshly registered user."""
    _, token = await register_user(client)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def second_auth(client: object) -> dict[str, str]:
    """Headers for a *different* user, for ownership tests."""
    _, token = await register_user(client)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_now() -> datetime:
    """A fixed instant. Domain functions take ``now`` as a parameter, so no clock
    patching is needed anywhere in the suite."""
    return datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
async def seeded_job(session: object) -> object:
    """One stored job, built through the real ingestion path."""
    from app.domain.normalize import (
        EmploymentType,
        NormalizedJob,
        PostedAtPrecision,
        SourceStatus,
        WorkArrangement,
    )
    from app.services.jobs import upsert_job

    normalized = NormalizedJob(
        source="demo_seed",
        source_job_id="test-job-1",
        company_name="Testcorp Limited",
        title="Backend Engineer",
        location_raw="Bengaluru, India",
        application_url="https://example.invalid/testcorp/apply/backend",
        description="A backend role used by the test suite.",
        requirements=(
            "Experience with Python",
            "Familiarity with PostgreSQL",
            "Kubernetes experience is a nice to have",
        ),
        work_arrangement=WorkArrangement.HYBRID,
        employment_type=EmploymentType.FULL_TIME,
        posted_at=datetime(2026, 6, 1, tzinfo=UTC),
        posted_at_precision=PostedAtPrecision.DATE_ONLY,
        source_status=SourceStatus.OPEN,
    )
    job, _ = await upsert_job(
        session,  # type: ignore[arg-type]
        normalized=normalized,
        source_external_key="test-suite",
        is_demo_data=True,
    )
    # Committed, not just flushed: the HTTP `client` fixture issues requests on a
    # separate session/connection, and under READ COMMITTED it cannot see this job
    # until the transaction that created it actually commits.
    await session.commit()  # type: ignore[attr-defined]
    return job


# ---------------------------------------------------------------------------
# Advancing an application past the point a browser agent would reach
#
# Phase 1 has no browser (that is Phase 5), so there is no HTTP path from
# WAITING_FOR_USER_INPUT/READY to WAITING_FOR_APPROVAL. Tests that need to exercise
# approval and the submission gate stand in for the browser directly at the service
# layer, then hand off to the HTTP client for the part that matters: the gate itself.
# ---------------------------------------------------------------------------


async def advance_to_waiting_for_approval(
    session: object, *, user_id: object, application_id: object
) -> str:
    """Force an application to ``WAITING_FOR_APPROVAL`` with every field verified.

    Resolves every outstanding field with a stand-in value (as the browser agent
    will do from Phase 5), marks the form fully verified, walks the *real* state
    machine via :func:`app.domain.states.shortest_path` rather than writing the state
    column directly, and returns the resulting ``form_state_hash``.

    The caller must commit the session before using a different session (e.g. the
    HTTP ``client`` fixture) to observe the result.
    """
    from app.core.clock import utcnow
    from app.domain.approval import FieldResolution, VerificationStatus, compute_form_state_hash
    from app.domain.provenance import ProvenanceKind
    from app.domain.states import ApplicationState, shortest_path
    from app.services.applications import build_form_snapshot, get_application, transition

    application = await get_application(
        session,  # type: ignore[arg-type]
        user_id=user_id,  # type: ignore[arg-type]
        application_id=application_id,  # type: ignore[arg-type]
    )

    for field in application.fields:
        if field.needs_human:
            field.value_masked = f"test-value:{field.field_key}"
            field.value_sha256 = "0" * 64
            field.value_provenance = ProvenanceKind.USER_DIRECT_INPUT
            field.resolution = FieldResolution.RESOLVED
            field.needs_human = False
        field.verification = VerificationStatus.PASSED

    application.verification_status = VerificationStatus.PASSED
    application.verified_at = utcnow()
    application.unresolved_interventions = []

    path = shortest_path(application.state, ApplicationState.WAITING_FOR_APPROVAL)
    assert path is not None, f"no legal path from {application.state} to WAITING_FOR_APPROVAL"
    for step in path:
        await transition(
            session,  # type: ignore[arg-type]
            application=application,
            to_state=step,
            message="test harness: standing in for the Phase 5 browser agent",
        )

    await session.refresh(application, ["fields", "answers"])  # type: ignore[attr-defined]
    application.form_state_hash = compute_form_state_hash(
        build_form_snapshot(application, resume=None)
    )
    await session.flush()  # type: ignore[attr-defined]
    return application.form_state_hash
