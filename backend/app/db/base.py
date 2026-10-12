"""The declarative base and the mixins every table uses.

Importing this module must not import the model modules, or Alembic's ``env.py`` ends
up with a circular import. ``app.models`` imports *this*; the relationship is one-way.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import NAMING_CONVENTION, JsonB


class Base(DeclarativeBase):
    """Declarative base for every CareerPilot table."""

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

    # Applied to every ``Mapped[...]`` annotation, so individual models rarely need to
    # name a SQL type at all. SQLAlchemy matches these keys exactly, so each generic
    # shape a model actually uses has to be listed; a bare ``dict`` or ``list`` would
    # not match ``dict[str, object]`` or ``list[str]``.
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this as a plain dict
        datetime: sa.DateTime(timezone=True),
        uuid.UUID: PgUUID(as_uuid=True),
        list[str]: JsonB,
        list[dict[str, object]]: JsonB,
        dict[str, object]: JsonB,
        dict[str, float]: JsonB,
    }

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class UUIDPrimaryKeyMixin:
    """A UUIDv4 primary key.

    Chosen over a sequence so an id reveals nothing: a job-search account's row count
    and signup order are not things an API should leak, and ids appear in URLs.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at``, both maintained by the database.

    ``server_default`` and ``onupdate`` are used rather than Python defaults so a row
    written by a migration, a seed script, or psql is timestamped the same way as one
    written by the application.
    """

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )
