"""Job status, deletion, retry, and archive endpoints."""

from __future__ import annotations

import io
import logging
import zipfile
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.auth import verify_api_key
from app.config import get_settings
from app.downloader import get_download_manager
from app.job_cleanup import purge_job
from app.models import (
    CompletedStatusResponse,
    DownloadingStatusResponse,
    ErrorResponse,
    FailedStatusResponse,
    FileInfo,
    QueuedStatusResponse,
    SuccessResponse,
)
from app.storage.job_store import get_job_store
from app.utils import safe_file_path, safe_job_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

StatusResponse = Union[
    QueuedStatusResponse,
    DownloadingStatusResponse,
    CompletedStatusResponse,
    FailedStatusResponse,
]


@router.get(
    "/status/{job_id}",
    response_model=StatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_job_status(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
) -> StatusResponse:
    """Return the current status of a download job."""
    store = get_job_store()
    job = await store.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "Job not found"},
        )

    if job.status == "queued":
        position = await store.get_queue_position(job_id)
        return QueuedStatusResponse(status="queued", queue_position=position)

    if job.status == "downloading":
        return DownloadingStatusResponse(status="downloading", progress=job.progress)

    if job.status == "completed":
        return CompletedStatusResponse(
            status="completed",
            files=[
                FileInfo(
                    id=f.id,
                    filename=f.filename,
                    size=f.size,
                    mime=f.mime,
                )
                for f in job.files
            ],
        )

    return FailedStatusResponse(status="failed", error=job.error or "Unknown error")


@router.delete(
    "/job/{job_id}",
    response_model=SuccessResponse,
    responses={404: {"model": ErrorResponse}},
)
async def delete_job(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
) -> SuccessResponse:
    """Delete a job and its downloaded files."""
    if not await purge_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "Job not found"},
        )

    return SuccessResponse(success=True)


@router.post(
    "/job/{job_id}/complete",
    response_model=SuccessResponse,
    responses={404: {"model": ErrorResponse}},
)
async def complete_job(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
) -> SuccessResponse:
    """
    Mark a job as done and delete all temporary files.

    Call this from your Node.js bot after it has fetched and sent all files.
    """
    if not await purge_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "Job not found"},
        )

    return SuccessResponse(success=True)


@router.post(
    "/job/{job_id}/retry",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def retry_job(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
) -> SuccessResponse:
    """Retry a failed download job."""
    store = get_job_store()
    manager = get_download_manager()

    job = await store.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "Job not found"},
        )

    if job.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": "Only failed jobs can be retried"},
        )

    retried = await manager.retry_job(job_id)
    if not retried:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": "Unable to retry job"},
        )

    return SuccessResponse(success=True)


@router.get("/job/{job_id}/archive")
async def download_archive(
    job_id: str,
    _api_key: str = Depends(verify_api_key),
) -> StreamingResponse:
    """Download all files for a completed job as a ZIP archive."""
    store = get_job_store()
    settings = get_settings()

    job = await store.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": "Job not found"},
        )

    if job.status != "completed" or not job.files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": "Job has no files to archive"},
        )

    try:
        job_dir = safe_job_path(settings.download_path, job.job_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": str(exc)},
        ) from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_record in job.files:
            try:
                file_path = safe_file_path(job_dir, file_record.relative_path)
                zf.write(file_path, arcname=file_record.filename)
            except ValueError:
                continue

    buffer.seek(0)
    filename = f"job_{job_id}.zip"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
