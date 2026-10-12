"""Resume routes.

Listing and deletion work now, because the tracker and the review screen need to know
which document is attached. Upload and extraction belong to Phase 3 and return 501:
the extraction pipeline, the user-confirmation step that makes extracted facts
authoritative, and the storage quota handling are all part of that work, and a
half-built upload that stored files without any of it would be worse than none.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import select

from app.api.deps import CurrentUserId, DbSession
from app.core.errors import NotFoundError, NotImplementedPhaseError
from app.models import Resume
from app.schemas.common import MessageResponse
from app.schemas.profile import ResumeOut

router = APIRouter(prefix="/resumes", tags=["resumes"])

ResumeId = Annotated[uuid.UUID, Path(description="Resume id.")]


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


@router.post("")
async def upload_resume(user_id: CurrentUserId) -> dict[str, object]:
    """Upload a resume file.

    Not implemented in Phase 1. Phase 3 adds the upload, text extraction, and the
    confirmation step that makes extracted facts authoritative.
    """
    raise NotImplementedPhaseError("Resume upload and extraction", phase=3)


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

    from pathlib import Path as FsPath

    stored = FsPath(row.storage_path)
    await session.delete(row)
    await session.flush()

    def remove_file() -> None:
        if stored.is_file():
            stored.unlink(missing_ok=True)

    # Filesystem calls block; run them off the event loop.
    await asyncio.to_thread(remove_file)
    return MessageResponse(message="Resume deleted.")
