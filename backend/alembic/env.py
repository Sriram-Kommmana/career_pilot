"""Alembic environment.

The database URL comes from ``app.core.config``, never from ``alembic.ini``, so there
is one configuration layer and no connection string in a committed file.

Migrations run through a synchronous psycopg connection even though the application
uses asyncpg. Alembic's migration context is synchronous, and bridging it with
``run_sync`` adds an event loop for no benefit when all we need is DDL.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import resolve_database_url

# Importing the models package registers every table on Base.metadata, which is what
# autogenerate compares against.
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """The resolved URL, with the async driver swapped for the sync one.

    Going through ``resolve_database_url`` means ``alembic upgrade head`` starts or
    attaches to the same embedded server the application uses, so there is no separate
    "set up the database first" step.
    """
    from app.core.devdb import to_sync_url

    return to_sync_url(resolve_database_url())


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Keep autogenerate focused on our own schema.

    pgvector installs objects into the public schema; without this, every autogenerate
    run would propose dropping them.
    """
    is_internal_table = type_ == "table" and name is not None and name.startswith("pg_")
    return not is_internal_table


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it. Useful for review in CI."""
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_sync_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            # Keep every migration's DDL in one transaction, so a failure part-way
            # through leaves the schema as it was.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
