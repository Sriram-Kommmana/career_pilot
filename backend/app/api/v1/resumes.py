"""Resume routes.

Upload, extraction, and confirmation are three separate requests on purpose: a
client can upload a file without triggering extraction, re-run extraction on an
already-uploaded file, and confirm facts independently of either — matching the
three trust levels described in ``app.services.resumes``.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, UploadFile, status
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUserId, DbSession
from app.core.errors import NotFoundError, PayloadTooLargeError
from app.models import Resume
from app.schemas.common import MessageResponse
from app.schemas.resume import (
    ConfirmExtractionRequest,
    ConfirmExtractionResponse,
    ExtractionProposalOut,
    ResumeOut,
)
from app.services import resumes as resumes_service

router = APIRouter(prefix="/resumes", tags=["resumes"])

ResumeId = Annotated[uuid.UUID, Path(description="Resume id.")]

#: Read in chunks rather than all at once, so an oversized upload is rejected as
#: soon as it crosses the limit instead of being fully buffered into memory first.
_READ_CHUNK_BYTES = 256 * 1024


@router.get("", response_model=list[ResumeOut])
async def list_resumes(session: DbSession, user_id: CurrentUserId) -> list[ResumeOut]:
    rows = (
        (
            await session.execute(
                select(Resume)
                .where(Resume.user_id == user_id)
                .order_by(Resume.is_default.desc(), Resume.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ResumeOut.model_validate(row) for row in rows]


@router.get("/{resume_id}", response_model=ResumeOut)
async def get_resume(resume_id: ResumeId, session: DbSession, user_id: CurrentUserId) -> ResumeOut:
    row = (
        await session.execute(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Resume not found.")
    return ResumeOut.model_validate(row)


async def _read_bounded(file: UploadFile, *, max_bytes: int) -> bytes:
    """Read an upload's body, refusing as soon as it exceeds ``max_bytes``.

    Never calls ``file.read()`` with no size argument — that would buffer an
    arbitrarily large body into memory before the limit could be checked at all.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"File exceeds the {max_bytes}-byte upload limit.",
                details={"max_upload_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    session: DbSession,
    user_id: CurrentUserId,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="A PDF, DOCX, or plain text resume.")],
    label: Annotated[str | None, Form()] = None,
    is_default: Annotated[bool, Form()] = False,
) -> ResumeOut:
    """Upload a resume file.

    Validates the file's actual content against its claimed name (not just its
    extension or ``Content-Type`` header), stores it under a server-generated name,
    and records it. This endpoint never reads the document's text and never touches
    a profile — see ``POST /resumes/{resume_id}/extract`` for that.
    """
    content = await _read_bounded(file, max_bytes=settings.max_upload_bytes)
    resume = await resumes_service.upload_resume(
        session,
        user_id=user_id,
        original_filename=file.filename or "resume",
        content=content,
        label=label,
        is_default=is_default,
        settings=settings,
    )
    return ResumeOut.model_validate(resume)


@router.post("/{resume_id}/extract", response_model=ExtractionProposalOut)
async def extract_resume(
    resume_id: ResumeId, session: DbSession, user_id: CurrentUserId
) -> ExtractionProposalOut:
    """Read the file into text and stage a proposal of candidate facts.

    Nothing here is written to the profile. A proposal is only a starting point
    until it is reviewed through ``POST /resumes/{resume_id}/confirm-extraction``.
    Re-running this replaces the staged proposal; it never touches rows an earlier
    confirmation already wrote.
    """
    resume = await resumes_service.extract_resume(session, user_id=user_id, resume_id=resume_id)
    return await resumes_service.build_proposal_out(session, user_id=user_id, resume=resume)


@router.get("/{resume_id}/extraction", response_model=ExtractionProposalOut)
async def get_extraction(
    resume_id: ResumeId, session: DbSession, user_id: CurrentUserId
) -> ExtractionProposalOut:
    """Re-fetch the most recently staged proposal, without re-running extraction."""
    resume = await resumes_service.get_owned_resume(session, user_id=user_id, resume_id=resume_id)
    return await resumes_service.build_proposal_out(session, user_id=user_id, resume=resume)


@router.post("/{resume_id}/confirm-extraction", response_model=ConfirmExtractionResponse)
async def confirm_extraction(
    resume_id: ResumeId,
    payload: ConfirmExtractionRequest,
    session: DbSession,
    user_id: CurrentUserId,
) -> ConfirmExtractionResponse:
    """Write the accepted items into the profile.

    The only endpoint that turns a staged proposal into authoritative profile data.
    An item is only ever written because it appears, explicitly accepted, in this
    request — there is no path from upload or extraction alone to a changed profile.
    """
    results = await resumes_service.confirm_extraction(
        session, user_id=user_id, resume_id=resume_id, decisions=payload.decisions
    )
    return ConfirmExtractionResponse(resume_id=resume_id, results=results)


@router.delete("/{resume_id}", response_model=MessageResponse)
async def delete_resume(
    resume_id: ResumeId, session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    """Delete a resume.

    The stored file is removed alongside the row. Applications referencing it keep
    their row — ``resume_id`` is nulled by the foreign key — so an application's
    history is not destroyed by deleting a document.
    """
    row = (
        await session.execute(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Resume not found.")

    stored = FsPath(row.storage_path)
    await session.delete(row)
    await session.flush()

    def remove_file() -> None:
        if stored.is_file():
            stored.unlink(missing_ok=True)

    # Filesystem calls block; run them off the event loop.
    await asyncio.to_thread(remove_file)
    return MessageResponse(message="Resume deleted.")
