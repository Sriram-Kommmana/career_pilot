"""Profile and career knowledge base routes.

Every handler passes ``user_id`` into the service, which scopes its queries by it.
There is no route that takes a profile id, so one user cannot address another's
profile at all.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import CurrentUserId, DbSession
from app.schemas.common import MessageResponse
from app.schemas.profile import (
    CertificationIn,
    CertificationOut,
    EducationIn,
    EducationOut,
    ExperienceIn,
    ExperienceOut,
    ProfileOut,
    ProfileUpdate,
    ProjectIn,
    ProjectOut,
    SkillIn,
    SkillOut,
)
from app.services import profile as profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
async def read_profile(session: DbSession, user_id: CurrentUserId) -> ProfileOut:
    profile = await profile_service.get_profile(session, user_id=user_id)
    return ProfileOut.model_validate(profile)


@router.patch("", response_model=ProfileOut)
async def patch_profile(
    payload: ProfileUpdate, session: DbSession, user_id: CurrentUserId
) -> ProfileOut:
    """Apply a partial update. Fields the client omits are left unchanged."""
    profile = await profile_service.update_profile(
        session, user_id=user_id, changes=payload.to_changes()
    )
    profile = await profile_service.get_profile(session, user_id=user_id)
    return ProfileOut.model_validate(profile)


# ---------------------------------------------------------------------------
# Child collections
#
# Five collections with identical CRUD shapes. They are written out rather than
# generated so the OpenAPI document names a concrete request and response model for
# each, which is what makes the generated client usable.
# ---------------------------------------------------------------------------

RecordId = Annotated[uuid.UUID, Path(description="Id of the record to change.")]


@router.post("/education", response_model=EducationOut, status_code=status.HTTP_201_CREATED)
async def add_education(
    payload: EducationIn, session: DbSession, user_id: CurrentUserId
) -> EducationOut:
    record = await profile_service.add_child(
        session, user_id=user_id, collection="education", payload=payload.model_dump()
    )
    return EducationOut.model_validate(record)


@router.patch("/education/{record_id}", response_model=EducationOut)
async def update_education(
    record_id: RecordId, payload: EducationIn, session: DbSession, user_id: CurrentUserId
) -> EducationOut:
    record = await profile_service.update_child(
        session,
        user_id=user_id,
        collection="education",
        record_id=record_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return EducationOut.model_validate(record)


@router.delete("/education/{record_id}", response_model=MessageResponse)
async def delete_education(
    record_id: RecordId, session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    await profile_service.delete_child(
        session, user_id=user_id, collection="education", record_id=record_id
    )
    return MessageResponse(message="Education record deleted.")


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def add_skill(payload: SkillIn, session: DbSession, user_id: CurrentUserId) -> SkillOut:
    """Add a skill.

    ``has_evidence`` is not settable here. It is derived from the projects and
    experience records that cite the skill.
    """
    record = await profile_service.add_child(
        session, user_id=user_id, collection="skills", payload=payload.model_dump()
    )
    return SkillOut.model_validate(record)


@router.patch("/skills/{record_id}", response_model=SkillOut)
async def update_skill(
    record_id: RecordId, payload: SkillIn, session: DbSession, user_id: CurrentUserId
) -> SkillOut:
    record = await profile_service.update_child(
        session,
        user_id=user_id,
        collection="skills",
        record_id=record_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return SkillOut.model_validate(record)


@router.delete("/skills/{record_id}", response_model=MessageResponse)
async def delete_skill(
    record_id: RecordId, session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    await profile_service.delete_child(
        session, user_id=user_id, collection="skills", record_id=record_id
    )
    return MessageResponse(message="Skill deleted.")


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def add_project(payload: ProjectIn, session: DbSession, user_id: CurrentUserId) -> ProjectOut:
    record = await profile_service.add_child(
        session, user_id=user_id, collection="projects", payload=payload.model_dump()
    )
    return ProjectOut.model_validate(record)


@router.patch("/projects/{record_id}", response_model=ProjectOut)
async def update_project(
    record_id: RecordId, payload: ProjectIn, session: DbSession, user_id: CurrentUserId
) -> ProjectOut:
    record = await profile_service.update_child(
        session,
        user_id=user_id,
        collection="projects",
        record_id=record_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return ProjectOut.model_validate(record)


@router.delete("/projects/{record_id}", response_model=MessageResponse)
async def delete_project(
    record_id: RecordId, session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    await profile_service.delete_child(
        session, user_id=user_id, collection="projects", record_id=record_id
    )
    return MessageResponse(message="Project deleted.")


@router.post("/experience", response_model=ExperienceOut, status_code=status.HTTP_201_CREATED)
async def add_experience(
    payload: ExperienceIn, session: DbSession, user_id: CurrentUserId
) -> ExperienceOut:
    record = await profile_service.add_child(
        session, user_id=user_id, collection="experience", payload=payload.model_dump()
    )
    return ExperienceOut.model_validate(record)


@router.patch("/experience/{record_id}", response_model=ExperienceOut)
async def update_experience(
    record_id: RecordId, payload: ExperienceIn, session: DbSession, user_id: CurrentUserId
) -> ExperienceOut:
    record = await profile_service.update_child(
        session,
        user_id=user_id,
        collection="experience",
        record_id=record_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return ExperienceOut.model_validate(record)


@router.delete("/experience/{record_id}", response_model=MessageResponse)
async def delete_experience(
    record_id: RecordId, session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    await profile_service.delete_child(
        session, user_id=user_id, collection="experience", record_id=record_id
    )
    return MessageResponse(message="Experience record deleted.")


@router.post(
    "/certifications", response_model=CertificationOut, status_code=status.HTTP_201_CREATED
)
async def add_certification(
    payload: CertificationIn, session: DbSession, user_id: CurrentUserId
) -> CertificationOut:
    record = await profile_service.add_child(
        session, user_id=user_id, collection="certifications", payload=payload.model_dump()
    )
    return CertificationOut.model_validate(record)


@router.patch("/certifications/{record_id}", response_model=CertificationOut)
async def update_certification(
    record_id: RecordId,
    payload: CertificationIn,
    session: DbSession,
    user_id: CurrentUserId,
) -> CertificationOut:
    record = await profile_service.update_child(
        session,
        user_id=user_id,
        collection="certifications",
        record_id=record_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return CertificationOut.model_validate(record)


@router.delete("/certifications/{record_id}", response_model=MessageResponse)
async def delete_certification(
    record_id: RecordId, session: DbSession, user_id: CurrentUserId
) -> MessageResponse:
    await profile_service.delete_child(
        session, user_id=user_id, collection="certifications", record_id=record_id
    )
    return MessageResponse(message="Certification deleted.")
