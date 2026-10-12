"""The v1 router.

Resumes are mounted here as a declared-but-unimplemented surface: upload and
extraction belong to Phase 3, and the endpoints return 501 rather than silently
missing from the API.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import applications, auth, jobs, observability, profile, resumes

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(jobs.router)
api_router.include_router(applications.router)
api_router.include_router(resumes.router)
api_router.include_router(observability.router)
