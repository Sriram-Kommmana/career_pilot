"""The embedded local PostgreSQL server.

CareerPilot's local development database needs no Docker, no system service, and no
administrator rights. ``pgserver`` ships a real PostgreSQL 16 build (with pgvector) as
a Python wheel; this module starts it against a data directory inside the repository
and hands back an ordinary connection URL.

How it is selected: ``CAREERPILOT_DATABASE_URL`` is ``embedded`` by default. Set it to
a real ``postgresql+asyncpg://`` URL and nothing here runs — that is the production
path, and a managed Postgres is used exactly as it would have been.

Why it works across commands: the server is started with ``cleanup_mode=None``, so it
outlives the process that launched it. ``alembic upgrade head``, ``uvicorn``, the seed
script, and the test suite each attach to the same already-running instance rather
than starting their own, because ``pgserver`` records the postmaster details in the
data directory and takes a file lock. Stopping it is explicit:
``python -m app.cli db-stop``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Values of CAREERPILOT_DATABASE_URL that mean "manage a local server for me".
EMBEDDED_SENTINELS: Final[frozenset[str]] = frozenset({"", "embedded", "embedded://"})

#: Postgres identifier rules, applied before a database name reaches a CREATE statement.
_SAFE_IDENTIFIER: Final = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Resolved URLs are cached per (pgdata, database) so repeated calls in one process do
# not re-run the readiness checks.
_resolved: dict[tuple[str, str], str] = {}


def _pgserver() -> Any:
    """Import ``pgserver``, with an actionable message if it is missing.

    Typed as ``Any`` deliberately: the package ships ``py.typed`` but does not
    re-export ``get_server`` through ``__all__``, so attribute access on the typed
    module fails type checking. Funnelling every use through here keeps that quirk in
    one place instead of scattering ignore comments through the module.
    """
    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover - declared in the dev extra
        raise RuntimeError(
            "CAREERPILOT_DATABASE_URL is 'embedded' but the pgserver package is not "
            "installed. Either install it with\n"
            '    pip install -e ".[dev]"\n'
            "or point CAREERPILOT_DATABASE_URL at a PostgreSQL server you run yourself."
        ) from exc
    return pgserver


def is_embedded(database_url: str | None) -> bool:
    """Whether this URL asks for the managed local server."""
    return (database_url or "").strip().lower() in EMBEDDED_SENTINELS


def _validate_database_name(name: str) -> str:
    """Reject anything that cannot be safely interpolated into DDL.

    Postgres has no parameter binding for identifiers, so a database name reaches
    ``CREATE DATABASE`` as text. Constraining it to a conservative identifier pattern
    removes the injection question entirely rather than relying on quoting.
    """
    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(
            f"Invalid database name {name!r}. Use lowercase letters, digits and "
            "underscores, starting with a letter or underscore."
        )
    return name


def _ensure_database_and_extensions(admin_url: str, database: str) -> None:
    """Create the database and enable pgvector, both idempotently.

    Runs over the synchronous psycopg driver because ``CREATE DATABASE`` cannot run
    inside a transaction block, and psycopg's ``autocommit`` makes that straightforward.
    """
    import psycopg

    safe_name = _validate_database_name(database)

    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (safe_name,)
        ).fetchone()
        if exists is None:
            conn.execute(f'CREATE DATABASE "{safe_name}"')
            logger.info("embedded_database_created", database=safe_name)

    target_url = admin_url.rsplit("/", 1)[0] + f"/{safe_name}"
    with psycopg.connect(target_url, autocommit=True) as conn:
        # pgvector is enabled here as well as in the Alembic baseline, so a freshly
        # created database is usable even before migrations run.
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


def start_embedded(*, pgdata: Path, database: str) -> str:
    """Start or attach the embedded server and return an asyncpg URL for ``database``.

    Idempotent and safe to call from several processes: ``pgserver`` serialises
    initialisation with a file lock and reuses a running postmaster.
    """
    key = (str(pgdata.resolve()), database)
    cached = _resolved.get(key)
    if cached is not None:
        return cached

    pgdata.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None keeps the server running after this process exits, so the next
    # command (migrations, seed, tests) attaches instead of cold-starting.
    server = _pgserver().get_server(str(pgdata), cleanup_mode=None)
    admin_url: str = server.get_uri()

    _ensure_database_and_extensions(admin_url, database)

    parts = urlsplit(admin_url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 5432
    user = parts.username or "postgres"
    url = f"postgresql+asyncpg://{user}@{host}:{port}/{database}"

    logger.info("embedded_postgres_ready", host=host, port=port, database=database)
    _resolved[key] = url
    return url


def stop_embedded(*, pgdata: Path) -> bool:
    """Shut the embedded server down. Returns whether one was running."""
    if not pgdata.exists():
        return False
    server = _pgserver().get_server(str(pgdata), cleanup_mode=None)
    if server.get_pid() is None:
        return False
    server.cleanup_mode = "stop"
    server.cleanup()
    _resolved.clear()
    logger.info("embedded_postgres_stopped", pgdata=str(pgdata))
    return True


def embedded_status(*, pgdata: Path) -> dict[str, object]:
    """Whether a local server is running, and where. For the CLI and health endpoint."""
    if not (pgdata / "postmaster.pid").exists():
        return {"running": False, "pgdata": str(pgdata)}
    server = _pgserver().get_server(str(pgdata), cleanup_mode=None)
    pid = server.get_pid()
    return {
        "running": pid is not None,
        "pid": pid,
        "pgdata": str(pgdata),
        # Shown without a password because the local server uses trust authentication
        # on loopback only; there is no secret here to leak.
        "uri": server.get_uri() if pid is not None else None,
    }


def to_sync_url(async_url: str) -> str:
    """The psycopg equivalent of an asyncpg URL, for Alembic and admin statements."""
    return async_url.replace("+asyncpg", "+psycopg")


def to_psycopg_dsn(async_url: str) -> str:
    """A bare libpq DSN, for psycopg calls that take a connection string."""
    return async_url.replace("postgresql+asyncpg://", "postgresql://")
