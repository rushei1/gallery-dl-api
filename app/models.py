"""Pydantic request and response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DownloadRequest(BaseModel):
    """Request body for POST /download."""

    url: str = Field(..., min_length=1)


class DownloadResponse(BaseModel):
    """Response for POST /download."""

    success: bool = True
    job_id: str


class SuccessResponse(BaseModel):
    """Generic success response."""

    success: bool = True


class ErrorResponse(BaseModel):
    """Consistent error response."""

    success: bool = False
    error: str


class ServiceInfoResponse(BaseModel):
    """Response for GET /."""

    service: str = "gallery-api"
    status: str = "online"


class HealthResponse(BaseModel):
    """Response for GET /health."""

    status: str = "healthy"
    uptime_seconds: float = 0.0
    active_jobs: int = 0
    queued_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    storage_bytes: int = 0


class FileInfo(BaseModel):
    """Metadata for a downloaded file."""

    id: str
    filename: str
    size: int
    mime: str


class QueuedStatusResponse(BaseModel):
    """Job status: queued."""

    status: Literal["queued"] = "queued"
    queue_position: int | None = None


class DownloadingStatusResponse(BaseModel):
    """Job status: downloading."""

    status: Literal["downloading"] = "downloading"
    progress: int = Field(ge=0, le=100)


class CompletedStatusResponse(BaseModel):
    """Job status: completed."""

    status: Literal["completed"] = "completed"
    files: list[FileInfo]


class FailedStatusResponse(BaseModel):
    """Job status: failed."""

    status: Literal["failed"] = "failed"
    error: str


class StatsResponse(BaseModel):
    """Aggregate download statistics."""

    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    total_files: int = 0
    total_bytes: int = 0
    average_duration_seconds: float | None = None
