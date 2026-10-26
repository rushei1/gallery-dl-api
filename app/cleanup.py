"""Background cleanup of expired jobs and files."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.job_cleanup import purge_job
from app.storage.job_store import get_job_store

logger = logging.getLogger(__name__)


async def cleanup_loop() -> None:
    """Periodically purge expired jobs and their downloaded files."""
    settings = get_settings()
    store = get_job_store()

    while True:
        try:
            await asyncio.sleep(60)
            expired = await store.get_expired_jobs(settings.job_expiry_minutes)
            if not expired:
                continue

            for job in expired:
                await purge_job(job.job_id)
                logger.info("Cleanup: purged expired job %s", job.job_id)

            logger.info("Cleanup: removed %d expired job(s)", len(expired))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cleanup loop error")
