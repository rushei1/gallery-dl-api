import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.config import Settings
from app.downloader import (
    GALLERY_IMAGE_FILTER,
    DownloadManager,
    build_gallery_dl_command,
)


class DownloaderFilteringTests(unittest.IsolatedAsyncioTestCase):
    def make_settings(self, root: Path, config: Path, cookies: Path) -> Settings:
        return Settings(
            API_KEY="unit-test-key",
            DOWNLOAD_DIR=str(root),
            GALLERY_DL_CONFIG_FILE=str(config),
            GALLERY_DL_INSTAGRAM_COOKIE_FILE=str(cookies),
        )

    async def test_command_is_image_only_and_keeps_url_last(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "gallery-dl.conf"
            cookies = root / "cookies.txt"
            config.write_text("{}", encoding="utf-8")
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            settings = self.make_settings(root, config, cookies)
            job_dir = root / str(uuid4())
            url = "https://www.instagram.com/p/example/"

            with patch("app.downloader.resolve_gallery_dl_command", return_value=["gallery-dl"]):
                command = build_gallery_dl_command(settings, url, job_dir)

            self.assertEqual(command[-1], url)
            self.assertEqual(command[command.index("--filter") + 1], GALLERY_IMAGE_FILTER)
            self.assertLess(command.index("-D"), command.index("--config"))
            self.assertLess(command.index("--config"), command.index("--cookies"))
            self.assertLess(command.index("--cookies"), command.index("--filter"))

    async def test_index_discards_non_image_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "gallery-dl.conf"
            cookies = root / "cookies.txt"
            config.write_text("{}", encoding="utf-8")
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            settings = self.make_settings(root, config, cookies)
            manager = DownloadManager(settings=settings, store=object())
            job_id = str(uuid4())
            job_dir = root / job_id
            job_dir.mkdir()

            image_path = job_dir / "01.jpg"
            video_path = job_dir / "02.mp4"
            unknown_path = job_dir / "03.bin"
            image_path.write_bytes(b"\xff\xd8\xff\xe0image")
            video_path.write_bytes(b"\x00\x00\x00\x18ftypisomvideo")
            unknown_path.write_bytes(b"unknown")

            files = await manager._index_files(job_id, job_dir)

            self.assertEqual([file.filename for file in files], ["01.jpg"])
            self.assertTrue(image_path.exists())
            self.assertFalse(video_path.exists())
            self.assertFalse(unknown_path.exists())


if __name__ == "__main__":
    unittest.main()
