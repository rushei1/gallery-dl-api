"""Shared utility helpers for validation, paths, and MIME detection."""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "video/webm"),  # needs WEBM check below
    (b"\x1a\x45\xdf\xa3", "video/webm"),
    (b"\x00\x00\x00", "video/mp4"),  # ftyp often at offset 4
    (b"PK\x03\x04", "application/zip"),
    (b"%PDF", "application/pdf"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"\xff\xf3", "audio/mpeg"),
    (b"\xff\xf2", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
    (b"\x00\x00\x01\xba", "video/mpeg"),
    (b"\x00\x00\x01\xb3", "video/mpeg"),
]


def is_supported_url(url: str) -> bool:
    """Return whether gallery-dl has an extractor that matches the URL."""
    from gallery_dl import extractor

    return extractor.find(url) is not None


def validate_url(url: str, max_length: int = 2048) -> str:
    """
    Validate a download URL.

    Raises ValueError if the URL is invalid.
    """
    if not url or not url.strip():
        raise ValueError("URL is required")

    url = url.strip()

    if len(url) > max_length:
        raise ValueError(f"URL exceeds maximum length of {max_length}")

    if any(ord(c) < 32 for c in url):
        raise ValueError("URL contains invalid control characters")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")

    if not parsed.netloc:
        raise ValueError("URL must have a valid host")

    return url


def cookie_file_key_for_url(url: str) -> str | None:
    """
    Return a cookie profile key for a URL host.

    Used to select site-specific cookie files (instagram, youtube).
    """
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host in ("instagram.com", "instagr.am") or host.endswith(".instagram.com"):
        return "instagram"

    if host in ("youtube.com", "youtu.be", "music.youtube.com") or host.endswith(
        ".youtube.com"
    ):
        return "youtube"

    return None


def resolve_cookie_file_for_url(url: str) -> Path | None:
    """Resolve the cookie file path for a download URL from settings."""
    from app.config import get_settings

    return get_settings().cookie_file_for_url(url)


def is_valid_uuid(value: str) -> bool:
    """Return True if value is a valid UUID string."""
    return bool(UUID_PATTERN.match(value))


def safe_job_path(download_dir: Path, job_id: str) -> Path:
    """
    Build a safe job directory path.

    Raises ValueError if job_id is not a valid UUID.
    """
    if not is_valid_uuid(job_id):
        raise ValueError("Invalid job ID")

    job_dir = (download_dir / job_id).resolve()
    download_resolved = download_dir.resolve()

    if not job_dir.is_relative_to(download_resolved):
        raise ValueError("Invalid job path")

    return job_dir


def safe_file_path(job_dir: Path, relative_path: str) -> Path:
    """
    Resolve a file path within a job directory safely.

    Raises ValueError on path traversal attempts.
    """
    if not relative_path or relative_path.startswith("/"):
        raise ValueError("Invalid file path")

    if ".." in Path(relative_path).parts:
        raise ValueError("Path traversal is not allowed")

    resolved = (job_dir / relative_path).resolve()
    job_resolved = job_dir.resolve()

    if not resolved.is_relative_to(job_resolved):
        raise ValueError("Path traversal is not allowed")

    if not resolved.is_file():
        raise ValueError("File not found")

    return resolved


def detect_mime(path: Path) -> str:
    """Detect MIME type using extension and magic-byte sniffing."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime

    try:
        with path.open("rb") as f:
            header = f.read(16)
    except OSError:
        return "application/octet-stream"

    if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBM":
        return "video/webm"

    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"

    for signature, mime_type in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            return mime_type

    return "application/octet-stream"


def get_directory_size(path: Path) -> int:
    """Return total byte size of all files under a directory."""
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def count_files_in_directory(path: Path) -> int:
    """Count regular files recursively under a directory."""
    if not path.exists():
        return 0
    count = 0
    for root, _dirs, files in os.walk(path):
        count += len(files)
    return count


def resolve_gallery_dl_command() -> list[str]:
    """
    Build the command prefix to invoke gallery-dl.

    Pterodactyl's Python egg installs pip packages to ~/.local/bin which is
    often not on PATH when uvicorn runs. Falls back to ``python -m gallery_dl``.
    """
    custom = os.environ.get("GALLERY_DL_PATH")
    if custom:
        return [custom]

    home = Path(os.environ.get("HOME", "/home/container"))
    local_bin = home / ".local" / "bin" / "gallery-dl"
    if local_bin.is_file():
        return [str(local_bin)]

    found = shutil.which("gallery-dl")
    if found:
        return [found]

    return [sys.executable, "-m", "gallery_dl"]
