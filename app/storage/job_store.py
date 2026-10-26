"""In-memory job storage with async-safe access."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import uuid4

JobStatus = Literal["queued", "downloading", "completed", "failed"]


@dataclass
class FileRecord:
    """Internal record for a downloaded file."""

    id: str
    filename: str
    relative_path: str
    size: int
    mime: str
    job_id: str


@dataclass
class JobRecord:
    """Internal record for a download job."""

    job_id: str
    url: str
    status: JobStatus
    created_at: datetime
    finished_at: datetime | None = None
    files: list[FileRecord] = field(default_factory=list)
    error: str | None = None
    progress: int = 0
    queue_position: int | None = None
    retry_count: int = 0


@dataclass
class JobStats:
    """Aggregate statistics for completed jobs."""

    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    total_files: int = 0
    total_bytes: int = 0
    total_duration_seconds: float = 0.0


class JobStoreProtocol(Protocol):
    """Protocol for job storage backends (SQLite-ready)."""

    async def create_job(self, url: str) -> JobRecord: ...

    async def get_job(self, job_id: str) -> JobRecord | None: ...

    async def get_job_by_file_id(self, file_id: str) -> tuple[JobRecord, FileRecord] | None: ...

    async def update_job(self, job: JobRecord) -> None: ...

    async def delete_job(self, job_id: str) -> bool: ...

    async def list_jobs_by_status(self, status: JobStatus) -> list[JobRecord]: ...

    async def get_expired_jobs(self, expiry_minutes: int) -> list[JobRecord]: ...

    async def get_queue_position(self, job_id: str) -> int | None: ...

    async def get_stats(self) -> JobStats: ...

    async def record_completion(self, job: JobRecord) -> None: ...


class JobStore:
    """Thread-safe in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._file_index: dict[str, tuple[str, FileRecord]] = {}
        self._stats = JobStats()
        self._lock = asyncio.Lock()

    async def create_job(self, url: str) -> JobRecord:
        """Create a new queued job."""
        async with self._lock:
            job_id = str(uuid4())
            queued_count = sum(1 for j in self._jobs.values() if j.status == "queued")
            job = JobRecord(
                job_id=job_id,
                url=url,
                status="queued",
                created_at=datetime.now(timezone.utc),
                queue_position=queued_count + 1,
            )
            self._jobs[job_id] = job
            self._stats.total_jobs += 1
            return job

    async def get_job(self, job_id: str) -> JobRecord | None:
        """Return a job by ID."""
        async with self._lock:
            return self._jobs.get(job_id)

    async def get_job_by_file_id(self, file_id: str) -> tuple[JobRecord, FileRecord] | None:
        """Return job and file record for a file ID."""
        async with self._lock:
            entry = self._file_index.get(file_id)
            if not entry:
                return None
            job_id, file_record = entry
            job = self._jobs.get(job_id)
            if not job:
                return None
            return job, file_record

    async def update_job(self, job: JobRecord) -> None:
        """Update an existing job."""
        async with self._lock:
            self._jobs[job.job_id] = job
            if job.status == "queued":
                self._refresh_queue_positions()

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job and its file index entries."""
        async with self._lock:
            job = self._jobs.pop(job_id, None)
            if not job:
                return False
            for file_record in job.files:
                self._file_index.pop(file_record.id, None)
            self._refresh_queue_positions()
            return True

    async def list_jobs_by_status(self, status: JobStatus) -> list[JobRecord]:
        """List all jobs with the given status."""
        async with self._lock:
            return [j for j in self._jobs.values() if j.status == status]

    async def get_expired_jobs(self, expiry_minutes: int) -> list[JobRecord]:
        """Return jobs older than expiry_minutes."""
        now = datetime.now(timezone.utc)
        async with self._lock:
            expired: list[JobRecord] = []
            for job in self._jobs.values():
                reference = job.finished_at or job.created_at
                age_minutes = (now - reference).total_seconds() / 60
                if age_minutes >= expiry_minutes:
                    expired.append(job)
            return expired

    async def get_queue_position(self, job_id: str) -> int | None:
        """Return queue position for a queued job."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "queued":
                return None
            queued = sorted(
                [j for j in self._jobs.values() if j.status == "queued"],
                key=lambda j: j.created_at,
            )
            for idx, j in enumerate(queued, start=1):
                if j.job_id == job_id:
                    return idx
            return None

    async def get_stats(self) -> JobStats:
        """Return aggregate statistics."""
        async with self._lock:
            return JobStats(
                total_jobs=self._stats.total_jobs,
                completed_jobs=self._stats.completed_jobs,
                failed_jobs=self._stats.failed_jobs,
                total_files=self._stats.total_files,
                total_bytes=self._stats.total_bytes,
                total_duration_seconds=self._stats.total_duration_seconds,
            )

    async def record_completion(self, job: JobRecord) -> None:
        """Record stats when a job finishes."""
        async with self._lock:
            if job.status == "completed":
                self._stats.completed_jobs += 1
                self._stats.total_files += len(job.files)
                self._stats.total_bytes += sum(f.size for f in job.files)
                if job.finished_at and job.created_at:
                    duration = (job.finished_at - job.created_at).total_seconds()
                    self._stats.total_duration_seconds += duration
            elif job.status == "failed":
                self._stats.failed_jobs += 1

    async def index_files(self, job: JobRecord) -> None:
        """Index file records for lookup by file ID."""
        async with self._lock:
            for file_record in job.files:
                self._file_index[file_record.id] = (job.job_id, file_record)

    def _refresh_queue_positions(self) -> None:
        """Update queue positions for all queued jobs."""
        queued = sorted(
            [j for j in self._jobs.values() if j.status == "queued"],
            key=lambda j: j.created_at,
        )
        for idx, job in enumerate(queued, start=1):
            job.queue_position = idx


_job_store: JobStore | None = None


def get_job_store() -> JobStore:
    """Return the singleton job store instance."""
    global _job_store
    if _job_store is None:
        _job_store = JobStore()
    return _job_store
