"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata``, which is what
Alembic autogenerate reads. A new model module must be imported here or its table will
be silently missing from migrations.
"""

from app.db.base import Base
from app.models.application import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationField,
    ApprovalRecordRow,
)
from app.models.job import (
    Company,
    Job,
    JobMatchAssessment,
    JobRequirement,
    JobSource,
)
from app.models.observability import AgentRun, Notification, ToolExecution
from app.models.user import (
    Certification,
    EducationRecord,
    ExperienceRecord,
    Profile,
    ProfileEvidence,
    Project,
    Resume,
    Skill,
    User,
)

__all__ = [
    "AgentRun",
    "Application",
    "ApplicationAnswer",
    "ApplicationEvent",
    "ApplicationField",
    "ApprovalRecordRow",
    "Base",
    "Certification",
    "Company",
    "EducationRecord",
    "ExperienceRecord",
    "Job",
    "JobMatchAssessment",
    "JobRequirement",
    "JobSource",
    "Notification",
    "Profile",
    "ProfileEvidence",
    "Project",
    "Resume",
    "Skill",
    "ToolExecution",
    "User",
]
