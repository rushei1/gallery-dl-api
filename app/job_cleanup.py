"""Shared job purge logic for temporary file storage."""

from __future__ import annotations

import logging

from app.downloader import get_download_manager
from app.storage.job_store import get_job_store

logger = logging.getLogger(__name__)


async def purge_job(job_id: str) -> bool:
    """
    Delete a job's metadata and all files on disk.

    Returns False if the job did not exist.
    """
    store = get_job_store()
    manager = get_download_manager()

    job = await store.get_job(job_id)
    if not job:
        return False

    await manager.delete_job_files(job_id)
    await store.delete_job(job_id)
    logger.info("Job purged (temporary files removed): %s", job_id)
    return True
