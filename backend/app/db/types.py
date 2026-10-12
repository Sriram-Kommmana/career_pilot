"""Shared column types and conventions.

Two decisions worth recording.

**Enums are stored as constrained strings, not Postgres native enums.** A native enum
requires an ``ALTER TYPE`` to add a value, which in practice means a migration every
time a new intervention reason or blocker code appears, and ``ALTER TYPE`` cannot run
inside a transaction on older servers. ``native_enum=False`` gives a ``VARCHAR`` plus a
``CHECK`` constraint, so the database still rejects a bad value but the migration is an
ordinary constraint change.

**Timestamps are always ``timestamptz``.** A naive timestamp in a job-search product
spanning time zones is a bug waiting to happen; the domain layer refuses naive
datetimes for the same reason.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

#: Longest enum value we store, with room to spare.
ENUM_LENGTH = 48


def enum_column[EnumT: StrEnum](enum_cls: type[EnumT], *, name: str) -> sa.Enum:
    """A ``VARCHAR`` + ``CHECK`` column for a :class:`StrEnum`.

    ``values_callable`` makes the stored text the enum's ``value`` rather than its
    member name. They are identical for a ``StrEnum``, but being explicit means a
    later rename of a member does not silently rewrite stored data.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        length=ENUM_LENGTH,
        values_callable=lambda enum: [member.value for member in enum],
        name=name,
    )


def utc_timestamp() -> sa.DateTime:
    return sa.DateTime(timezone=True)


#: Alias so model modules read clearly and the dialect import stays in one place.
JsonB: Any = JSONB

#: Naming convention so Alembic autogenerate produces stable, predictable constraint
#: names instead of server-assigned ones. Without this, a downgrade cannot reliably
#: find the constraint it needs to drop.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
