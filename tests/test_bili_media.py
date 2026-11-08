"""Local-only media integration: real ffmpeg, no QQ or Bili account calls."""
import asyncio
import base64
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bili_media", ROOT / "deploy/bili_activity/media.py")
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class FakeAPI:
    def __init__(self, fixture, *, subtitles=None, fail=None):
        self.fixture = fixture
        self.subtitles = subtitles or []
        self.downloads = []
        self.fail = fail

    async def identity(self, bvid, cid):
        if bvid != "BV1test12345" or cid != 321:
            raise ValueError("cid belongs to another video")
        return {"bvid": bvid, "aid": 123, "cid": cid, "duration": 61, "title": "local fixture", "page": 1}

    async def read(self, operation, args):
        if self.fail == "subtitles":
            raise TimeoutError()
        return {"subtitles": self.subtitles}

    async def request(self, path, params):
        return {"dash": {
            "video": [{"baseUrl": "https://test.bilivideo.com/video", "bandwidth": 200, "codecs": "avc1"}],
            "audio": [{"baseUrl": "https://test.bilivideo.com/audio", "bandwidth": 100}],
        }}

    async def download(self, url, path, limit):
        self.downloads.append((url, limit))
        if self.fail and url.endswith("/" + self.fail):
            path.write_bytes(b"partial")
            raise TimeoutError()
        shutil.copyfile(self.fixture, path)
        if path.stat().st_size > limit:
            raise ValueError("download limit")


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class MediaTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bili-media-tests-")
        cls.fixture = Path(cls.temp.name) / "fixture.mp4"
        subprocess.run([
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=red:s=96x64:r=2:d=61",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=61",
            "-vf", "drawbox=color=blue:t=fill:enable='gte(t,50)'",
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-c:a", "aac", str(cls.fixture),
        ], check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    async def test_real_frames_audio_time_range_and_compact_bundle(self):
        api = FakeAPI(self.fixture)
        result = await media.prepare_video(api, "BV1test12345", 321, 0, 100)
        self.assertEqual(result["identity"]["cid"], 321)
        self.assertEqual(result["covered_range"], {"start": 0, "end": 61})
        self.assertEqual(len(result["frames"]), 8)
        self.assertEqual(len(result["audio"]), 2)
        self.assertEqual(result["audio"][0]["start"], 0)
        # MP3 frame/encoder delay makes a requested 0-60s chunk probe as
        # ~59.94s.  The implementation must report the verified duration, not
        # the requested one; allow one encoder-frame worth of tolerance while
        # still catching real truncation.
        self.assertAlmostEqual(result["audio"][0]["end"], 60, delta=0.1)
        self.assertEqual(result["audio"][1]["start"], 60)
        self.assertEqual(result["audio"][1]["end"], 61)
        self.assertLess(len(json.dumps(result).encode()), media.MAX_RESPONSE_BYTES)
        self.assertEqual(len(api.downloads), 2)
        self.assertLess(api.downloads[1][1], api.downloads[0][1])
        for item in result["frames"]:
            self.assertLessEqual(len(base64.b64decode(item["data"])), media.MAX_FRAME_BYTES)
        with Image.open(io.BytesIO(base64.b64decode(result["frames"][-1]["data"]))) as picture:
            red, _, blue = picture.getpixel((picture.width // 2, picture.height // 2))
            self.assertGreater(blue, red + 100)
        self.assertNotIn(str(self.temp.name), json.dumps(result))
        self.assertNotIn("bilivideo.com", json.dumps(result))

    async def test_subtitles_skip_audio_and_filter_to_requested_part(self):
        api = FakeAPI(self.fixture, subtitles=[
            {"from": 0, "to": 2, "content": "before"},
            {"from": 10, "to": 12, "content": "included"},
            {"from": 25, "to": 30, "content": "after"},
        ])
        result = await media.prepare_video(api, "BV1test12345", 321, 10, 20)
        self.assertEqual([x["content"] for x in result["subtitles"]], ["included"])
        self.assertEqual(result["audio"], [])
        self.assertTrue(result["frames"])
        self.assertEqual(len(api.downloads), 1)
        self.assertEqual(result["frames"][0]["at"], 10)
        self.assertGreater(result["frames"][-1]["at"], 19)

    async def test_video_failure_retains_audio_and_subtitle_failure_falls_back(self):
        api = FakeAPI(self.fixture, fail="video")
        result = await media.prepare_video(api, "BV1test12345", 321, 0, 2)
        self.assertFalse(result["frames"])
        self.assertTrue(result["audio"])
        self.assertTrue(any("video download failed" in x for x in result["notes"]))
        api = FakeAPI(self.fixture, fail="subtitles")
        result = await media.prepare_video(api, "BV1test12345", 321, 0, 2)
        self.assertTrue(result["audio"])
        self.assertTrue(result["frames"])

    async def test_identity_mismatch_and_invalid_range_stop_before_download(self):
        api = FakeAPI(self.fixture)
        with self.assertRaises(ValueError):
            await media.prepare_video(api, "BV1test12345", 999)
        for start, end in ((-1, 3), (0, float("inf")), (2, 1), (70, 80)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                await media.prepare_video(api, "BV1test12345", 321, start, end)
        self.assertEqual(api.downloads, [])

    async def test_no_ffmpeg_still_retains_subtitles(self):
        api = FakeAPI(self.fixture, subtitles=[{"from": 0, "to": 2, "content": "known"}])
        with patch.object(media.shutil, "which", return_value=None):
            result = await media.prepare_video(api, "BV1test12345", 321, 0, 2)
        self.assertEqual(result["subtitles"][0]["content"], "known")
        self.assertEqual(api.downloads, [])

    async def test_cancellation_reaps_child_and_temporary_files(self):
        original_create = asyncio.create_subprocess_exec
        children = []
        folders = []
        original_temp = tempfile.TemporaryDirectory

        async def hanging_process(*args, **kwargs):
            proc = await original_create(
                sys.executable, "-c", "import time; time.sleep(30)",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            children.append(proc)
            return proc

        def remember_temp(*args, **kwargs):
            folder = original_temp(*args, **kwargs)
            folders.append(folder.name)
            return folder

        with patch.object(media.asyncio, "create_subprocess_exec", hanging_process), \
                patch.object(media.tempfile, "TemporaryDirectory", remember_temp):
            task = asyncio.create_task(media.prepare_video(FakeAPI(self.fixture), "BV1test12345", 321, 0, 2))
            for _ in range(100):
                if children:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(children)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        self.assertTrue(all(p.returncode is not None for p in children))
        self.assertTrue(all(not Path(folder).exists() for folder in folders))


class URLTests(unittest.TestCase):
    def test_rejects_non_cdn_and_cleartext_sources(self):
        for url in ("http://test.bilivideo.com/a", "https://bilivideo.com.evil.test/a",
                    "file:///tmp/a", "https://user:pass@test.bilivideo.com/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                media._cdn_url({"baseUrl": url})


if __name__ == "__main__":
    unittest.main(verbosity=2)
