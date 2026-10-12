"""Profile contracts.

Note what is *not* here: there is no request field that sets ``has_evidence`` on a
skill. That flag is derived from the projects and experience records that cite the
skill, because a user asserting "this is evidenced" would make the evidence component
of a match score meaningless. See ``app.services.profile.refresh_skill_evidence``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import Field, HttpUrl, field_validator

from app.domain.matching import EducationLevel
from app.domain.normalize import EmploymentType
from app.domain.provenance import ProvenanceKind
from app.schemas.common import ApiModel, ApiRequest

Name = Annotated[str, Field(min_length=1, max_length=200)]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class EducationOut(ApiModel):
    id: uuid.UUID
    institution: str
    degree: str | None
    field_of_study: str | None
    level: EducationLevel
    start_date: date | None
    end_date: date | None
    grade: str | None
    provenance: ProvenanceKind


class EducationIn(ApiRequest):
    institution: ShortText
    degree: str | None = None
    field_of_study: str | None = None
    level: EducationLevel = EducationLevel.UNKNOWN
    start_date: date | None = None
    end_date: date | None = None
    grade: str | None = None


class SkillOut(ApiModel):
    id: uuid.UUID
    name: str
    canonical_name: str
    category: str | None
    years: float | None
    #: Derived, not user-set: true when a project or role cites this skill.
    has_evidence: bool
    provenance: ProvenanceKind


class SkillIn(ApiRequest):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    category: str | None = None
    years: Annotated[float | None, Field(ge=0, le=60)] = None


class ProjectOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str | None
    role: str | None
    url: str | None
    repository_url: str | None
    skills_used: list[str]
    highlights: list[str]
    start_date: date | None
    end_date: date | None


class ProjectIn(ApiRequest):
    name: Name
    description: str | None = None
    role: str | None = None
    url: HttpUrl | None = None
    repository_url: HttpUrl | None = None
    skills_used: list[str] = Field(default_factory=list, max_length=50)
    highlights: list[str] = Field(default_factory=list, max_length=20)
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("url", "repository_url", mode="after")
    @classmethod
    def _to_string(cls, value: HttpUrl | None) -> str | None:
        return str(value) if value is not None else None


class ExperienceOut(ApiModel):
    id: uuid.UUID
    company: str
    title: str
    location: str | None
    employment_type: EmploymentType
    description: str | None
    skills_used: list[str]
    highlights: list[str]
    start_date: date | None
    end_date: date | None
    is_current: bool


class ExperienceIn(ApiRequest):
    company: ShortText
    title: Name
    location: str | None = None
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    description: str | None = None
    skills_used: list[str] = Field(default_factory=list, max_length=50)
    highlights: list[str] = Field(default_factory=list, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False


class CertificationOut(ApiModel):
    id: uuid.UUID
    name: str
    issuer: str | None
    issued_on: date | None
    expires_on: date | None
    credential_url: str | None


class CertificationIn(ApiRequest):
    name: ShortText
    issuer: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None
    credential_url: HttpUrl | None = None

    @field_validator("credential_url", mode="after")
    @classmethod
    def _to_string(cls, value: HttpUrl | None) -> str | None:
        return str(value) if value is not None else None


class WorkAuthorisation(ApiRequest):
    """Work-authorisation answers, only ever as the user stated them.

    Free-text strings rather than booleans: forms ask this in many phrasings, and
    storing the user's own wording avoids re-interpreting their answer. Nothing here
    is ever inferred from a location or anything else.
    """

    authorised: str | None = None
    requires_sponsorship: str | None = None
    notes: Annotated[str | None, Field(max_length=1000)] = None


class ProfileOut(ApiModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str | None
    headline: str | None
    phone: str | None
    location: str | None
    github_url: str | None
    linkedin_url: str | None
    portfolio_url: str | None

    highest_education: EducationLevel
    graduation_year: int | None

    target_roles: list[str]
    preferred_locations: list[str]
    open_to_remote: bool | None
    employment_preferences: list[str]

    excluded_companies: list[str]
    excluded_roles: list[str]
    excluded_locations: list[str]

    minimum_salary: float | None
    salary_currency: str | None
    work_authorisation: dict[str, object]
    notify_score_threshold: float

    education_records: list[EducationOut] = Field(default_factory=list)
    skills: list[SkillOut] = Field(default_factory=list)
    projects: list[ProjectOut] = Field(default_factory=list)
    experience_records: list[ExperienceOut] = Field(default_factory=list)
    certifications: list[CertificationOut] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime


class ProfileUpdate(ApiRequest):
    """A partial update. Omitted fields are left alone, not cleared."""

    full_name: Annotated[str | None, Field(max_length=200)] = None
    headline: Annotated[str | None, Field(max_length=300)] = None
    phone: Annotated[str | None, Field(max_length=40)] = None
    location: Annotated[str | None, Field(max_length=200)] = None
    github_url: Annotated[str | None, Field(max_length=500)] = None
    linkedin_url: Annotated[str | None, Field(max_length=500)] = None
    portfolio_url: Annotated[str | None, Field(max_length=500)] = None

    highest_education: EducationLevel | None = None
    graduation_year: Annotated[int | None, Field(ge=1950, le=2100)] = None

    target_roles: Annotated[list[str] | None, Field(max_length=30)] = None
    preferred_locations: Annotated[list[str] | None, Field(max_length=30)] = None
    open_to_remote: bool | None = None
    employment_preferences: list[EmploymentType] | None = None

    excluded_companies: Annotated[list[str] | None, Field(max_length=200)] = None
    excluded_roles: Annotated[list[str] | None, Field(max_length=100)] = None
    excluded_locations: Annotated[list[str] | None, Field(max_length=100)] = None

    minimum_salary: Annotated[float | None, Field(ge=0)] = None
    salary_currency: Annotated[str | None, Field(min_length=3, max_length=3)] = None
    work_authorisation: WorkAuthorisation | None = None
    notify_score_threshold: Annotated[float | None, Field(ge=0.0, le=1.0)] = None

    def to_changes(self) -> dict[str, object]:
        """Only the fields the client actually sent."""
        payload = self.model_dump(exclude_unset=True, exclude_none=False)
        if "employment_preferences" in payload and payload["employment_preferences"] is not None:
            payload["employment_preferences"] = [
                item.value if hasattr(item, "value") else str(item)
                for item in payload["employment_preferences"]
            ]
        if "work_authorisation" in payload and payload["work_authorisation"] is not None:
            payload["work_authorisation"] = {
                key: value
                for key, value in payload["work_authorisation"].items()
                if value is not None
            }
        return payload


class ResumeOut(ApiModel):
    id: uuid.UUID
    label: str
    original_filename: str
    content_type: str
    size_bytes: int
    content_sha256: str
    is_default: bool
    #: Until this is set, facts extracted from the file are not authoritative.
    extraction_confirmed_at: datetime | None
    created_at: datetime
