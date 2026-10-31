"""真实 ffmpeg + 本地 HTTP 回归；不连接 QQ、不调用付费模型。

运行：python tests/test_video_processing.py
需要 aiohttp（插件已有依赖）和 ffmpeg；其余仅用标准库。
"""
import asyncio
import base64
import importlib.util
import logging
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    class Star:
        def __init__(self, *args, **kwargs):
            pass

    class Filter:
        EventMessageType = types.SimpleNamespace(ALL="ALL")

        def __getattr__(self, name):
            return lambda *args, **kwargs: lambda fn: fn

    attributes = {
        "astrbot": {}, "astrbot.api": {"logger": logging.getLogger("video-test")},
        "astrbot.api.event": {"AstrMessageEvent": object, "filter": Filter()},
        "astrbot.api.star": {"Context": object, "Star": Star,
                             "register": lambda *args: lambda cls: cls},
        "astrbot.core": {}, "astrbot.core.message": {},
        "astrbot.core.message.components": {
            name: type(name, (), {}) for name in
            ("At", "Face", "Image", "Plain", "Record", "Reply", "Video")
        },
        "astrbot.core.star": {},
        "astrbot.core.star.session_llm_manager": {"SessionServiceManager": object},
    }
    modules = {}
    for name, attrs in attributes.items():
        modules[name] = types.ModuleType(name)
        vars(modules[name]).update(attrs)
    spec = importlib.util.spec_from_file_location(
        "video_test_plugin", ROOT / "plugin/astrbot_plugin_face_convert/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


mod = load_plugin()
logging.basicConfig(level=logging.CRITICAL)


def plugin(**config):
    instance = mod.FaceConvertPlugin(None)
    instance.cfg.update(config)
    instance.asr_key = "test-only"
    return instance


class Event:
    message_str = "这个视频讲了什么"

    def __init__(self):
        self.extra = {}

    def get_messages(self):
        return [mod.Video()]

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_extra(self, key):
        return self.extra.get(key)

    def get_sender_id(self):
        return "test"

    def get_group_id(self):
        return ""

    def is_private_chat(self):
        return False


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_cancellation_reap_child_with_full_stderr_pipe(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    "import sys,time; sys.stderr.write('x'*200000); sys.stderr.flush(); time.sleep(30)",
                    stderr=asyncio.subprocess.PIPE,
                )
                task = asyncio.create_task(mod.run_bounded(proc, 30 if cancel else 0.1))
                if cancel:
                    await asyncio.sleep(0.1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                else:
                    rc, _ = await asyncio.wait_for(task, 2)
                    self.assertEqual(rc, -1)
                self.assertIsNotNone(proc.returncode)

    async def test_whole_video_timeout_keeps_results_and_cleans_directory(self):
        bot = plugin(video_timeout_s=0.05)
        folders = []
        stopped = asyncio.Event()

        async def analyze(event, comp, folder, result):
            folders.append(folder)
            result.duration = result.covered = 300
            result.items.append((0, "00:00 画面", "已经识别到的开头"))
            Path(folder, "partial.wav").write_bytes(b"partial")
            try:
                await asyncio.sleep(30)
            finally:
                stopped.set()

        bot._analyze_video = analyze
        result = await bot._process_video(None, None)
        self.assertTrue(stopped.is_set())
        self.assertIn("已经识别到的开头", result.render())
        self.assertIn("处理超时", result.render())
        self.assertFalse(Path(folders[0]).exists())

    async def test_external_cancellation_propagates_and_cleans_directory(self):
        bot = plugin()
        entered = asyncio.Event()
        folders = []

        async def analyze(event, comp, folder, result):
            folders.append(folder)
            entered.set()
            await asyncio.sleep(30)

        bot._analyze_video = analyze
        task = asyncio.create_task(bot._process_video(None, None))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(Path(folders[0]).exists())

    async def test_busy_queue_returns_promptly_without_releasing_others_slot(self):
        bot = plugin(media_queue_timeout_s=0.02)
        bot._process_video = AsyncMock()
        await bot._media_sem.acquire()
        event = Event()
        await bot.inbound_face_to_text(event)
        self.assertIn("正在处理其他媒体", event.message_str)
        self.assertTrue(bot._media_sem.locked())
        bot._process_video.assert_not_called()
        bot._media_sem.release()

    async def test_partial_results_reach_chat_and_honest_reply_survives_scrubber(self):
        bot = plugin()
        result = mod.VideoAnalysis(300, 300, [(240, "04:00 声音", "最后一句话")], ["部分画面识别失败"])
        bot._process_video = AsyncMock(return_value=result)
        event = Event()
        await bot.inbound_face_to_text(event)
        self.assertIn("最后一句话", event.message_str)
        self.assertNotIn("看得很清楚", event.message_str)
        self.assertFalse(bot._media_sem.locked())
        response = types.SimpleNamespace(completion_text="后面的画面没看清。")
        await bot.scrub_drafts(event, response)
        self.assertEqual(response.completion_text, "后面的画面没看清。")


@unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg")
class MediaTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="video-test-")
        cls.videos = {}
        for duration, audio in ((180, True), (300, True), (330, True), (2, False)):
            path = Path(cls.folder.name, f"{duration}.mp4")
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "lavfi", "-i", f"color=red:s=96x64:r=2:d={duration}"]
            if audio:
                command += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={duration}"]
            command += ["-vf", "drawbox=color=blue:t=fill:enable='gte(t,240)'",
                        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1"]
            if audio:
                command += ["-c:a", "aac"]
            subprocess.run(command + [str(path)], check=True, capture_output=True, timeout=20)
            cls.videos[duration] = path

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    async def exercise(self, duration, fail_one_audio=False):
        bot = plugin()
        bot._ffmpeg_exe = lambda: shutil.which("ffmpeg")
        bot._resolve_video = AsyncMock(return_value=str(self.videos[duration]))
        images, audios, temp_paths = [], [], []
        active = peak = 0

        async def tick():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

        async def describe(frames):
            images.extend(at for at, _ in frames)
            temp_paths.extend(path for _, path in frames)
            self.assertLessEqual(len(frames), 4)
            # 解码真实末尾截图，确认来自蓝色结尾，而非重复开头红色截图。
            if frames[-1][0] > 290:
                pixel = subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-i", frames[-1][1],
                     "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                    check=True, capture_output=True, timeout=5,
                ).stdout
                self.assertGreater(pixel[2], pixel[0] + 100)
            await tick()
            return f"画面覆盖到 {mod.timestamp(frames[-1][0])}"

        async def transcribe(path):
            temp_paths.append(path)
            with wave.open(path) as stream:
                seconds = stream.getnframes() / stream.getframerate()
                self.assertEqual(stream.getframerate(), 16000)
                self.assertEqual(stream.getnchannels(), 1)
                self.assertLessEqual(seconds, 60)
                self.assertGreater(seconds, 0)
            start = float(Path(path).stem[2:])
            audios.append((start, seconds))
            await tick()
            if fail_one_audio and start == 60:
                raise TimeoutError("模拟一段 ASR 超时")
            return f"音频从 {mod.timestamp(start)} 开始"

        bot._vl_describe, bot._asr_file = describe, transcribe
        result = await bot._process_video(None, None)
        self.assertLessEqual(peak, 2)
        self.assertTrue(self.videos[duration].exists())
        self.assertTrue(all(not Path(path).exists() for path in temp_paths))
        return result, sorted(images), sorted(audios)

    async def test_three_and_five_minutes_cover_beginning_and_end(self):
        for duration in (180, 300):
            with self.subTest(duration=duration):
                result, images, audios = await self.exercise(duration)
                self.assertEqual(result.notes, [])
                self.assertEqual(len(images), 16)
                self.assertEqual(images[0], 0)
                self.assertGreaterEqual(images[-1], duration - 1)
                self.assertEqual([start for start, _ in audios], list(range(0, duration, 60)))
                self.assertAlmostEqual(sum(length for _, length in audios), duration, delta=0.1)
                self.assertIn(f"{mod.timestamp(duration - 60)}–{mod.timestamp(duration)} 声音", result.render())

    async def test_longer_video_is_explicitly_truncated_at_five_minutes(self):
        result, images, audios = await self.exercise(330)
        self.assertEqual(result.covered, 300)
        self.assertLess(max(images), 300)
        self.assertEqual(len(audios), 5)
        self.assertIn("后续内容未处理", result.render())

    async def test_short_silent_video_still_returns_picture(self):
        result, images, audios = await self.exercise(2)
        self.assertEqual(len(images), 1)
        self.assertEqual(audios, [])
        self.assertTrue(result.items)
        self.assertIn("没有音轨", result.render())

    async def test_single_asr_failure_does_not_lose_later_or_picture_results(self):
        result, images, audios = await self.exercise(300, fail_one_audio=True)
        self.assertEqual(len(images), 16)
        self.assertEqual(len(audios), 5)
        self.assertIn("04:00–05:00 声音", result.render())
        self.assertIn("其他结果保留", result.render())

    async def test_local_oversized_video_is_rejected_before_ffmpeg(self):
        bot = plugin(max_media_mb=1)
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder, "large.mp4")
            with video.open("wb") as stream:
                stream.truncate(1024 * 1024 + 1)
            bot._resolve_video = AsyncMock(return_value=str(video))
            bot._probe_video = AsyncMock()
            result = await bot._process_video(None, None)
            self.assertIn("超过大小限制", result.render())
            bot._probe_video.assert_not_called()
            self.assertTrue(video.exists())


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = web.Application()
        self.received = []
        self.started = asyncio.Event()
        self.delay_model = False

        async def model(request):
            self.received.append(await request.json())
            self.assertEqual(request.headers["Authorization"], "Bearer test-only")
            self.started.set()
            if self.delay_model:
                await asyncio.sleep(0.2)
            return web.json_response({"output": {"choices": [{"message": {"content": [
                {"text": "第一段"}, {"text": "第二段"}
            ]}}]}})

        async def download(request):
            mode = request.match_info["mode"]
            if mode == "html":
                return web.Response(body=b"<html>" + b"x" * 2000)
            if mode == "length":
                return web.Response(body=b"x" * (1024 * 1024 + 1))
            response = web.StreamResponse()
            await response.prepare(request)
            self.started.set()
            try:
                for _ in range(18 if mode == "over" else 2):
                    await response.write(b"x" * 65536)
                    await asyncio.sleep(0.1 if mode == "slow" else 0)
            except ConnectionResetError:
                pass
            return response

        self.app.router.add_post("/services/aigc/multimodal-generation/generation", model)
        self.app.router.add_get("/{mode}", download)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_async_api_payload_and_all_text_blocks(self):
        bot = plugin(asr_timeout_s=3)
        sdk = types.ModuleType("dashscope")
        sdk.base_http_api_url = self.url
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"dashscope": sdk}):
            path = Path(folder, "sample.wav")
            path.write_bytes(b"test audio bytes")
            result = await bot._asr_file(str(path))
        self.assertEqual(result, "第一段\n第二段")
        payload = self.received[0]
        self.assertEqual(payload["model"], "qwen3-asr-flash")
        audio = payload["input"]["messages"][0]["content"][0]["audio"]
        self.assertTrue(audio.startswith("data:audio/wav;base64,"))
        self.assertEqual(base64.b64decode(audio.split(",", 1)[1]), b"test audio bytes")

    async def test_model_timeout_and_outer_cancellation_return_promptly(self):
        bot = plugin()
        sdk = types.ModuleType("dashscope")
        sdk.base_http_api_url = self.url
        self.delay_model = True
        with patch.dict(sys.modules, {"dashscope": sdk}):
            with self.assertRaises(asyncio.TimeoutError):
                await bot._mm_call("test-model", [{"text": "test"}], timeout=0.02)
            self.started.clear()
            task = asyncio.create_task(bot._mm_call("test-model", [{"text": "test"}]))
            await self.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)

    async def test_streaming_download_limits_and_partial_cleanup(self):
        bot = plugin(max_media_mb=1)
        with tempfile.TemporaryDirectory() as folder:
            for mode in ("ok", "over", "length", "html"):
                with self.subTest(mode=mode):
                    result = await bot._download_ok(self.url + "/" + mode, folder)
                    if mode == "ok":
                        self.assertEqual(Path(result).stat().st_size, 2 * 65536)
                    else:
                        self.assertIsNone(result)
                        self.assertFalse(Path(folder, "in.mp4").exists())
            self.started.clear()
            task = asyncio.create_task(bot._download_ok(self.url + "/slow", folder))
            await self.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(Path(folder, "in.mp4").exists())


class VoiceTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    """语音转写优先级：本地 WAV -> 直链 -> NapCat get_record。"""

    async def test_local_wav_beats_napcat(self):
        inst = plugin()
        inst._asr_file = AsyncMock(return_value="本地")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF")
            path = f.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        rec = types.SimpleNamespace(
            path=path, file="bare.amr", url="http://example.com/a.amr"
        )
        bot = types.SimpleNamespace(get_record=AsyncMock())
        event = types.SimpleNamespace(bot=bot)

        self.assertEqual(await inst._transcribe_voice(event, rec), "本地")
        inst._asr_file.assert_awaited_once_with(path)
        bot.get_record.assert_not_awaited()

    async def test_url_download_beats_napcat(self):
        inst = plugin()
        inst._asr_file = AsyncMock(return_value="直链")
        rec = types.SimpleNamespace(
            path=None, file=None, url="http://example.com/a.wav"
        )
        bot = types.SimpleNamespace(get_record=AsyncMock())
        event = types.SimpleNamespace(bot=bot)

        async def fake_download(session, url, **kwargs):
            return b"RIFF"

        with patch.object(mod, "download_capped", fake_download):
            self.assertEqual(await inst._transcribe_voice(event, rec), "直链")
        bot.get_record.assert_not_awaited()
        self.assertTrue(inst._asr_file.await_args.args[0].endswith(".wav"))

    async def test_get_record_fallback_still_works(self):
        inst = plugin()
        inst._asr_file = AsyncMock(return_value="兜底")
        rec = types.SimpleNamespace(path=None, file="abc.amr", url=None)
        bot = types.SimpleNamespace(
            get_record=AsyncMock(return_value={"data": {"file": "/tmp/x.mp3"}})
        )
        event = types.SimpleNamespace(bot=bot)

        self.assertEqual(await inst._transcribe_voice(event, rec), "兜底")
        bot.get_record.assert_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
