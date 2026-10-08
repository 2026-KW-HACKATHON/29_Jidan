"""Substitute job postings, applications and work requests (docs/erd/jobs.md)."""
from fastapi import APIRouter

from app.jobs import applicants, applications, postings, search, work_requests

router = APIRouter()
router.include_router(postings.router)
router.include_router(search.router)
router.include_router(applications.router)
router.include_router(applicants.router)
router.include_router(work_requests.router)
