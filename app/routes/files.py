"""File serving endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.auth import verify_api_key
from app.config import get_settings
from app.models import ErrorResponse
from app.storage.job_store import get_job_store
from app.utils import safe_file_path, safe_job_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


@router.get(
    "/file/{file_id}",
    responses={
        404: {"model": ErrorResponse},
    },
)
async def get_file(
    file_id: str,
    _api_key: str = Depends(verify_api_key),
) -> FileResponse:
    """Return a downloaded file by its ID."""
    store = get_job_store()
    settings = get_settings()

    result = await store.get_job_by_file_id(file_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "File not found"},
        )

    job, file_record = result

    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "File not available"},
        )

    try:
        job_dir = safe_job_path(settings.download_path, job.job_id)
        file_path = safe_file_path(job_dir, file_record.relative_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": str(exc)},
        ) from exc

    logger.info("Serving file %s for job %s", file_id, job.job_id)

    return FileResponse(
        path=file_path,
        media_type=file_record.mime,
        filename=file_record.filename,
    )
