"""Resume upload, text extraction, and the confirmation step that makes extracted
facts authoritative.

Three operations, three different trust levels:

1. :func:`upload_resume` — validates and stores a file. Nothing here reads the
   document's content as anything but bytes to hash and store.
2. :func:`extract_resume` — reads the file into plain text and runs it through the
   deterministic reader in ``app.domain.resume_extraction``. The result is staged in
   ``Resume.extraction_payload``. Nothing here writes to a profile.
3. :func:`confirm_extraction` — the *only* function in this module that writes to a
   profile's child tables, and only for items the caller explicitly accepted. An
   accepted item never silently replaces an existing confirmed row; see the
   docstring on :func:`confirm_extraction` for the exact rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from datetime import date
from pathlib import Path
from typing import Final

import pypdf
from docx import Document
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.file_validation import SniffedFileType, validate_upload
from app.domain.matching import EducationLevel
from app.domain.provenance import ProvenanceKind
from app.domain.resume_extraction import ExtractedItem, ExtractionProposal, extract
from app.domain.skills import canonical_skill
from app.models import (
    Certification,
    EducationRecord,
    ExperienceRecord,
    Profile,
    ProfileEvidence,
    Project,
    Resume,
    Skill,
)
from app.schemas.resume import (
    ConfirmExtractionResultOut,
    ExtractionDecision,
    ExtractionItemOut,
    ExtractionProposalOut,
)
from app.services.profile import get_profile, refresh_skill_evidence

logger = get_logger(__name__)

#: A resume that cannot be parsed within this window is treated as failed rather
#: than left hanging — a pathological file must not be able to tie up a request.
_EXTRACTION_TIMEOUT_SECONDS: Final = 10.0

#: Enough for anyone genuinely managing several tailored resumes; stops storage from
#: growing unbounded for one account.
_MAX_RESUMES_PER_USER: Final = 10

_EXTENSION_BY_TYPE: Final[dict[SniffedFileType, str]] = {
    SniffedFileType.PDF: ".pdf",
    SniffedFileType.DOCX: ".docx",
    SniffedFileType.TEXT: ".txt",
}
_CONTENT_TYPE_BY_TYPE: Final[dict[SniffedFileType, str]] = {
    SniffedFileType.PDF: "application/pdf",
    SniffedFileType.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    SniffedFileType.TEXT: "text/plain",
}

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def _count_resumes(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Resume).where(Resume.user_id == user_id)
    )
    return result.scalar_one()


async def _clear_other_defaults(
    session: AsyncSession, *, user_id: uuid.UUID, keep: uuid.UUID
) -> None:
    result = await session.execute(
        select(Resume).where(
            Resume.user_id == user_id, Resume.is_default.is_(True), Resume.id != keep
        )
    )
    for other in result.scalars().all():
        other.is_default = False
    await session.flush()


async def upload_resume(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    original_filename: str,
    content: bytes,
    label: str | None,
    is_default: bool,
    settings: Settings,
) -> Resume:
    """Validate, hash, and store one resume file. Never touches a profile.

    The on-disk filename is derived from the generated resume id, never from
    ``original_filename`` — a client-supplied name is display metadata only, so it
    cannot be used to traverse or overwrite another path on disk.
    """
    existing_count = await _count_resumes(session, user_id=user_id)
    if existing_count >= _MAX_RESUMES_PER_USER:
        raise ConflictError(
            f"You already have {_MAX_RESUMES_PER_USER} resumes stored. Delete one "
            "before uploading another.",
            code="RESUME_QUOTA_EXCEEDED",
        )

    validation = validate_upload(filename=original_filename, content=content)
    if not validation.ok:
        raise ValidationError(validation.reason or "This file could not be validated.")

    resume_id = uuid.uuid4()
    extension = _EXTENSION_BY_TYPE[validation.sniffed_type]
    storage_path = settings.resume_dir / f"{resume_id}{extension}"

    def write_file() -> None:
        settings.resume_dir.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)

    await asyncio.to_thread(write_file)

    resume = Resume(
        id=resume_id,
        user_id=user_id,
        label=(label or original_filename).strip() or original_filename,
        original_filename=original_filename,
        content_type=_CONTENT_TYPE_BY_TYPE[validation.sniffed_type],
        size_bytes=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        storage_path=str(storage_path),
        is_default=is_default,
    )
    session.add(resume)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        await asyncio.to_thread(lambda: storage_path.unlink(missing_ok=True))
        raise ConflictError(
            "You already have a resume with that label.", code="DUPLICATE_RESUME_LABEL"
        ) from exc

    if is_default:
        await _clear_other_defaults(session, user_id=user_id, keep=resume.id)

    logger.info(
        "resume_uploaded",
        resume_id=str(resume.id),
        user_id=str(user_id),
        size_bytes=resume.size_bytes,
        content_type=resume.content_type,
    )
    return resume


async def get_owned_resume(
    session: AsyncSession, *, user_id: uuid.UUID, resume_id: uuid.UUID
) -> Resume:
    result = await session.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise NotFoundError("Resume not found.")
    return resume


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _read_file_text(*, storage_path: str, content_type: str) -> str:
    """Blocking. Always called via ``asyncio.to_thread``."""
    raw = Path(storage_path).read_bytes()
    if content_type == "application/pdf":
        reader = pypdf.PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if content_type == _CONTENT_TYPE_BY_TYPE[SniffedFileType.DOCX]:
        document = Document(io.BytesIO(raw))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    return raw.decode("utf-8", errors="replace")


def _serialize_proposal(proposal: ExtractionProposal) -> dict[str, object]:
    return {
        "items": [
            {
                "field": item.field,
                "data": item.data,
                "source_excerpt": item.source_excerpt,
                "locator": item.locator,
                "confidence": item.confidence,
                "needs_review": item.needs_review,
            }
            for item in proposal.items
        ],
        "unparsed_sections": list(proposal.unparsed_sections),
    }


def _deserialize_proposal(payload: dict[str, object] | None) -> ExtractionProposal:
    if not payload:
        return ExtractionProposal()
    raw_items = payload.get("items")
    items = tuple(
        ExtractedItem(
            field=raw["field"],
            data=raw["data"],
            source_excerpt=raw["source_excerpt"],
            locator=raw["locator"],
            confidence=raw["confidence"],
            needs_review=raw["needs_review"],
        )
        for raw in (raw_items if isinstance(raw_items, list) else [])
    )
    unparsed = payload.get("unparsed_sections")
    return ExtractionProposal(
        items=items,
        unparsed_sections=tuple(unparsed) if isinstance(unparsed, list) else (),
    )


async def extract_resume(
    session: AsyncSession, *, user_id: uuid.UUID, resume_id: uuid.UUID
) -> Resume:
    """Read the file into text and stage a proposal. Never writes to a profile.

    Re-running this replaces the staged (unconfirmed) proposal; it never touches
    rows a previous confirmation already wrote.
    """
    resume = await get_owned_resume(session, user_id=user_id, resume_id=resume_id)

    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(
                _read_file_text, storage_path=resume.storage_path, content_type=resume.content_type
            ),
            timeout=_EXTRACTION_TIMEOUT_SECONDS,
        )
        proposal = extract(text)
    except TimeoutError:
        resume.extraction_status = "failed"
        resume.extraction_error = "Extraction timed out. The file may be malformed."
        await session.flush()
        logger.warning("resume_extraction_timed_out", resume_id=str(resume.id))
        return resume
    except Exception as exc:
        # Any parser failure (corrupt PDF, unreadable DOCX, ...) is a clean,
        # reported failure, never a 500 — the resume row is left otherwise intact.
        resume.extraction_status = "failed"
        resume.extraction_error = (
            "This file could not be read. Try re-saving it and uploading again."
        )
        await session.flush()
        logger.warning(
            "resume_extraction_failed", resume_id=str(resume.id), error_type=type(exc).__name__
        )
        return resume

    resume.extracted_text = text
    resume.extraction_payload = _serialize_proposal(proposal)
    resume.extraction_status = "succeeded"
    resume.extraction_error = None
    await session.flush()
    logger.info(
        "resume_extracted",
        resume_id=str(resume.id),
        item_count=len(proposal.items),
        unparsed_sections=list(proposal.unparsed_sections),
    )
    return resume


def _find_possible_duplicate(item: ExtractedItem, *, profile: Profile) -> uuid.UUID | None:
    """An existing row this item might describe again. Advisory only."""
    if item.field == "skill":
        canonical = canonical_skill(str(item.data.get("name", "")))
        if not canonical:
            return None
        return next((s.id for s in profile.skills if s.canonical_name == canonical), None)
    if item.field == "education":
        institution = str(item.data.get("institution", "")).strip().lower()
        if not institution:
            return None
        return next(
            (
                r.id
                for r in profile.education_records
                if r.institution.strip().lower() == institution
            ),
            None,
        )
    if item.field == "experience":
        company = str(item.data.get("company", "")).strip().lower()
        if not company:
            return None
        return next(
            (r.id for r in profile.experience_records if r.company.strip().lower() == company),
            None,
        )
    if item.field == "certification":
        name = str(item.data.get("name", "")).strip().lower()
        if not name:
            return None
        return next((c.id for c in profile.certifications if c.name.strip().lower() == name), None)
    return None


async def build_proposal_out(
    session: AsyncSession, *, user_id: uuid.UUID, resume: Resume
) -> ExtractionProposalOut:
    """The staged proposal, with a possible-duplicate hint computed against the
    profile's *current* state — recomputed on every read rather than stored, since
    the profile can change between an extraction and a confirmation."""
    profile = await get_profile(session, user_id=user_id)
    proposal = _deserialize_proposal(resume.extraction_payload)
    items = [
        ExtractionItemOut(
            index=index,
            field=item.field,
            data=item.data,
            source_excerpt=item.source_excerpt,
            locator=item.locator,
            confidence=item.confidence,
            needs_review=item.needs_review,
            possible_duplicate_of=_find_possible_duplicate(item, profile=profile),
        )
        for index, item in enumerate(proposal.items)
    ]
    return ExtractionProposalOut(
        resume_id=resume.id,
        extraction_status=resume.extraction_status,
        items=items,
        unparsed_sections=list(proposal.unparsed_sections),
    )


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


async def _apply_skill(
    session: AsyncSession, *, profile: Profile, item: ExtractedItem
) -> ConfirmExtractionResultOut:
    name = str(item.data.get("name", "")).strip()
    canonical = canonical_skill(name)
    existing = next((s for s in profile.skills if s.canonical_name == canonical), None)
    if existing is not None:
        return ConfirmExtractionResultOut(
            index=-1,
            field="skill",
            status="skipped",
            record_id=existing.id,
            message="Already on your profile.",
        )

    record = Skill(
        profile_id=profile.id,
        name=name,
        canonical_name=canonical,
        provenance=ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
    )
    session.add(record)
    await session.flush()
    return ConfirmExtractionResultOut(
        index=-1, field="skill", status="created", record_id=record.id
    )


async def _apply_education(
    session: AsyncSession,
    *,
    profile: Profile,
    item: ExtractedItem,
    existing_record_id: uuid.UUID | None,
) -> ConfirmExtractionResultOut:
    data = item.data
    try:
        level = EducationLevel(str(data.get("level") or "UNKNOWN"))
    except ValueError:
        level = EducationLevel.UNKNOWN

    if existing_record_id is not None:
        record = next((r for r in profile.education_records if r.id == existing_record_id), None)
        if record is None:
            return ConfirmExtractionResultOut(
                index=-1, field="education", status="rejected", message="No such existing record."
            )
        record.institution = str(data.get("institution") or record.institution)
        record.degree = _optional_str(data.get("degree")) or record.degree
        record.level = level
        record.start_date = _parse_iso_date(data.get("start_date")) or record.start_date
        record.end_date = _parse_iso_date(data.get("end_date")) or record.end_date
        record.provenance = ProvenanceKind.RESUME_EXTRACTION_CONFIRMED
        await session.flush()
        return ConfirmExtractionResultOut(
            index=-1, field="education", status="updated", record_id=record.id
        )

    record = EducationRecord(
        profile_id=profile.id,
        institution=str(data.get("institution") or "Unknown institution"),
        degree=data.get("degree"),
        level=level,
        start_date=_parse_iso_date(data.get("start_date")),
        end_date=_parse_iso_date(data.get("end_date")),
        provenance=ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
    )
    session.add(record)
    await session.flush()
    return ConfirmExtractionResultOut(
        index=-1, field="education", status="created", record_id=record.id
    )


async def _apply_experience(
    session: AsyncSession,
    *,
    profile: Profile,
    item: ExtractedItem,
    existing_record_id: uuid.UUID | None,
) -> ConfirmExtractionResultOut:
    data = item.data
    skills_used = data.get("skills_used")
    skills_used_list = list(skills_used) if isinstance(skills_used, list) else []

    if existing_record_id is not None:
        record = next((r for r in profile.experience_records if r.id == existing_record_id), None)
        if record is None:
            return ConfirmExtractionResultOut(
                index=-1, field="experience", status="rejected", message="No such existing record."
            )
        record.title = str(data.get("title") or record.title)
        record.company = str(data.get("company") or record.company)
        record.start_date = _parse_iso_date(data.get("start_date")) or record.start_date
        record.end_date = _parse_iso_date(data.get("end_date")) or record.end_date
        record.is_current = bool(data.get("is_current", record.is_current))
        record.description = _optional_str(data.get("description")) or record.description
        record.skills_used = skills_used_list or record.skills_used
        record.provenance = ProvenanceKind.RESUME_EXTRACTION_CONFIRMED
        await session.flush()
        return ConfirmExtractionResultOut(
            index=-1, field="experience", status="updated", record_id=record.id
        )

    record = ExperienceRecord(
        profile_id=profile.id,
        company=str(data.get("company") or "Unknown company"),
        title=str(data.get("title") or "Unknown title"),
        description=data.get("description"),
        skills_used=skills_used_list,
        start_date=_parse_iso_date(data.get("start_date")),
        end_date=_parse_iso_date(data.get("end_date")),
        is_current=bool(data.get("is_current", False)),
        provenance=ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
    )
    session.add(record)
    await session.flush()
    return ConfirmExtractionResultOut(
        index=-1, field="experience", status="created", record_id=record.id
    )


async def _apply_certification(
    session: AsyncSession,
    *,
    profile: Profile,
    item: ExtractedItem,
    existing_record_id: uuid.UUID | None,
) -> ConfirmExtractionResultOut:
    data = item.data
    if existing_record_id is not None:
        record = next((c for c in profile.certifications if c.id == existing_record_id), None)
        if record is None:
            return ConfirmExtractionResultOut(
                index=-1,
                field="certification",
                status="rejected",
                message="No such existing record.",
            )
        record.name = str(data.get("name") or record.name)
        record.provenance = ProvenanceKind.RESUME_EXTRACTION_CONFIRMED
        await session.flush()
        return ConfirmExtractionResultOut(
            index=-1, field="certification", status="updated", record_id=record.id
        )

    record = Certification(
        profile_id=profile.id,
        name=str(data.get("name") or "Unknown certification"),
        issuer=data.get("issuer"),
        provenance=ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
    )
    session.add(record)
    await session.flush()
    return ConfirmExtractionResultOut(
        index=-1, field="certification", status="created", record_id=record.id
    )


async def _apply_project(
    session: AsyncSession, *, profile: Profile, item: ExtractedItem
) -> ConfirmExtractionResultOut:
    data = item.data
    skills_used = data.get("skills_used")
    record = Project(
        profile_id=profile.id,
        name=str(data.get("name") or "Untitled project"),
        description=data.get("description"),
        url=data.get("url"),
        repository_url=data.get("repository_url"),
        skills_used=list(skills_used) if isinstance(skills_used, list) else [],
        provenance=ProvenanceKind.RESUME_EXTRACTION_CONFIRMED,
    )
    session.add(record)
    await session.flush()
    return ConfirmExtractionResultOut(
        index=-1, field="project", status="created", record_id=record.id
    )


async def _apply_achievement(
    session: AsyncSession, *, user_id: uuid.UUID, item: ExtractedItem
) -> ConfirmExtractionResultOut:
    record = ProfileEvidence(
        user_id=user_id,
        source_kind="resume_section",
        content=str(item.data.get("text", "")),
        locator=item.locator,
        provenance=ProvenanceKind.USER_APPROVED_EVIDENCE,
        approved_at=utcnow(),
    )
    session.add(record)
    await session.flush()
    return ConfirmExtractionResultOut(
        index=-1, field="achievement", status="created", record_id=record.id
    )


async def confirm_extraction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    resume_id: uuid.UUID,
    decisions: list[ExtractionDecision],
) -> list[ConfirmExtractionResultOut]:
    """Write the user's accepted items into the profile. Nothing else does.

    An accepted item creates a new row unless the decision names an existing
    record's id explicitly — silence always means "add as new" (or, for a skill
    that already exists, "leave it alone"), never "replace what's there". This is
    the one rule that keeps a confirmation from silently overwriting something the
    user already confirmed by hand.
    """
    resume = await get_owned_resume(session, user_id=user_id, resume_id=resume_id)
    proposal = _deserialize_proposal(resume.extraction_payload)
    profile = await get_profile(session, user_id=user_id)

    results: list[ConfirmExtractionResultOut] = []
    any_accepted = False
    touched_evidence_tables = False

    for decision in decisions:
        if decision.index < 0 or decision.index >= len(proposal.items):
            results.append(
                ConfirmExtractionResultOut(
                    index=decision.index,
                    field="achievement",
                    status="rejected",
                    message="No such item.",
                )
            )
            continue
        item = proposal.items[decision.index]
        if not decision.accept:
            results.append(
                ConfirmExtractionResultOut(
                    index=decision.index, field=item.field, status="rejected"
                )
            )
            continue

        any_accepted = True
        if item.field == "skill":
            result = await _apply_skill(session, profile=profile, item=item)
        elif item.field == "education":
            result = await _apply_education(
                session, profile=profile, item=item, existing_record_id=decision.existing_record_id
            )
        elif item.field == "experience":
            result = await _apply_experience(
                session, profile=profile, item=item, existing_record_id=decision.existing_record_id
            )
            touched_evidence_tables = True
        elif item.field == "certification":
            result = await _apply_certification(
                session, profile=profile, item=item, existing_record_id=decision.existing_record_id
            )
        elif item.field == "project":
            result = await _apply_project(session, profile=profile, item=item)
            touched_evidence_tables = True
        else:  # "achievement"
            result = await _apply_achievement(session, user_id=user_id, item=item)
        results.append(result.model_copy(update={"index": decision.index}))

    if touched_evidence_tables:
        await refresh_skill_evidence(session, profile=profile)

    if any_accepted:
        resume.extraction_confirmed_at = utcnow()
    await session.flush()

    logger.info(
        "resume_extraction_confirmed",
        resume_id=str(resume_id),
        user_id=str(user_id),
        accepted=sum(1 for r in results if r.status in {"created", "updated", "skipped"}),
        rejected=sum(1 for r in results if r.status == "rejected"),
    )
    return results
