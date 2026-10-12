"""Baseline schema.

Creates the full CareerPilot schema: users and the career knowledge base, companies,
job sources, jobs and requirements, match assessments, applications with their
fields, answers, events and approvals, notifications, and the agent-run observability
tables.

The pgvector extension is enabled here even though no vector column exists yet. The
embedding dimension depends on the model chosen in Phase 3, so adding the column now
would mean guessing; enabling the extension is free and makes that later migration
purely additive.

Revision ID: 065da2315c77
Revises:
Create Date: 2026-10-11 23:15:47.856484
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "065da2315c77"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "companies",
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("canonical_name", sa.String(length=300), nullable=False),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("careers_url", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    op.create_index(
        op.f("ix_companies_canonical_name"), "companies", ["canonical_name"], unique=True
    )
    op.create_table(
        "job_sources",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("external_key", sa.String(length=200), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_sources")),
        sa.UniqueConstraint("kind", "external_key", name="kind_external_key"),
    )
    op.create_index(op.f("ix_job_sources_kind"), "job_sources", ["kind"], unique=False)
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "jobs",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("job_source_id", sa.UUID(), nullable=False),
        sa.Column("source_job_id", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("canonical_title", sa.String(length=400), nullable=False),
        sa.Column("location_raw", sa.String(length=300), nullable=True),
        sa.Column("canonical_location", sa.String(length=300), nullable=True),
        sa.Column(
            "work_arrangement",
            sa.Enum(
                "REMOTE",
                "HYBRID",
                "ONSITE",
                "UNKNOWN",
                name="job_work_arrangement",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "employment_type",
            sa.Enum(
                "FULL_TIME",
                "PART_TIME",
                "INTERNSHIP",
                "CONTRACT",
                "TEMPORARY",
                "APPRENTICESHIP",
                "UNKNOWN",
                name="job_employment_type",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("responsibilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("salary_min", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("salary_max", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
        sa.Column("salary_period", sa.String(length=20), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "posted_at_precision",
            sa.Enum(
                "EXACT",
                "DATE_ONLY",
                "UNKNOWN",
                name="job_posted_at_precision",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("application_url", sa.String(length=1000), nullable=False),
        sa.Column(
            "source_status",
            sa.Enum(
                "OPEN",
                "CLOSED",
                "DRAFT",
                "UNKNOWN",
                name="job_source_status",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(posted_at IS NULL AND posted_at_precision = 'UNKNOWN') OR (posted_at IS NOT NULL AND posted_at_precision <> 'UNKNOWN')",
            name=op.f("ck_jobs_posted_at_precision_consistent"),
        ),
        sa.CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min",
            name=op.f("ck_jobs_salary_range_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_jobs_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_source_id"],
            ["job_sources.id"],
            name=op.f("fk_jobs_job_source_id_job_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("job_source_id", "source_job_id", name="job_source_id_source_job_id"),
    )
    op.create_index(op.f("ix_jobs_canonical_title"), "jobs", ["canonical_title"], unique=False)
    op.create_index(op.f("ix_jobs_company_id"), "jobs", ["company_id"], unique=False)
    op.create_index("ix_jobs_feed", "jobs", ["last_observed_at", "is_demo_data"], unique=False)
    op.create_index(op.f("ix_jobs_fingerprint"), "jobs", ["fingerprint"], unique=False)
    op.create_index(op.f("ix_jobs_is_demo_data"), "jobs", ["is_demo_data"], unique=False)
    op.create_index(op.f("ix_jobs_job_source_id"), "jobs", ["job_source_id"], unique=False)
    op.create_index(op.f("ix_jobs_last_observed_at"), "jobs", ["last_observed_at"], unique=False)
    op.create_table(
        "profile_evidence",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("locator", sa.String(length=300), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="evidence_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_profile_evidence_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_evidence")),
    )
    op.create_index(
        op.f("ix_profile_evidence_user_id"), "profile_evidence", ["user_id"], unique=False
    )
    op.create_index(
        "ix_profile_evidence_user_source",
        "profile_evidence",
        ["user_id", "source_kind"],
        unique=False,
    )
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("headline", sa.String(length=300), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("github_url", sa.String(length=500), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("portfolio_url", sa.String(length=500), nullable=True),
        sa.Column(
            "highest_education",
            sa.Enum(
                "UNKNOWN",
                "HIGH_SCHOOL",
                "DIPLOMA",
                "BACHELORS",
                "MASTERS",
                "DOCTORATE",
                name="education_level",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("graduation_year", sa.Integer(), nullable=True),
        sa.Column("target_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preferred_locations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("open_to_remote", sa.Boolean(), nullable=True),
        sa.Column(
            "employment_preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("excluded_companies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excluded_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excluded_locations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("minimum_salary", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
        sa.Column("work_authorisation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notify_score_threshold", sa.Float(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "graduation_year IS NULL OR (graduation_year >= 1950 AND graduation_year <= 2100)",
            name=op.f("ck_profiles_graduation_year_plausible"),
        ),
        sa.CheckConstraint(
            "notify_score_threshold >= 0 AND notify_score_threshold <= 1",
            name=op.f("ck_profiles_notify_threshold_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_profiles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
    )
    op.create_index(op.f("ix_profiles_user_id"), "profiles", ["user_id"], unique=True)
    op.create_table(
        "resumes",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("original_filename", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1000), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extraction_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extraction_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_resumes_resume_size_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_resumes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resumes")),
        sa.UniqueConstraint("user_id", "label", name="user_id_label"),
    )
    op.create_index(op.f("ix_resumes_content_sha256"), "resumes", ["content_sha256"], unique=False)
    op.create_index(op.f("ix_resumes_user_id"), "resumes", ["user_id"], unique=False)
    op.create_table(
        "applications",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=True),
        sa.Column(
            "state",
            sa.Enum(
                "DISCOVERED",
                "MATCHED",
                "SELECTED",
                "PREPARING",
                "READY",
                "FILLING",
                "VERIFYING",
                "WAITING_FOR_USER_INPUT",
                "WAITING_FOR_APPROVAL",
                "SUBMITTING",
                "SUBMITTED",
                "SUBMITTED_UNCERTAIN",
                "FAILED",
                "CANCELLED",
                name="application_state",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "NONE",
                "ACKNOWLEDGED",
                "INTERVIEW",
                "OFFER",
                "REJECTED",
                "WITHDRAWN",
                "CLOSED",
                name="application_outcome",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("outcome_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_url", sa.String(length=1000), nullable=False),
        sa.Column("form_state_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum(
                "PASSED",
                "FAILED",
                "INCONCLUSIVE",
                "NOT_ATTEMPTED",
                name="application_verification_status",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "unresolved_interventions", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("submission_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submission_idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(submitted_at IS NULL) OR (state IN ('SUBMITTED', 'SUBMITTED_UNCERTAIN'))",
            name=op.f("ck_applications_submitted_at_requires_submitted_state"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_applications_job_id_jobs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resume_id"],
            ["resumes.id"],
            name=op.f("fk_applications_resume_id_resumes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_applications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint(
            "submission_idempotency_key", name=op.f("uq_applications_submission_idempotency_key")
        ),
    )
    op.create_index(op.f("ix_applications_job_id"), "applications", ["job_id"], unique=False)
    op.create_index(op.f("ix_applications_state"), "applications", ["state"], unique=False)
    op.create_index(op.f("ix_applications_user_id"), "applications", ["user_id"], unique=False)
    op.create_index(
        "ix_applications_user_state", "applications", ["user_id", "state"], unique=False
    )
    op.create_index(
        "uq_applications_live_user_job",
        "applications",
        ["user_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('CANCELLED', 'FAILED')"),
    )
    op.create_table(
        "certifications",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("issuer", sa.String(length=300), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("credential_url", sa.String(length=500), nullable=True),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="certification_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_certifications_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certifications")),
    )
    op.create_index(
        op.f("ix_certifications_profile_id"), "certifications", ["profile_id"], unique=False
    )
    op.create_table(
        "education_records",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("institution", sa.String(length=300), nullable=False),
        sa.Column("degree", sa.String(length=200), nullable=True),
        sa.Column("field_of_study", sa.String(length=200), nullable=True),
        sa.Column(
            "level",
            sa.Enum(
                "UNKNOWN",
                "HIGH_SCHOOL",
                "DIPLOMA",
                "BACHELORS",
                "MASTERS",
                "DOCTORATE",
                name="education_record_level",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("grade", sa.String(length=50), nullable=True),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="education_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name=op.f("ck_education_records_education_dates_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_education_records_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_education_records")),
    )
    op.create_index(
        op.f("ix_education_records_profile_id"), "education_records", ["profile_id"], unique=False
    )
    op.create_table(
        "experience_records",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column(
            "employment_type",
            sa.Enum(
                "FULL_TIME",
                "PART_TIME",
                "INTERNSHIP",
                "CONTRACT",
                "TEMPORARY",
                "APPRENTICESHIP",
                "UNKNOWN",
                name="experience_employment_type",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("skills_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("highlights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="experience_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (is_current AND end_date IS NOT NULL)",
            name=op.f("ck_experience_records_current_role_has_no_end_date"),
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name=op.f("ck_experience_records_experience_dates_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_experience_records_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experience_records")),
    )
    op.create_index(
        op.f("ix_experience_records_profile_id"), "experience_records", ["profile_id"], unique=False
    )
    op.create_table(
        "job_match_assessments",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "score_kind",
            sa.Enum("RANKING_HEURISTIC", name="match_score_kind", native_enum=False, length=48),
            nullable=False,
        ),
        sa.Column("passed_hard_filters", sa.Boolean(), nullable=False),
        sa.Column("hard_filter_failures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assessed_weight", sa.Float(), nullable=False),
        sa.Column("requirement_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(length=20), nullable=False),
        sa.Column(
            "assessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "passed_hard_filters OR score IS NULL",
            name=op.f("ck_job_match_assessments_filtered_jobs_are_unscored"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name=op.f("ck_job_match_assessments_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_match_assessments_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_job_match_assessments_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_match_assessments")),
        sa.UniqueConstraint("user_id", "job_id", name="user_id_job_id"),
    )
    op.create_index(
        op.f("ix_job_match_assessments_job_id"), "job_match_assessments", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_job_match_assessments_user_id"), "job_match_assessments", ["user_id"], unique=False
    )
    op.create_index(
        "ix_match_user_score",
        "job_match_assessments",
        ["user_id", sa.literal_column("score DESC NULLS LAST")],
        unique=False,
    )
    op.create_table(
        "job_requirements",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_optional", sa.Boolean(), nullable=False),
        sa.Column("extracted_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_requirements_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_requirements")),
        sa.UniqueConstraint("job_id", "position", name="job_id_position"),
    )
    op.create_index(
        op.f("ix_job_requirements_job_id"), "job_requirements", ["job_id"], unique=False
    )
    op.create_table(
        "projects",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=200), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("repository_url", sa.String(length=500), nullable=True),
        sa.Column("skills_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("highlights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="project_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_projects_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_profile_id"), "projects", ["profile_id"], unique=False)
    op.create_table(
        "skills",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("canonical_name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=True),
        sa.Column("years", sa.Float(), nullable=True),
        sa.Column("has_evidence", sa.Boolean(), nullable=False),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="skill_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_skills_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("profile_id", "canonical_name", name="profile_id_canonical_name"),
    )
    op.create_index(op.f("ix_skills_canonical_name"), "skills", ["canonical_name"], unique=False)
    op.create_index(op.f("ix_skills_profile_id"), "skills", ["profile_id"], unique=False)
    op.create_table(
        "agent_runs",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("retry_count >= 0", name=op.f("ck_agent_runs_retry_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_agent_runs_application_id_applications"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_agent_runs_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_agent_runs_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(
        op.f("ix_agent_runs_application_id"), "agent_runs", ["application_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_runs_correlation_id"), "agent_runs", ["correlation_id"], unique=False
    )
    op.create_index(op.f("ix_agent_runs_kind"), "agent_runs", ["kind"], unique=False)
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"], unique=False)
    op.create_index(op.f("ix_agent_runs_user_id"), "agent_runs", ["user_id"], unique=False)
    op.create_index(
        "ix_agent_runs_user_started",
        "agent_runs",
        ["user_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_table(
        "application_answers",
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("question_key", sa.String(length=300), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column(
            "provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="answer_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_generated", sa.Boolean(), nullable=False),
        sa.Column(
            "sensitive_category",
            sa.Enum(
                "WORK_AUTHORISATION",
                "VISA_SPONSORSHIP",
                "SALARY_HISTORY",
                "SALARY_EXPECTATION",
                "DEMOGRAPHIC",
                "DISABILITY",
                "VETERAN_STATUS",
                "CRIMINAL_HISTORY",
                "SECURITY_CLEARANCE",
                "REFERENCES",
                "DATE_OF_BIRTH",
                "GOVERNMENT_ID",
                name="answer_sensitive_category",
                native_enum=False,
                length=48,
            ),
            nullable=True,
        ),
        sa.Column("needs_human", sa.Boolean(), nullable=False),
        sa.Column("user_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_application_answers_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_answers")),
        sa.UniqueConstraint("application_id", "question_key", name="application_id_question_key"),
    )
    op.create_index(
        op.f("ix_application_answers_application_id"),
        "application_answers",
        ["application_id"],
        unique=False,
    )
    op.create_table(
        "application_events",
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column(
            "from_state",
            sa.Enum(
                "DISCOVERED",
                "MATCHED",
                "SELECTED",
                "PREPARING",
                "READY",
                "FILLING",
                "VERIFYING",
                "WAITING_FOR_USER_INPUT",
                "WAITING_FOR_APPROVAL",
                "SUBMITTING",
                "SUBMITTED",
                "SUBMITTED_UNCERTAIN",
                "FAILED",
                "CANCELLED",
                name="event_from_state",
                native_enum=False,
                length=48,
            ),
            nullable=True,
        ),
        sa.Column(
            "to_state",
            sa.Enum(
                "DISCOVERED",
                "MATCHED",
                "SELECTED",
                "PREPARING",
                "READY",
                "FILLING",
                "VERIFYING",
                "WAITING_FOR_USER_INPUT",
                "WAITING_FOR_APPROVAL",
                "SUBMITTING",
                "SUBMITTED",
                "SUBMITTED_UNCERTAIN",
                "FAILED",
                "CANCELLED",
                name="event_to_state",
                native_enum=False,
                length=48,
            ),
            nullable=True,
        ),
        sa.Column(
            "intervention_reason",
            sa.Enum(
                "NEEDS_LOGIN",
                "NEEDS_CAPTCHA",
                "NEEDS_MFA",
                "NEEDS_HUMAN",
                "UNKNOWN_FIELD",
                "VERIFICATION_FAILED",
                "JOB_CLOSED",
                "UNSUPPORTED_FORM",
                name="event_intervention_reason",
                native_enum=False,
                length=48,
            ),
            nullable=True,
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_application_events_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_events")),
    )
    op.create_index(
        op.f("ix_application_events_application_id"),
        "application_events",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_events_correlation_id"),
        "application_events",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_events_event_type"), "application_events", ["event_type"], unique=False
    )
    op.create_index(
        op.f("ix_application_events_occurred_at"),
        "application_events",
        ["occurred_at"],
        unique=False,
    )
    op.create_table(
        "application_fields",
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("field_key", sa.String(length=300), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=True),
        sa.Column("field_type", sa.String(length=40), nullable=False),
        sa.Column("selector", sa.String(length=500), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column(
            "resolution",
            sa.Enum(
                "RESOLVED",
                "NEEDS_HUMAN",
                "UNKNOWN_FIELD",
                "SKIPPED",
                name="field_resolution",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "sensitivity",
            sa.Enum(
                "PUBLIC",
                "MASKED",
                "NEVER_STORED",
                name="field_sensitivity",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("value_masked", sa.Text(), nullable=True),
        sa.Column("value_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "value_provenance",
            sa.Enum(
                "USER_CONFIRMED_PROFILE",
                "RESUME_EXTRACTION_CONFIRMED",
                "USER_APPROVED_EVIDENCE",
                "PREVIOUSLY_APPROVED_ANSWER",
                "USER_DIRECT_INPUT",
                "RESUME_EXTRACTION_UNCONFIRMED",
                "GENERATED_UNVERIFIED",
                "UNKNOWN",
                name="field_value_provenance",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "sensitive_category",
            sa.Enum(
                "WORK_AUTHORISATION",
                "VISA_SPONSORSHIP",
                "SALARY_HISTORY",
                "SALARY_EXPECTATION",
                "DEMOGRAPHIC",
                "DISABILITY",
                "VETERAN_STATUS",
                "CRIMINAL_HISTORY",
                "SECURITY_CLEARANCE",
                "REFERENCES",
                "DATE_OF_BIRTH",
                "GOVERNMENT_ID",
                name="field_sensitive_category",
                native_enum=False,
                length=48,
            ),
            nullable=True,
        ),
        sa.Column("action_result", sa.String(length=40), nullable=True),
        sa.Column("action_error", sa.Text(), nullable=True),
        sa.Column(
            "verification",
            sa.Enum(
                "PASSED",
                "FAILED",
                "INCONCLUSIVE",
                "NOT_ATTEMPTED",
                name="field_verification",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_human", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_application_fields_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_fields")),
        sa.UniqueConstraint("application_id", "field_key", name="application_id_field_key"),
    )
    op.create_index(
        op.f("ix_application_fields_application_id"),
        "application_fields",
        ["application_id"],
        unique=False,
    )
    op.create_table(
        "approval_records",
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("approved_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("form_state_hash", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("request_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_approval_records_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name=op.f("fk_approval_records_approved_by_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_records")),
    )
    op.create_index(
        "ix_approval_active", "approval_records", ["application_id", "revoked_at"], unique=False
    )
    op.create_index(
        op.f("ix_approval_records_application_id"),
        "approval_records",
        ["application_id"],
        unique=False,
    )
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_notifications_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_notifications_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("user_id", "dedupe_key", name="user_id_dedupe_key"),
    )
    op.create_index(
        op.f("ix_notifications_application_id"), "notifications", ["application_id"], unique=False
    )
    op.create_index(op.f("ix_notifications_job_id"), "notifications", ["job_id"], unique=False)
    op.create_index(op.f("ix_notifications_kind"), "notifications", ["kind"], unique=False)
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)
    op.create_index(
        "ix_notifications_user_unread", "notifications", ["user_id", "read_at"], unique=False
    )
    op.create_table(
        "tool_executions",
        sa.Column("agent_run_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("arguments_redacted", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_redacted", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_executions_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_executions")),
        sa.UniqueConstraint("agent_run_id", "sequence", name="agent_run_id_sequence"),
    )
    op.create_index(
        op.f("ix_tool_executions_agent_run_id"), "tool_executions", ["agent_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_tool_executions_tool_name"), "tool_executions", ["tool_name"], unique=False
    )


def downgrade() -> None:
    # The vector extension is deliberately not dropped: it is cluster-wide and another
    # database may depend on it.
    op.drop_index(op.f("ix_tool_executions_tool_name"), table_name="tool_executions")
    op.drop_index(op.f("ix_tool_executions_agent_run_id"), table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_kind"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_job_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_application_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_approval_records_application_id"), table_name="approval_records")
    op.drop_index("ix_approval_active", table_name="approval_records")
    op.drop_table("approval_records")
    op.drop_index(op.f("ix_application_fields_application_id"), table_name="application_fields")
    op.drop_table("application_fields")
    op.drop_index(op.f("ix_application_events_occurred_at"), table_name="application_events")
    op.drop_index(op.f("ix_application_events_event_type"), table_name="application_events")
    op.drop_index(op.f("ix_application_events_correlation_id"), table_name="application_events")
    op.drop_index(op.f("ix_application_events_application_id"), table_name="application_events")
    op.drop_table("application_events")
    op.drop_index(op.f("ix_application_answers_application_id"), table_name="application_answers")
    op.drop_table("application_answers")
    op.drop_index("ix_agent_runs_user_started", table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_user_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_kind"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_correlation_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_application_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_skills_profile_id"), table_name="skills")
    op.drop_index(op.f("ix_skills_canonical_name"), table_name="skills")
    op.drop_table("skills")
    op.drop_index(op.f("ix_projects_profile_id"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_job_requirements_job_id"), table_name="job_requirements")
    op.drop_table("job_requirements")
    op.drop_index("ix_match_user_score", table_name="job_match_assessments")
    op.drop_index(op.f("ix_job_match_assessments_user_id"), table_name="job_match_assessments")
    op.drop_index(op.f("ix_job_match_assessments_job_id"), table_name="job_match_assessments")
    op.drop_table("job_match_assessments")
    op.drop_index(op.f("ix_experience_records_profile_id"), table_name="experience_records")
    op.drop_table("experience_records")
    op.drop_index(op.f("ix_education_records_profile_id"), table_name="education_records")
    op.drop_table("education_records")
    op.drop_index(op.f("ix_certifications_profile_id"), table_name="certifications")
    op.drop_table("certifications")
    op.drop_index(
        "uq_applications_live_user_job",
        table_name="applications",
        postgresql_where=sa.text("state NOT IN ('CANCELLED', 'FAILED')"),
    )
    op.drop_index("ix_applications_user_state", table_name="applications")
    op.drop_index(op.f("ix_applications_user_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_state"), table_name="applications")
    op.drop_index(op.f("ix_applications_job_id"), table_name="applications")
    op.drop_table("applications")
    op.drop_index(op.f("ix_resumes_user_id"), table_name="resumes")
    op.drop_index(op.f("ix_resumes_content_sha256"), table_name="resumes")
    op.drop_table("resumes")
    op.drop_index(op.f("ix_profiles_user_id"), table_name="profiles")
    op.drop_table("profiles")
    op.drop_index("ix_profile_evidence_user_source", table_name="profile_evidence")
    op.drop_index(op.f("ix_profile_evidence_user_id"), table_name="profile_evidence")
    op.drop_table("profile_evidence")
    op.drop_index(op.f("ix_jobs_last_observed_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_job_source_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_is_demo_data"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_fingerprint"), table_name="jobs")
    op.drop_index("ix_jobs_feed", table_name="jobs")
    op.drop_index(op.f("ix_jobs_company_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_canonical_title"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_job_sources_kind"), table_name="job_sources")
    op.drop_table("job_sources")
    op.drop_index(op.f("ix_companies_canonical_name"), table_name="companies")
    op.drop_table("companies")
