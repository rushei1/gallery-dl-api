"""Pterodactyl / Python Generic egg entry point.

The generic egg startup runs: python /home/container/${PY_FILE}
Set PY_FILE to: run.py
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def _ensure_local_bin_on_path() -> None:
    """Add Pterodactyl pip --prefix .local bin directory to PATH."""
    home = Path(os.environ.get("HOME", "/home/container"))
    local_bin = home / ".local" / "bin"
    if local_bin.is_dir():
        current = os.environ.get("PATH", "")
        local_str = str(local_bin)
        if local_str not in current.split(":"):
            os.environ["PATH"] = f"{local_str}:{current}" if current else local_str


def main() -> None:
    """Start uvicorn using Pterodactyl SERVER_PORT when available."""
    _ensure_local_bin_on_path()
    port = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", "8000")))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
