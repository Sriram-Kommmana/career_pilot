"""Normalising job data from heterogeneous sources into one shape.

Every connector produces a :class:`NormalizedJob`. The rules here are deliberately
conservative: when a source does not tell us something, the result says "unknown"
rather than picking a plausible value.

The one that matters most is the posting date. A great many ATS feeds omit it, and it
is tempting to substitute the time we first saw the job. That would silently convert
"we don't know when this was posted" into "posted today", which misleads the user
about how stale a listing is. So :class:`PostedAtPrecision` travels with the date and
``UNKNOWN`` is a first-class answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final


class WorkArrangement(StrEnum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ONSITE = "ONSITE"
    UNKNOWN = "UNKNOWN"


class EmploymentType(StrEnum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    INTERNSHIP = "INTERNSHIP"
    CONTRACT = "CONTRACT"
    TEMPORARY = "TEMPORARY"
    APPRENTICESHIP = "APPRENTICESHIP"
    UNKNOWN = "UNKNOWN"


class PostedAtPrecision(StrEnum):
    """How much the source actually told us about the posting date."""

    #: A full timestamp from the source.
    EXACT = "EXACT"
    #: A calendar date with no time component.
    DATE_ONLY = "DATE_ONLY"
    #: The source gave nothing. Never render this as "posted recently".
    UNKNOWN = "UNKNOWN"


class SourceStatus(StrEnum):
    """The status the source itself reported, not our inference."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    DRAFT = "DRAFT"
    UNKNOWN = "UNKNOWN"


_WHITESPACE_RE: Final = re.compile(r"\s+")
_NON_ALNUM_RE: Final = re.compile(r"[^a-z0-9]+")

# Suffixes stripped when comparing company names, so "Acme, Inc." and "Acme" are
# recognised as the same employer for deduplication purposes.
_COMPANY_SUFFIXES: Final[tuple[str, ...]] = (
    "incorporated",
    "inc",
    "corporation",
    "corp",
    "limited",
    "ltd",
    "llc",
    "llp",
    "plc",
    "gmbh",
    "bv",
    "nv",
    "sa",
    "ag",
    "ab",
    "oy",
    "pty",
    "pvt",
    "private",
    "co",
    "company",
    "holdings",
    "group",
    "technologies",
    "technology",
    "labs",
)

# Decorations that carry no signal about which role a posting is for.
# Job pages use en dashes, em dashes and minus signs interchangeably with hyphens.
# Folding them once, here, keeps every pattern in this package pure ASCII and means
# a title punctuated with an en dash normalises the same as one using a hyphen.
_DASH_TRANSLATION: Final = str.maketrans({chr(0x2013): "-", chr(0x2014): "-", chr(0x2212): "-"})


def fold_dashes(text: str) -> str:
    """Map en dash, em dash and minus sign onto a plain hyphen."""
    return text.translate(_DASH_TRANSLATION)


_TITLE_NOISE_RE: Final = re.compile(
    r"""
    \( [^)]* \)             # (Remote), (f/m/d), (2026 Start)
    | \[ [^\]]* \]          # [Hybrid]
    | \b (?: f/m/d | m/f/d | m/w/d | d/f/m | h/f ) \b
    | \b (?: urgent(?:ly)?\ hiring | hiring\ now | immediate\ joiner )\b
    | -\s*$                 # trailing dash (any dash variant is folded first)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_REMOTE_HINTS: Final[tuple[str, ...]] = (
    "remote",
    "work from home",
    "work-from-home",
    "wfh",
    "distributed",
    "anywhere",
    "virtual",
)
_HYBRID_HINTS: Final[tuple[str, ...]] = ("hybrid", "flexible location", "partially remote")
_ONSITE_HINTS: Final[tuple[str, ...]] = ("on-site", "onsite", "in office", "in-office")

_EMPLOYMENT_HINTS: Final[tuple[tuple[EmploymentType, tuple[str, ...]], ...]] = (
    (
        EmploymentType.INTERNSHIP,
        ("intern", "internship", "industrial trainee", "summer analyst", "co-op", "coop"),
    ),
    (EmploymentType.APPRENTICESHIP, ("apprentice", "apprenticeship", "graduate scheme")),
    (EmploymentType.CONTRACT, ("contract", "contractor", "freelance", "b2b", "consultant")),
    (EmploymentType.TEMPORARY, ("temporary", "temp", "seasonal", "fixed term", "fixed-term")),
    (EmploymentType.PART_TIME, ("part time", "part-time", "parttime")),
    (EmploymentType.FULL_TIME, ("full time", "full-time", "fulltime", "permanent", "regular")),
)


def collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_text(text: str | None) -> str:
    """Lowercase, strip accents, collapse whitespace. For comparison, not display."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return collapse_whitespace(fold_dashes(stripped).lower())


def slugify(text: str | None) -> str:
    """A stable comparison key: lowercase alphanumerics joined by single hyphens."""
    return _NON_ALNUM_RE.sub("-", normalize_text(text)).strip("-")


def normalize_company_name(name: str | None) -> str:
    """A comparison key for an employer, with legal-form suffixes removed.

    ``"Acme Technologies, Inc."`` and ``"ACME"`` both reduce to ``"acme"``. Suffixes
    are stripped repeatedly from the end, but never to nothing — a company genuinely
    called "Group" keeps its name.
    """
    tokens = [token for token in slugify(name).split("-") if token]
    while len(tokens) > 1 and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return "-".join(tokens)


def normalize_job_title(title: str | None) -> str:
    """A comparison key for a role, with decoration removed.

    Seniority words are *kept*: "Senior Engineer" and "Engineer" are different jobs
    and must not collapse into one another during deduplication.
    """
    if not title:
        return ""
    cleaned = _TITLE_NOISE_RE.sub(" ", fold_dashes(title))
    return slugify(cleaned)


def normalize_location(location: str | None) -> str:
    """A comparison key for a location.

    Remote phrasing is folded to the single token ``remote`` so that
    ``"Remote - India"``, ``"Remote (India)"`` and ``"India, Remote"`` compare equal.
    """
    text = normalize_text(location)
    if not text:
        return ""
    if any(hint in text for hint in _REMOTE_HINTS):
        remainder = text
        for hint in _REMOTE_HINTS:
            remainder = remainder.replace(hint, " ")
        remainder_slug = slugify(remainder)
        return f"remote-{remainder_slug}".rstrip("-") if remainder_slug else "remote"
    return slugify(text)


def classify_work_arrangement(
    *,
    location: str | None = None,
    title: str | None = None,
    description: str | None = None,
    source_value: str | None = None,
) -> WorkArrangement:
    """Infer remote/hybrid/onsite, or return ``UNKNOWN``.

    A value the source stated explicitly always wins. Otherwise the location and
    title are inspected, because they are short and specific. The description is
    consulted last and only for a hybrid or remote hint, since long prose mentions
    "remote" for all sorts of incidental reasons.
    """
    if source_value:
        candidate = normalize_text(source_value)
        for arrangement in (
            WorkArrangement.HYBRID,
            WorkArrangement.REMOTE,
            WorkArrangement.ONSITE,
        ):
            if arrangement.value.lower() in candidate:
                return arrangement

    short_text = " ".join(filter(None, (normalize_text(location), normalize_text(title))))
    if any(hint in short_text for hint in _HYBRID_HINTS):
        return WorkArrangement.HYBRID
    if any(hint in short_text for hint in _REMOTE_HINTS):
        return WorkArrangement.REMOTE
    if any(hint in short_text for hint in _ONSITE_HINTS):
        return WorkArrangement.ONSITE

    body = normalize_text(description)
    if body:
        if any(hint in body for hint in _HYBRID_HINTS):
            return WorkArrangement.HYBRID
        if "fully remote" in body or "100% remote" in body:
            return WorkArrangement.REMOTE

    return WorkArrangement.UNKNOWN


def classify_employment_type(
    *,
    title: str | None = None,
    source_value: str | None = None,
    description: str | None = None,
) -> EmploymentType:
    """Infer the employment type, or return ``UNKNOWN``.

    Checked most-specific-first, so "Software Engineering Intern, Full-Time" is an
    internship rather than a full-time role.
    """
    if source_value:
        candidate = normalize_text(source_value).replace("_", " ")
        for employment_type, hints in _EMPLOYMENT_HINTS:
            if any(hint in candidate for hint in hints):
                return employment_type

    title_text = normalize_text(title)
    for employment_type, hints in _EMPLOYMENT_HINTS:
        if any(hint in title_text for hint in hints):
            return employment_type

    body = normalize_text(description)
    if body:
        for employment_type, hints in _EMPLOYMENT_HINTS:
            if any(hint in body for hint in hints):
                return employment_type

    return EmploymentType.UNKNOWN


@dataclass(frozen=True, slots=True)
class SalaryRange:
    """A compensation range exactly as the source gave it.

    Nothing here is converted, annualised, or estimated. A range with only a maximum
    is stored with ``minimum=None``, not with the maximum copied across.
    """

    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None  # "year" | "month" | "hour" — the source's own wording

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Salary minimum cannot exceed the maximum.")

    @property
    def is_empty(self) -> bool:
        return self.minimum is None and self.maximum is None


@dataclass(frozen=True, slots=True)
class NormalizedJob:
    """The shared job shape every connector produces.

    ``source_job_id`` is the source's own stable identifier. Together with the source
    this is the ingestion idempotency key, so re-running a connector updates rows
    instead of duplicating them.
    """

    source: str
    source_job_id: str
    company_name: str
    title: str
    application_url: str

    location_raw: str | None = None
    description: str | None = None
    requirements: tuple[str, ...] = field(default_factory=tuple)
    responsibilities: tuple[str, ...] = field(default_factory=tuple)

    work_arrangement: WorkArrangement = WorkArrangement.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    salary: SalaryRange | None = None

    posted_at: datetime | None = None
    posted_at_precision: PostedAtPrecision = PostedAtPrecision.UNKNOWN
    source_status: SourceStatus = SourceStatus.UNKNOWN
    last_observed_at: datetime | None = None

    raw_payload: str | None = None

    def __post_init__(self) -> None:
        if not self.source_job_id.strip():
            raise ValueError("source_job_id is required and must not be blank.")
        if not self.company_name.strip():
            raise ValueError("company_name is required and must not be blank.")
        if not self.title.strip():
            raise ValueError("title is required and must not be blank.")
        if not self.application_url.strip():
            raise ValueError("application_url is required and must not be blank.")
        # The invariant that keeps us honest about dates, in both directions.
        if self.posted_at is None and self.posted_at_precision is not PostedAtPrecision.UNKNOWN:
            raise ValueError(
                "posted_at_precision must be UNKNOWN when there is no posted_at. "
                "A missing date is never treated as a recent one."
            )
        if self.posted_at is not None and self.posted_at_precision is PostedAtPrecision.UNKNOWN:
            raise ValueError(
                "posted_at was given without a precision. State whether the source "
                "provided a full timestamp (EXACT) or only a date (DATE_ONLY)."
            )
        if self.posted_at is not None and self.posted_at.tzinfo is None:
            raise ValueError("posted_at must be timezone-aware.")

    @property
    def company_key(self) -> str:
        return normalize_company_name(self.company_name)

    @property
    def title_key(self) -> str:
        return normalize_job_title(self.title)

    @property
    def location_key(self) -> str:
        return normalize_location(self.location_raw)

    @property
    def has_known_posting_date(self) -> bool:
        return self.posted_at_precision is not PostedAtPrecision.UNKNOWN
