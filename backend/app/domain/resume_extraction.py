"""Turning plain resume text into a proposal of structured candidate facts.

This is a rule-based reader, not a generator: every :class:`ExtractedItem` it returns
carries a ``source_excerpt`` that is a verbatim substring of the resume text it was
pulled from (enforced by a unit test over this module, not just convention) — there is
no step here that could introduce a word the resume does not contain.

What this module deliberately does *not* do:

* It never produces a date it cannot read in full (month and year). A block that only
  states a year, or no date at all, is still returned — with low confidence and the
  raw date text preserved for a human to read — rather than guessing a month.
* It has no vocabulary slot for anything in ``app.domain.provenance.SensitiveCategory``
  (work authorisation, date of birth, demographic/disability/veteran status,
  government id, salary history, and so on). Even if a resume states one of these
  explicitly, there is no ``field`` value or ``data`` key this module could put it in —
  this is a structural guarantee, not a filter applied after the fact. See
  ``docs/SAFETY.md`` section 4.
* It never calls a model. The result is identical on every run for the same input,
  which is what makes "never invent" a property that can be tested rather than a
  promise about a prompt.

A heading-based section split, plus a handful of regular expressions, is a weaker
reader than a language model on a messy resume — that trade is deliberate: it costs
nothing, needs no API key, and has no prompt-injection surface at all, because
nothing here ever treats resume text as instructions. See ``docs/PLAN.md`` section 8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Literal

from app.domain.matching import EducationLevel
from app.domain.normalize import fold_dashes
from app.domain.skills import KNOWN_TECHNOLOGIES, extract_degree_requirement, extract_skill_mentions

Confidence = Literal["high", "medium", "low"]

#: What ``ExtractedItem.field`` may be. Nothing here maps onto a
#: ``app.domain.provenance.SensitiveCategory`` — that is the whole point.
ItemField = Literal["skill", "education", "project", "experience", "certification", "achievement"]

# ---------------------------------------------------------------------------
# Section headings
# ---------------------------------------------------------------------------

#: Canonical section name -> patterns a heading line might match. Matched against a
#: whole, trimmed line (optionally followed by a colon), case-insensitively, so a
#: heading has to be short and on its own line — this avoids treating an ordinary
#: sentence that happens to contain "experience" as a new section.
_SECTION_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "experience",
        re.compile(
            r"^(work\s+)?(experience|employment(\s+history)?|professional\s+experience)\s*:?$",
            re.IGNORECASE,
        ),
    ),
    ("education", re.compile(r"^education(al\s+background)?\s*:?$", re.IGNORECASE)),
    (
        "skills",
        re.compile(
            r"^(technical\s+|core\s+)?skills(\s*(&|and)\s*(tools|technologies))?\s*:?$",
            re.IGNORECASE,
        ),
    ),
    ("projects", re.compile(r"^(personal\s+|academic\s+)?projects\s*:?$", re.IGNORECASE)),
    (
        "certifications",
        re.compile(r"^(certifications?|certificates|licenses)\s*:?$", re.IGNORECASE),
    ),
    (
        "achievements",
        re.compile(
            r"^(achievements?|awards|honou?rs|summary|profile|highlights)\s*:?$", re.IGNORECASE
        ),
    ),
)

#: A heading line is short — resumes do not write prose on a section-title line.
_MAX_HEADING_LENGTH: Final = 48

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS: Final[dict[str, int]] = {
    name.lower(): index
    for index, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}
_MONTH_ALIASES: Final[dict[str, int]] = {name[:3]: value for name, value in _MONTHS.items()}

_CURRENT_WORDS: Final = re.compile(r"\b(present|current|ongoing|now)\b", re.IGNORECASE)

# "Jan 2020", "January 2020" — deliberately requires a month AND a year together.
# A bare year alone is never enough to populate a date; see DateSpan's docstring.
_MONTH_YEAR_RE: Final = re.compile(r"\b(?P<month>[A-Za-z]{3,9})\.?\s+(?P<year>(19|20)\d{2})\b")

#: A date-range-shaped fragment, used to decide whether a block of text is the start
#: of a new experience/education entry at all. The end side optionally allows a month
#: name before its year ("Jun 2018 - Dec 2020"), not just a bare year or "Present".
#: Matched against text already passed through ``fold_dashes``, so only a plain
#: hyphen needs to be checked here, not every dash variant a resume might use.
_DATE_RANGE_RE: Final = re.compile(
    r"((19|20)\d{2})\s*(-|to)\s*([A-Za-z]{3,9}\.?\s+)?((19|20)\d{2}|present|current)",
    re.IGNORECASE,
)

_URL_RE: Final = re.compile(r"https?://\S+")


@dataclass(frozen=True, slots=True)
class _ParsedDate:
    year: int
    month: int | None

    def iso(self) -> str | None:
        """ISO date string, only when a full month+year is known."""
        if self.month is None:
            return None
        return f"{self.year:04d}-{self.month:02d}-01"


def _parse_month_year(text: str) -> _ParsedDate | None:
    match = _MONTH_YEAR_RE.search(text)
    if match is None:
        return None
    raw_month = match.group("month").lower()
    month = _MONTHS.get(raw_month) or _MONTH_ALIASES.get(raw_month[:3])
    if month is None:
        return None
    return _ParsedDate(year=int(match.group("year")), month=month)


@dataclass(frozen=True, slots=True)
class DateSpan:
    """What was read from a date range, honestly.

    ``start_iso``/``end_iso`` are only set when a full month and year were present —
    never defaulted to the 1st of a guessed month. ``raw`` is always kept, so a block
    that only states a year is still reviewable by the user without CareerPilot
    fabricating the missing precision.
    """

    raw: str
    start_iso: str | None = None
    end_iso: str | None = None
    is_current: bool = False


def _extract_date_span(raw_text: str) -> DateSpan | None:
    # En dash, em dash, and minus sign are folded to a plain hyphen up front, so
    # every pattern below only needs to check for "-", not every dash variant a
    # resume might use.
    text = fold_dashes(raw_text)
    range_match = _DATE_RANGE_RE.search(text)
    if range_match is None:
        return None
    raw = range_match.group(0)
    is_current = bool(_CURRENT_WORDS.search(raw))

    # Look for two month+year dates within the matched span's neighbourhood; the
    # regex above only proves a range exists, not that months are stated.
    window_start = max(0, range_match.start() - 20)
    window = text[window_start : range_match.end() + 20]
    maybe_dates = [_parse_month_year(part) for part in re.split(r"-|\bto\b", window)]
    parsed_dates = [p for p in maybe_dates if p is not None]

    start_iso = parsed_dates[0].iso() if len(parsed_dates) >= 1 else None
    end_iso = None if is_current else (parsed_dates[1].iso() if len(parsed_dates) >= 2 else None)
    return DateSpan(raw=raw, start_iso=start_iso, end_iso=end_iso, is_current=is_current)


# ---------------------------------------------------------------------------
# The proposal shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractedItem:
    """One candidate fact, staged, never yet written anywhere authoritative.

    ``source_excerpt`` is always a verbatim substring of the resume text this item
    came from — that is what makes the fact traceable rather than asserted.
    """

    field: ItemField
    data: dict[str, object]
    source_excerpt: str
    locator: str
    confidence: Confidence
    needs_review: bool


@dataclass(frozen=True, slots=True)
class ExtractionProposal:
    """Everything pulled out of one resume, staged for the user to review."""

    items: tuple[ExtractedItem, ...] = field(default_factory=tuple)
    #: Section headings that were detected but produced no usable item — shown to the
    #: user as "we saw this section but could not make sense of it", not silently
    #: dropped.
    unparsed_sections: tuple[str, ...] = field(default_factory=tuple)


def _detect_section(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LENGTH:
        return None
    for name, pattern in _SECTION_PATTERNS:
        if pattern.match(stripped):
            return name
    return None


def _split_into_sections(text: str) -> dict[str, list[tuple[int, str]]]:
    """Group (1-indexed line number, line text) pairs by detected section.

    Blank lines are kept (as empty strings) rather than dropped here — they are the
    most reliable signal a resume gives for "a new entry starts here", and
    :func:`_blocks` uses them for exactly that. Lines before the first recognised
    heading go under ``"achievements"`` — a resume's opening summary is exactly the
    kind of free text that belongs there.
    """
    sections: dict[str, list[tuple[int, str]]] = {}
    current = "achievements"
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        detected = _detect_section(raw_line)
        if detected is not None:
            current = detected
            continue
        sections.setdefault(current, []).append((line_number, raw_line))
    return sections


def _blocks(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Split a section's lines into entries on blank lines.

    This is the one layout signal that holds up across the most resume formats:
    whatever else varies, a blank line between two jobs or two degrees is the norm.
    A resume that packs entries together with no blank line will have them merged
    into one block — a real limitation, covered by a test, rather than a crash or a
    guess at where one entry ends and the next begins.
    """
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for line_number, text in lines:
        if not text.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append((line_number, text))
    if current:
        blocks.append(current)
    return blocks


def _split_by_date_anchors(block: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Split a block that contains more than one date range into one sub-block per
    date range.

    Blank-line splitting (:func:`_blocks`) is the primary signal, but some PDF text
    extractors (``pypdf`` among them) drop a line entirely when it had no visible
    characters, which silently deletes the blank lines between entries — a resume
    with two jobs and no other separator then arrives here as one block containing
    two date ranges. A date range is a reliable second signal specifically for
    experience and education, which always have one; it is not used for projects,
    which often do not.

    Each sub-block starts at the line right before its date range (where the
    company/title or institution/degree header usually sits), except the first,
    which keeps whatever came before it. A block with zero or one date range is
    returned unchanged.
    """
    anchors = [i for i, (_, text) in enumerate(block) if _DATE_RANGE_RE.search(fold_dashes(text))]
    if len(anchors) <= 1:
        return [block]

    splits = [0]
    for anchor in anchors[1:]:
        start = max(splits[-1], anchor - 1)
        if start > splits[-1]:
            splits.append(start)
    splits.append(len(block))
    return [block[splits[i] : splits[i + 1]] for i in range(len(splits) - 1)]


def _locator(section: str, lines: list[tuple[int, str]]) -> str:
    first_line = lines[0][0]
    last_line = lines[-1][0]
    if first_line == last_line:
        return f"resume:section={section},line={first_line}"
    return f"resume:section={section},lines={first_line}-{last_line}"


def _excerpt(lines: list[tuple[int, str]]) -> str:
    return "\n".join(text for _, text in lines).strip()


def _extract_experience_or_education(
    section: str, lines: list[tuple[int, str]]
) -> ExtractedItem | None:
    excerpt = _excerpt(lines)
    span = _extract_date_span(excerpt)
    if span is None:
        return None

    header = fold_dashes(lines[0][1].strip())
    # The header line often also carries the date range; strip it so the
    # company/title (or institution/degree) split below isn't confused by it.
    header_without_dates = _DATE_RANGE_RE.sub("", header).strip(" -|,\t")
    parts = re.split(r"\s{2,}|\t|\s[-|]\s", header_without_dates)
    parts = [p.strip() for p in parts if p.strip()]

    needs_review = span.start_iso is None
    locator = _locator(section, lines)

    if section == "education":
        level = extract_degree_requirement(excerpt)
        # Two common shapes: "Degree — Institution" on one line, or the degree and
        # the institution on their own separate lines (checked first, since a line
        # that itself names a degree is a strong, specific signal either way).
        second_line = None
        if len(lines) > 1:
            candidate = fold_dashes(lines[1][1].strip())
            if not _DATE_RANGE_RE.search(candidate):
                second_line = candidate
        if second_line and extract_degree_requirement(header_without_dates) is not None:
            degree: str | None = header_without_dates
            institution = second_line
        elif len(parts) > 1:
            institution = parts[0]
            degree = parts[1]
        else:
            institution = header_without_dates
            degree = None
        data: dict[str, object] = {
            "institution": institution,
            "degree": degree,
            "level": level or EducationLevel.UNKNOWN.value,
            "start_date": span.start_iso,
            "end_date": span.end_iso,
            "raw_dates": span.raw,
        }
        if level is None:
            needs_review = True
        return ExtractedItem(
            field="education",
            data=data,
            source_excerpt=excerpt,
            locator=locator,
            confidence="low" if needs_review else "medium",
            needs_review=needs_review,
        )

    # Experience: guess which half of the header is the title and which is the
    # company. Genuinely ambiguous without more context, so this is never "high".
    title = parts[0] if parts else header_without_dates
    company = parts[1] if len(parts) > 1 else ""
    body_lines = lines[1:]
    description = "\n".join(text.strip() for _, text in body_lines).strip() or None
    skills_used = sorted(extract_skill_mentions(excerpt, vocabulary=KNOWN_TECHNOLOGIES))

    data = {
        "title": title,
        "company": company,
        "start_date": span.start_iso,
        "end_date": span.end_iso,
        "is_current": span.is_current,
        "description": description,
        "skills_used": skills_used,
        "raw_dates": span.raw,
    }
    return ExtractedItem(
        field="experience",
        data=data,
        source_excerpt=excerpt,
        locator=locator,
        confidence="low",
        needs_review=True,
    )


def _extract_project(lines: list[tuple[int, str]], *, index: int) -> ExtractedItem:
    excerpt = _excerpt(lines)
    header = lines[0][1].strip()
    url_match = _URL_RE.search(excerpt)
    url = url_match.group(0) if url_match else None
    name = _URL_RE.sub("", header).strip(" -|,\t") or f"Project {index + 1}"
    description = "\n".join(text.strip() for _, text in lines[1:]).strip() or None
    skills_used = sorted(extract_skill_mentions(excerpt, vocabulary=KNOWN_TECHNOLOGIES))

    data: dict[str, object] = {
        "name": name,
        "description": description,
        "skills_used": skills_used,
        "url": url if url and "github.com" not in url else None,
        "repository_url": url if url and "github.com" in url else None,
    }
    return ExtractedItem(
        field="project",
        data=data,
        source_excerpt=excerpt,
        locator=_locator("projects", lines),
        confidence="medium",
        needs_review=False,
    )


def _extract_certification(line_number: int, text: str) -> ExtractedItem:
    return ExtractedItem(
        field="certification",
        data={"name": text.strip(), "issuer": None, "issued_on": None},
        source_excerpt=text.strip(),
        locator=f"resume:section=certifications,line={line_number}",
        confidence="medium",
        needs_review=False,
    )


def _extract_skills(full_text: str) -> list[ExtractedItem]:
    """Every known technology mentioned anywhere in the document, not just a
    "Skills" section — a skill is as often demonstrated in an experience bullet as
    it is listed up top."""
    items: list[ExtractedItem] = []
    seen: set[str] = set()
    for line_number, line in enumerate(full_text.splitlines(), start=1):
        if not line.strip():
            continue
        for skill in sorted(extract_skill_mentions(line, vocabulary=KNOWN_TECHNOLOGIES)):
            if skill in seen:
                continue
            seen.add(skill)
            items.append(
                ExtractedItem(
                    field="skill",
                    data={"name": skill},
                    source_excerpt=line.strip(),
                    locator=f"resume:line={line_number}",
                    confidence="high",
                    needs_review=False,
                )
            )
    return items


def _extract_achievements(lines: list[tuple[int, str]]) -> list[ExtractedItem]:
    """Free text that doesn't decompose into a structured record.

    Kept as whole blank-line-separated paragraphs rather than line by line, since a
    single achievement is often wrapped across two or three lines.
    """
    items: list[ExtractedItem] = []
    for block in _blocks(lines):
        text = " ".join(t.strip() for _, t in block).strip()
        if len(text) < 15:  # Too short to be meaningful evidence on its own.
            continue
        items.append(
            ExtractedItem(
                field="achievement",
                data={"text": text},
                source_excerpt=text,
                locator=_locator("achievements", block),
                confidence="medium",
                needs_review=False,
            )
        )
    return items


def extract(resume_text: str) -> ExtractionProposal:
    """Read a resume's plain text into a staged proposal of candidate facts.

    Deterministic: the same text always produces the same proposal, with items in a
    stable order (skills first, then by section). Nothing here writes to a database
    or calls a model.
    """
    if not resume_text or not resume_text.strip():
        return ExtractionProposal()

    sections = _split_into_sections(resume_text)
    items: list[ExtractedItem] = list(_extract_skills(resume_text))
    unparsed: list[str] = []

    for section_name in ("experience", "education"):
        lines = sections.get(section_name)
        if not lines:
            continue
        found_any = False
        for block in _blocks(lines):
            for sub_block in _split_by_date_anchors(block):
                parsed = _extract_experience_or_education(section_name, sub_block)
                if parsed is not None:
                    items.append(parsed)
                    found_any = True
        if not found_any:
            unparsed.append(section_name)

    project_lines = sections.get("projects")
    if project_lines:
        for index, block in enumerate(_blocks(project_lines)):
            items.append(_extract_project(block, index=index))

    certification_lines = sections.get("certifications")
    if certification_lines:
        for line_number, text in certification_lines:
            if text.strip():
                items.append(_extract_certification(line_number, text))

    achievement_lines = sections.get("achievements")
    if achievement_lines:
        items.extend(_extract_achievements(achievement_lines))

    return ExtractionProposal(items=tuple(items), unparsed_sections=tuple(unparsed))
