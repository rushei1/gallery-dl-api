"""Download endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_api_key
from app.config import get_settings
from app.downloader import get_download_manager
from app.models import DownloadRequest, DownloadResponse, ErrorResponse
from app.storage.job_store import get_job_store
from app.utils import is_supported_url, validate_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["download"])


@router.post(
    "/download",
    response_model=DownloadResponse,
    responses={400: {"model": ErrorResponse}},
)
async def create_download(
    body: DownloadRequest,
    _api_key: str = Depends(verify_api_key),
) -> DownloadResponse:
    """Create a background download job and return immediately."""
    settings = get_settings()
    store = get_job_store()
    manager = get_download_manager()

    try:
        url = validate_url(body.url, max_length=settings.max_url_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": str(exc)},
        ) from exc

    if not is_supported_url(url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": "Unsupported URL"},
        )

    job = await store.create_job(url)
    logger.info("Job created: %s for URL %s", job.job_id, url)

    await manager.enqueue(job)

    return DownloadResponse(success=True, job_id=job.job_id)
