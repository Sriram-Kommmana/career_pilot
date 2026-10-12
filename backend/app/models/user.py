"""User, profile, and the career knowledge base.

Ownership runs through ``user_id`` on every table that holds personal data, so an
authorisation check is always a single comparison rather than a join the caller might
forget. Child records of a profile reach the user through ``profile.user_id``; the
service layer loads the profile by user and never by id alone.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column
from app.domain.matching import EducationLevel
from app.domain.normalize import EmploymentType
from app.domain.provenance import ProvenanceKind


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True, index=True)
    #: Argon2id. Plaintext never reaches this table, a log line, or a prompt.
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)

    profile: Mapped[Profile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    resumes: Mapped[list[Resume]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Profile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The user's structured profile and search preferences.

    One row per user. Preference lists are JSONB arrays of strings rather than child
    tables: they are short, always read as a whole, and never joined against.
    """

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    full_name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    headline: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    location: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    github_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    highest_education: Mapped[EducationLevel] = mapped_column(
        enum_column(EducationLevel, name="education_level"),
        nullable=False,
        default=EducationLevel.UNKNOWN,
    )
    graduation_year: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    target_roles: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    #: Tri-state on purpose: ``None`` means the user has not said, which must not be
    #: read as "not open to remote".
    open_to_remote: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    employment_preferences: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    excluded_companies: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    excluded_roles: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    excluded_locations: Mapped[list[str]] = mapped_column(nullable=False, default=list)

    minimum_salary: Mapped[float | None] = mapped_column(sa.Numeric(14, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(sa.String(3), nullable=True)

    #: Only ever written from an explicit user answer. Never inferred from a location,
    #: a nationality, or anything else. See docs/SAFETY.md section 4.
    work_authorisation: Mapped[dict[str, object]] = mapped_column(nullable=False, default=dict)

    notify_score_threshold: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.6)

    user: Mapped[User] = relationship(back_populates="profile")
    education_records: Mapped[list[EducationRecord]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    skills: Mapped[list[Skill]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    experience_records: Mapped[list[ExperienceRecord]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    certifications: Mapped[list[Certification]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )

    __table_args__ = (
        sa.CheckConstraint(
            "notify_score_threshold >= 0 AND notify_score_threshold <= 1",
            name="notify_threshold_range",
        ),
        sa.CheckConstraint(
            "graduation_year IS NULL OR (graduation_year >= 1950 AND graduation_year <= 2100)",
            name="graduation_year_plausible",
        ),
    )


class EducationRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "education_records"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    institution: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    degree: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    field_of_study: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    level: Mapped[EducationLevel] = mapped_column(
        enum_column(EducationLevel, name="education_record_level"),
        nullable=False,
        default=EducationLevel.UNKNOWN,
    )
    start_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    grade: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="education_provenance"),
        nullable=False,
        default=ProvenanceKind.USER_CONFIRMED_PROFILE,
    )

    profile: Mapped[Profile] = relationship(back_populates="education_records")

    __table_args__ = (
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="education_dates_ordered",
        ),
    )


class Skill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A claimed skill.

    ``canonical_name`` is the comparison key produced by ``domain.skills``; it is
    stored so a query can match without recomputing, and indexed for the match engine.

    ``has_evidence`` is maintained from the project and experience records that cite
    the skill. It is the difference between "listed" and "demonstrated", and the
    evidence component of a match score rests entirely on it.
    """

    __tablename__ = "skills"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    canonical_name: Mapped[str] = mapped_column(sa.String(120), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    years: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    has_evidence: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="skill_provenance"),
        nullable=False,
        default=ProvenanceKind.USER_CONFIRMED_PROFILE,
    )

    profile: Mapped[Profile] = relationship(back_populates="skills")

    __table_args__ = (
        sa.UniqueConstraint("profile_id", "canonical_name", name="profile_id_canonical_name"),
    )


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    role: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    repository_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    #: Canonical skill names this project demonstrates. The source of ``has_evidence``.
    skills_used: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    highlights: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    start_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="project_provenance"),
        nullable=False,
        default=ProvenanceKind.USER_CONFIRMED_PROFILE,
    )

    profile: Mapped[Profile] = relationship(back_populates="projects")


class ExperienceRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A job the user has held.

    ``is_current`` with a null ``end_date`` is how an ongoing role is represented, so
    duration can be computed without inventing an end date.
    """

    __tablename__ = "experience_records"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    employment_type: Mapped[EmploymentType] = mapped_column(
        enum_column(EmploymentType, name="experience_employment_type"),
        nullable=False,
        default=EmploymentType.UNKNOWN,
    )
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    skills_used: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    highlights: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    start_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="experience_provenance"),
        nullable=False,
        default=ProvenanceKind.USER_CONFIRMED_PROFILE,
    )

    profile: Mapped[Profile] = relationship(back_populates="experience_records")

    __table_args__ = (
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="experience_dates_ordered",
        ),
        sa.CheckConstraint(
            "NOT (is_current AND end_date IS NOT NULL)",
            name="current_role_has_no_end_date",
        ),
    )


class Certification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "certifications"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    issuer: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    issued_on: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    credential_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="certification_provenance"),
        nullable=False,
        default=ProvenanceKind.USER_CONFIRMED_PROFILE,
    )

    profile: Mapped[Profile] = relationship(back_populates="certifications")


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An uploaded resume.

    The file itself lives on disk (or object storage) at ``storage_path``; only
    metadata is in the row. ``content_sha256`` is what the approval hash uses, so
    replacing a file with a different one of the same name invalidates an approval.

    ``extraction_confirmed_at`` is the gate on extracted facts: until the user has
    confirmed them, anything pulled out of this file carries
    ``RESUME_EXTRACTION_UNCONFIRMED`` provenance and cannot be submitted.
    """

    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    is_default: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    extracted_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    extraction_payload: Mapped[dict[str, object] | None] = mapped_column(nullable=True)
    extraction_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    user: Mapped[User] = relationship(back_populates="resumes")

    __table_args__ = (
        sa.UniqueConstraint("user_id", "label", name="user_id_label"),
        sa.CheckConstraint("size_bytes > 0", name="resume_size_positive"),
    )

    @property
    def extraction_is_confirmed(self) -> bool:
        return self.extraction_confirmed_at is not None


class ProfileEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A retrievable chunk of evidence about the candidate.

    Phase 3 adds a pgvector ``embedding`` column here and populates it from projects,
    experience, and confirmed resume sections. The table exists now so the retrieval
    contract is fixed and the migration that adds the vector column is additive.

    Nothing sensitive goes in here. It holds career text the user has approved for
    retrieval, never credentials, session state, or answers to sensitive questions.
    """

    __tablename__ = "profile_evidence"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: "project" | "experience" | "resume_section" | "certification"
    source_kind: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    #: Human-checkable pointer, e.g. "resume:page=2,section=Experience".
    locator: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    skills: Mapped[list[str]] = mapped_column(nullable=False, default=list)
    provenance: Mapped[ProvenanceKind] = mapped_column(
        enum_column(ProvenanceKind, name="evidence_provenance"),
        nullable=False,
        default=ProvenanceKind.USER_APPROVED_EVIDENCE,
    )
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (sa.Index("ix_profile_evidence_user_source", "user_id", "source_kind"),)
