"""Background download execution via gallery-dl subprocess."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import Settings, get_settings
from app.storage.job_store import FileRecord, JobRecord, JobStore, get_job_store
from app.utils import (
    cookie_file_key_for_url,
    count_files_in_directory,
    detect_mime,
    resolve_gallery_dl_command,
    safe_job_path,
)

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "heic"})
GALLERY_IMAGE_FILTER = "extension in ('jpg', 'jpeg', 'png', 'webp', 'heic')"


def build_gallery_dl_command(settings: Settings, url: str, job_dir: Path) -> list[str]:
    """Build an image-only gallery-dl command for one isolated job directory."""
    cmd = resolve_gallery_dl_command() + ["-D", str(job_dir)]

    config_path = settings.resolve_config_path()
    if config_path:
        cmd.extend(["--config", str(config_path)])

    cookie_path = settings.cookie_file_for_url(url)
    if cookie_path:
        cmd.extend(["--cookies", str(cookie_path)])

    cmd.extend(["--filter", GALLERY_IMAGE_FILTER, url])
    return cmd


def is_allowed_image_output(path: Path, mime: str) -> bool:
    """Return whether a downloaded file satisfies the API's image-only contract."""
    extension = path.suffix.lower().lstrip(".")
    return extension in ALLOWED_IMAGE_EXTENSIONS and str(mime).lower().startswith("image/")


class DownloadManager:
    """Manages concurrent gallery-dl downloads with a semaphore."""

    def __init__(self, settings: Settings | None = None, store: JobStore | None = None) -> None:
        self._settings = settings or get_settings()
        self._store = store or get_job_store()
        self._semaphore = asyncio.Semaphore(self._settings.max_downloads)

    async def enqueue(self, job: JobRecord) -> None:
        """Start processing a job in the background."""
        asyncio.create_task(self._process_job(job.job_id))

    async def retry_job(self, job_id: str) -> JobRecord | None:
        """Re-queue a failed job for retry."""
        job = await self._store.get_job(job_id)
        if not job or job.status != "failed":
            return None

        job.status = "queued"
        job.error = None
        job.progress = 0
        job.finished_at = None
        job.files = []
        job.queue_position = await self._store.get_queue_position(job_id)
        await self._store.update_job(job)
        asyncio.create_task(self._process_job(job.job_id))
        logger.info("Job %s re-queued for retry (attempt %d)", job_id, job.retry_count)
        return job

    async def _process_job(self, job_id: str) -> None:
        """Acquire semaphore and run the download pipeline with optional retries."""
        while True:
            job = await self._store.get_job(job_id)
            if not job or job.status != "queued":
                return

            async with self._semaphore:
                job = await self._store.get_job(job_id)
                if not job or job.status != "queued":
                    return

                job.status = "downloading"
                job.queue_position = None
                job.progress = 0
                await self._store.update_job(job)
                logger.info("Download started for job %s: %s", job_id, job.url)

                success = await self._run_download(
                    job,
                    clear_instagram_cache=job.retry_count > 0,
                )

            job = await self._store.get_job(job_id)
            if not job:
                return

            if success:
                await self._store.record_completion(job)
                logger.info(
                    "Download completed for job %s (%d files)",
                    job_id,
                    len(job.files),
                )
                return

            # Unsupported URLs never succeed on retry.
            permanent = "Unsupported URL" in (job.error or "")
            if not permanent and job.retry_count < self._settings.max_retries:
                job.retry_count += 1
                job.status = "queued"
                job.error = None
                job.progress = 0
                job.finished_at = None
                await self._store.update_job(job)
                logger.info(
                    "Job %s scheduled for retry %d/%d",
                    job_id,
                    job.retry_count,
                    self._settings.max_retries,
                )
                continue

            await self._store.record_completion(job)
            logger.error("Download failed for job %s: %s", job_id, job.error)
            return

    async def _run_download(self, job: JobRecord, clear_instagram_cache: bool = False) -> bool:
        """Execute gallery-dl subprocess for a job. Returns True on success."""
        settings = self._settings
        job_dir = safe_job_path(settings.download_path, job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        if clear_instagram_cache and cookie_file_key_for_url(job.url) == "instagram":
            await self._clear_instagram_cache()

        cmd = build_gallery_dl_command(settings, job.url, job_dir)
        cookie_path = settings.cookie_file_for_url(job.url)
        if cookie_path:
            logger.info("Using cookies for job %s: %s", job.job_id, cookie_path.name)
        else:
            logger.debug("No cookie file matched for job %s URL", job.job_id)

        monitor_task: asyncio.Task[None] | None = None
        proc: asyncio.subprocess.Process | None = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            monitor_task = asyncio.create_task(
                self._monitor_progress(job.job_id, job_dir, proc)
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=settings.download_timeout_seconds,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                await self._fail_job(
                    job.job_id,
                    f"Download timed out after {settings.download_timeout_seconds // 60} minutes",
                )
                return False

            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                error_msg = stderr_text or f"gallery-dl exited with code {proc.returncode}"
                if len(error_msg) > 500:
                    error_msg = error_msg[:500] + "..."
                logger.error(
                    "gallery-dl failed for job %s (code %s): %s",
                    job.job_id,
                    proc.returncode,
                    error_msg,
                )
                await self._fail_job(job.job_id, self._friendly_error(stderr_text, job.url))
                return False

            files = await self._index_files(job.job_id, job_dir)
            if not files:
                await self._fail_job(job.job_id, "No files downloaded")
                return False

            job = await self._store.get_job(job.job_id)
            if job:
                job.status = "completed"
                job.progress = 100
                job.files = files
                job.finished_at = datetime.now(timezone.utc)
                job.error = None
                await self._store.update_job(job)
                await self._store.index_files(job)

            if stdout:
                logger.debug("gallery-dl stdout for %s: %s", job.job_id, stdout[:200])

            return True

        except FileNotFoundError:
            await self._fail_job(
                job.job_id,
                "gallery-dl executable not found. Ensure it is installed and on PATH.",
            )
            return False
        except Exception as exc:
            logger.exception("Unexpected error during download for job %s", job.job_id)
            await self._fail_job(job.job_id, str(exc))
            return False
        finally:
            if monitor_task:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

    async def _monitor_progress(
        self,
        job_id: str,
        job_dir: Path,
        proc: asyncio.subprocess.Process,
    ) -> None:
        """Poll job directory for download progress while subprocess runs."""
        last_progress = 0

        while proc.returncode is None:
            job = await self._store.get_job(job_id)
            if not job or job.status != "downloading":
                break

            file_count = count_files_in_directory(job_dir)
            progress = last_progress
            if file_count > 0:
                progress = min(90, max(progress, file_count * 5))

            if progress != job.progress:
                job.progress = progress
                last_progress = progress
                await self._store.update_job(job)

            await asyncio.sleep(2.0)

    async def _clear_instagram_cache(self) -> None:
        """Clear gallery-dl's cached Instagram session before a retry."""
        settings = self._settings
        cmd = resolve_gallery_dl_command() + ["--clear-cache", "instagram"]
        config_path = settings.resolve_config_path()
        if config_path:
            cmd.extend(["--config", str(config_path)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            logger.info("Cleared gallery-dl Instagram cache before retry")
        except Exception:
            logger.warning("Failed to clear gallery-dl Instagram cache", exc_info=True)

    def _friendly_error(self, stderr_text: str, url: str) -> str:
        """Map gallery-dl stderr to a clearer client-facing message."""
        if "redirect to home page" in stderr_text or "redirect to login page" in stderr_text:
            if cookie_file_key_for_url(url) == "instagram":
                return (
                    "Instagram rejected the session. Refresh insta-cookies.txt from a "
                    "logged-in browser (export Instagram-only cookies). Hosting IPs are "
                    "often blocked by Instagram even with valid cookies."
                )
            return (
                "Site redirected to login/home. Refresh cookies for this site and try again."
            )

        error_msg = stderr_text or "gallery-dl download failed"
        if len(error_msg) > 500:
            error_msg = error_msg[:500] + "..."
        return error_msg

    async def _index_files(self, job_id: str, job_dir: Path) -> list[FileRecord]:
        """Walk job directory and build file records."""
        files: list[FileRecord] = []
        if not job_dir.exists():
            return files

        for path in sorted(job_dir.rglob("*")):
            if not path.is_file():
                continue
            mime = detect_mime(path)
            if not is_allowed_image_output(path, mime):
                logger.warning(
                    "Discarding non-image gallery output for job %s: %s (%s)",
                    job_id,
                    path.name,
                    mime,
                )
                try:
                    path.unlink()
                except OSError:
                    logger.warning("Failed to remove rejected gallery output: %s", path)
                continue
            relative = str(path.relative_to(job_dir)).replace("\\", "/")
            files.append(
                FileRecord(
                    id=str(uuid4()),
                    filename=path.name,
                    relative_path=relative,
                    size=path.stat().st_size,
                    mime=mime,
                    job_id=job_id,
                )
            )
        return files

    async def _fail_job(self, job_id: str, error: str) -> None:
        """Mark a job as failed."""
        job = await self._store.get_job(job_id)
        if job:
            job.status = "failed"
            job.error = error
            job.finished_at = datetime.now(timezone.utc)
            await self._store.update_job(job)

    async def delete_job_files(self, job_id: str) -> None:
        """Remove downloaded files for a job."""
        try:
            job_dir = safe_job_path(self._settings.download_path, job_id)
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
        except ValueError:
            pass


_download_manager: DownloadManager | None = None


def get_download_manager() -> DownloadManager:
    """Return the singleton download manager."""
    global _download_manager
    if _download_manager is None:
        _download_manager = DownloadManager()
    return _download_manager
