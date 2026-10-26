import os
import tempfile
import time
import unittest
from unittest.mock import patch

TMP = tempfile.mkdtemp()
os.environ["API_KEY"] = "unit-test-key"
os.environ["DOWNLOAD_DIR"] = TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

H = {"X-API-Key": "unit-test-key"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def fake_cmd(settings, url, job_dir):
    code = (
        "import sys,pathlib;"
        f"pathlib.Path(r'{job_dir}').mkdir(parents=True,exist_ok=True);"
        f"pathlib.Path(r'{job_dir}','a.png').write_bytes({PNG!r})"
    )
    return [__import__("sys").executable, "-c", code]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(create_app())
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_auth(self):
        self.assertEqual(self.c.get("/").status_code, 401)
        self.assertEqual(self.c.get("/", headers={"X-API-Key": "bad"}).status_code, 401)
        self.assertEqual(self.c.get("/", headers=H).status_code, 200)

    def test_health_and_stats(self):
        self.assertEqual(self.c.get("/health").status_code, 200)
        self.assertEqual(self.c.get("/stats", headers=H).status_code, 200)

    def test_unsupported_url_rejected_without_job(self):
        r = self.c.post("/download", json={"url": "https://www.pinterest.com/"}, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "Unsupported URL")

    def test_bad_url_scheme(self):
        r = self.c.post("/download", json={"url": "ftp://x.com/a"}, headers=H)
        self.assertEqual(r.status_code, 400)

    def test_unknown_job(self):
        r = self.c.get("/status/00000000-0000-0000-0000-000000000000", headers=H)
        self.assertEqual(r.status_code, 404)

    def test_full_flow(self):
        with patch("app.downloader.build_gallery_dl_command", fake_cmd):
            r = self.c.post("/download", json={"url": "https://www.pinterest.com/a/b/"}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            job_id = r.json()["job_id"]
            for _ in range(50):
                st = self.c.get(f"/status/{job_id}", headers=H).json()
                if st["status"] in ("completed", "failed"):
                    break
                time.sleep(0.2)
        self.assertEqual(st["status"], "completed", st)
        self.assertEqual(len(st["files"]), 1)
        r = self.c.post(f"/job/{job_id}/complete", headers=H)
        self.assertEqual(r.status_code, 200)

    def _run_job(self, url="https://www.pinterest.com/a/b/", cmd=fake_cmd):
        with patch("app.downloader.build_gallery_dl_command", cmd):
            job_id = self.c.post("/download", json={"url": url}, headers=H).json()["job_id"]
            for _ in range(100):
                st = self.c.get(f"/status/{job_id}", headers=H).json()
                if st["status"] in ("completed", "failed"):
                    break
                time.sleep(0.2)
        return job_id, st

    def test_file_archive_delete(self):
        job_id, st = self._run_job()
        self.assertEqual(st["status"], "completed", st)
        r = self.c.get(f"/file/{st['files'][0]['id']}", headers=H)
        self.assertEqual((r.status_code, r.content), (200, PNG))
        r = self.c.get(f"/job/{job_id}/archive", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:2], b"PK")
        self.assertEqual(self.c.delete(f"/job/{job_id}", headers=H).status_code, 200)
        self.assertEqual(self.c.delete(f"/job/{job_id}", headers=H).status_code, 404)
        self.assertEqual(self.c.get(f"/file/{st['files'][0]['id']}", headers=H).status_code, 404)

    def test_failed_job_and_retry(self):
        def bad_cmd(settings, url, job_dir):
            return [__import__("sys").executable, "-c", "import sys;sys.exit(1)"]

        job_id, st = self._run_job(cmd=bad_cmd)
        self.assertEqual(st["status"], "failed", st)
        self.assertEqual(self.c.post(f"/job/{job_id}/retry", headers=H).status_code, 200)
        ok = self.c.post(f"/job/{job_id}/complete", headers=H)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.c.post(f"/job/{job_id}/retry", headers=H).status_code, 404)

    def test_retry_non_failed_rejected(self):
        job_id, st = self._run_job()
        self.assertEqual(self.c.post(f"/job/{job_id}/retry", headers=H).status_code, 400)

    def test_cleanup_loop_runs(self):
        import asyncio
        from app import cleanup

        async def go():
            with patch("app.cleanup.asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
                with self.assertRaises(asyncio.CancelledError):
                    await cleanup.cleanup_loop()

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
