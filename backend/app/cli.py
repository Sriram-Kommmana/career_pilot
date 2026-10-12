"""Developer task runner: ``python -m app.cli <command>``.

Written with argparse rather than a CLI framework so it adds no dependency, and in
Python rather than a Makefile because this project targets Windows as a first-class
development platform and ``make`` is not present there by default.

Run ``python -m app.cli --help`` for the current list.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _print(message: str) -> None:
    """Write to stdout without tripping the no-print lint rule in app code."""
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def cmd_db_start(_args: argparse.Namespace) -> int:
    from app.core.config import get_settings, resolve_database_url

    settings = get_settings()
    url = resolve_database_url(settings)
    _print(f"Database ready: {url}")
    _print(f"Data directory: {settings.pgdata_dir}")
    return 0


def cmd_db_stop(_args: argparse.Namespace) -> int:
    from app.core.config import get_settings
    from app.core.devdb import is_embedded, stop_embedded

    settings = get_settings()
    if not is_embedded(settings.database_url):
        _print(
            "CAREERPILOT_DATABASE_URL points at an external server, so there is "
            "nothing for CareerPilot to stop."
        )
        return 0
    stopped = stop_embedded(pgdata=settings.pgdata_dir)
    _print("Embedded PostgreSQL stopped." if stopped else "No embedded server was running.")
    return 0


def cmd_db_status(_args: argparse.Namespace) -> int:
    from app.core.config import get_settings
    from app.core.devdb import embedded_status, is_embedded

    settings = get_settings()
    if not is_embedded(settings.database_url):
        _print(f"External database configured: {settings.database_url}")
        return 0
    status = embedded_status(pgdata=settings.pgdata_dir)
    for key, value in status.items():
        _print(f"{key}: {value}")
    return 0


def cmd_db_upgrade(_args: argparse.Namespace) -> int:
    return _run_alembic(["upgrade", "head"])


def cmd_db_downgrade(args: argparse.Namespace) -> int:
    return _run_alembic(["downgrade", args.revision])


def cmd_db_revision(args: argparse.Namespace) -> int:
    command = ["revision", "-m", args.message]
    if args.autogenerate:
        command.append("--autogenerate")
    return _run_alembic(command)


def cmd_db_reset(args: argparse.Namespace) -> int:
    """Drop every table and re-run migrations.

    Destructive, so it refuses without ``--yes``. Only ever touches the configured
    development or test database.
    """
    from app.core.config import get_settings, resolve_database_url
    from app.core.devdb import to_psycopg_dsn

    settings = get_settings()
    url = resolve_database_url(settings)
    if not args.yes:
        _print("This drops every table in:")
        _print(f"  {url}")
        _print("Re-run with --yes if that is what you want.")
        return 1

    import psycopg

    with psycopg.connect(to_psycopg_dsn(url), autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    _print("Schema dropped and recreated.")
    return _run_alembic(["upgrade", "head"])


def _run_alembic(arguments: list[str]) -> int:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        check=False,
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def cmd_seed(args: argparse.Namespace) -> int:
    from app.seed.runner import seed_database

    summary = asyncio.run(seed_database(reset=args.reset, demo_user=not args.no_user))
    for line in summary:
        _print(line)
    return 0


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.core.config import get_settings, resolve_database_url

    settings = get_settings()
    # Start the database before uvicorn so a connection failure is reported here,
    # clearly, instead of inside the first request.
    resolve_database_url(settings)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=None,  # structlog owns logging; see app.core.logging
    )
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="CareerPilot backend tasks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("db-start", help="Start (or attach to) the local database.").set_defaults(
        func=cmd_db_start
    )
    sub.add_parser("db-stop", help="Stop the embedded local database.").set_defaults(
        func=cmd_db_stop
    )
    sub.add_parser("db-status", help="Show whether the local database is running.").set_defaults(
        func=cmd_db_status
    )
    sub.add_parser("db-upgrade", help="Apply all pending migrations.").set_defaults(
        func=cmd_db_upgrade
    )

    downgrade = sub.add_parser("db-downgrade", help="Roll back migrations.")
    downgrade.add_argument("revision", nargs="?", default="-1")
    downgrade.set_defaults(func=cmd_db_downgrade)

    revision = sub.add_parser("db-revision", help="Create a new migration.")
    revision.add_argument("-m", "--message", required=True)
    revision.add_argument("--autogenerate", action="store_true")
    revision.set_defaults(func=cmd_db_revision)

    reset = sub.add_parser("db-reset", help="Drop all tables and re-migrate (destructive).")
    reset.add_argument("--yes", action="store_true", help="Confirm the drop.")
    reset.set_defaults(func=cmd_db_reset)

    seed = sub.add_parser("seed", help="Load labelled demo data.")
    seed.add_argument("--reset", action="store_true", help="Remove existing demo rows first.")
    seed.add_argument("--no-user", action="store_true", help="Skip the demo user account.")
    seed.set_defaults(func=cmd_seed)

    serve = sub.add_parser("serve", help="Run the API server.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-reload", dest="reload", action="store_false", default=True)
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
