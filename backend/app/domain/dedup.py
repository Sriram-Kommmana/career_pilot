"""Deduplicating jobs within and across sources.

Two layers, because they answer different questions.

1. **Ingestion identity** — ``(source, source_job_id)``. The same posting seen twice
   from the same source is one row, updated in place. This is a database uniqueness
   constraint, not a heuristic.

2. **Cross-source identity** — :func:`job_fingerprint`. The same role listed on both a
   company career page and its Greenhouse board should be shown to the user once.
   This *is* a heuristic, so duplicates are **linked, never deleted**: each source's
   canonical ``application_url`` is preserved, and the user can still apply through
   whichever one they prefer. Deleting a row because it looked like a duplicate would
   be unrecoverable and occasionally wrong.

Seniority is intentionally part of the fingerprint. "Senior Engineer" and "Engineer"
at the same company are different jobs, and collapsing them would hide a real opening.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from app.domain.normalize import (
    normalize_company_name,
    normalize_job_title,
    normalize_location,
)

#: Bumping this invalidates stored fingerprints, so a migration must recompute them.
FINGERPRINT_VERSION: Final = "v1"


class _FingerprintableJob(Protocol):
    """The minimum needed to fingerprint a job. Satisfied by NormalizedJob and the ORM model."""

    @property
    def company_name(self) -> str: ...
    @property
    def title(self) -> str: ...


def job_fingerprint(
    *,
    company_name: str,
    title: str,
    location: str | None = None,
) -> str:
    """A deterministic cross-source identity for a posting.

    Built from the normalised company, title, and location. Location is included
    because the same title at the same company in two cities is two jobs; it is
    normalised first so ``"Remote - US"`` and ``"US (Remote)"`` agree.
    """
    parts = (
        FINGERPRINT_VERSION,
        normalize_company_name(company_name),
        normalize_job_title(title),
        normalize_location(location),
    )
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def fingerprint_for(job: _FingerprintableJob, *, location: str | None = None) -> str:
    return job_fingerprint(company_name=job.company_name, title=job.title, location=location)


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """One logical job and the source records that describe it.

    ``canonical_index`` points at the record whose ``application_url`` should be
    offered by default. Every record is kept.
    """

    fingerprint: str
    indices: tuple[int, ...]
    canonical_index: int

    @property
    def is_duplicate(self) -> bool:
        return len(self.indices) > 1


#: Source preference when choosing which duplicate to surface first. Applying through
#: the ATS the employer actually uses tends to work better than a career-page wrapper,
#: and a direct board beats an aggregator.
_SOURCE_PRIORITY: Final[dict[str, int]] = {
    "greenhouse": 0,
    "lever": 0,
    "ashby": 0,
    "workday": 1,
    "career_page": 2,
    "manual": 3,
}
_DEFAULT_SOURCE_PRIORITY: Final = 5


def source_priority(source: str) -> int:
    return _SOURCE_PRIORITY.get(source.lower(), _DEFAULT_SOURCE_PRIORITY)


def source_priorities() -> dict[str, int]:
    """The priority table, for a query that needs to mirror it in SQL.

    Returned as a copy so the feed query and :func:`group_duplicates` cannot drift
    apart: both read the same ordering from here.
    """
    return dict(_SOURCE_PRIORITY)


def default_source_priority() -> int:
    return _DEFAULT_SOURCE_PRIORITY


def group_duplicates(
    jobs: Sequence[_FingerprintableJob],
    *,
    locations: Sequence[str | None] | None = None,
    sources: Sequence[str] | None = None,
) -> list[DuplicateGroup]:
    """Group a batch of jobs by fingerprint, preserving input order.

    ``locations`` and ``sources`` are parallel to ``jobs``. They are accepted
    separately so this works for both :class:`NormalizedJob` instances and ORM rows,
    without the domain layer needing to know either type.

    The canonical record is the one from the most preferred source; ties go to the
    earliest in the input, which keeps the result stable.
    """
    if locations is not None and len(locations) != len(jobs):
        raise ValueError("locations must be the same length as jobs")
    if sources is not None and len(sources) != len(jobs):
        raise ValueError("sources must be the same length as jobs")

    buckets: dict[str, list[int]] = {}
    for index, job in enumerate(jobs):
        location = locations[index] if locations is not None else None
        fingerprint = job_fingerprint(
            company_name=job.company_name, title=job.title, location=location
        )
        buckets.setdefault(fingerprint, []).append(index)

    groups: list[DuplicateGroup] = []
    for fingerprint, indices in buckets.items():
        if sources is None:
            canonical = indices[0]
        else:
            canonical = min(indices, key=lambda i: (source_priority(sources[i]), i))
        groups.append(
            DuplicateGroup(
                fingerprint=fingerprint,
                indices=tuple(indices),
                canonical_index=canonical,
            )
        )
    return groups


def dedupe_keep_canonical(
    jobs: Sequence[_FingerprintableJob],
    *,
    locations: Sequence[str | None] | None = None,
    sources: Sequence[str] | None = None,
) -> list[int]:
    """Indices of the canonical record of each group, in first-seen order.

    This is the list the job feed renders. Nothing is discarded from storage; the
    non-canonical records remain available on the job detail view as alternative
    places to apply.
    """
    groups = group_duplicates(jobs, locations=locations, sources=sources)
    return sorted(group.canonical_index for group in groups)


def materially_changed(
    *,
    previous: dict[str, object],
    current: dict[str, object],
    watched_fields: Iterable[str] = (
        "title",
        "location_raw",
        "employment_type",
        "work_arrangement",
        "application_url",
        "source_status",
        "salary_min",
        "salary_max",
    ),
) -> list[str]:
    """Which watched fields differ between two snapshots of the same posting.

    Used for two decisions: whether to re-notify the user about a job they already
    saw, and whether a prepared application needs re-checking. Description text is
    deliberately *not* watched — ATS feeds reflow prose constantly, and treating that
    as a change would bury the user in noise.
    """
    changed: list[str] = []
    for key in watched_fields:
        if previous.get(key) != current.get(key):
            changed.append(key)
    return changed
