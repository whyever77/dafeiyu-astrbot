"""把 LLM 回复中的 [表情名] 文字自动转换成真实 QQ 表情段。

原理：QQ 只在"人手输入"时把 [旺柴] 这类文字渲染成表情，
机器人发出的纯文本接收端只会看到字面量。本插件在消息发送前
把 [名字] 替换为真正的 face 消息段（NapCat / OneBot v11）。
字典来源：NapCat 内置 qface 表 + 社区 315 条 sysface 快照合并（414 条）。
"""

import asyncio
import base64
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import At, Face, Image, Plain, Record, Reply, Video
from astrbot.core.star.session_llm_manager import SessionServiceManager

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_REPLACE = 8  # 单条消息最多替换几个表情，防刷屏

# 中文标点归一：模型偶尔拿顿号当逗号、拿「」当引号。
# 只做低风险替换；代码块由调用方切出去，不在这里处理。
_PUNCT_CLAUSE_STARTERS = (
    "也|又|还|就|都|却|但|可是|不过|所以|因此|因为|然后|而且|并且|"
    "或者|或是|反而|其实|甚至|只是|才|再|已经|不会|不能|没|不|可|而"
)
_PUNCT_DUNHAO_CLAUSE_RE = re.compile(rf"、(?=(?:{_PUNCT_CLAUSE_STARTERS}))")
DEFAULT_CONFIG = {
    "hedge_eraser": True,
    # 听写/看图凭证与模型独立于 TTS provider：换音源、改 provider id 时
    # 不会连带把 ASR / 视频理解弄哑。留空则回退到旧版 dashscope_tts 凭证。
    "asr_api_key": "",
    "asr_model": "qwen3-asr-flash",
    "vl_model": "qwen3-vl-plus",
    "face_cooldown_minutes": 10,
    "max_media_mb": 80,
    "video_timeout_s": 300,
    "video_max_duration_s": 300,
    "video_max_frames": 16,
    "video_audio_chunk_s": 60,
    "video_workers": 2,
    "vl_timeout_s": 60,
    "asr_timeout_s": 60,
    "max_concurrent_media": 1,
    "media_queue_timeout_s": 10,
}


def timestamp(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{seconds:02d}"


def frame_times(duration: float, limit: int) -> list[float]:
    """均匀覆盖首尾；短视频约每四秒取一帧，长视频不超过 limit 帧。"""
    count = min(limit, max(1, math.ceil(duration / 4)))
    end = max(0.0, duration - min(0.5, duration / 2))
    return [end * i / (count - 1) for i in range(count)] if count > 1 else [0.0]


@dataclass
class VideoAnalysis:
    duration: float = 0
    covered: float = 0
    items: list[tuple[float, str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        notes = "；".join(dict.fromkeys(self.notes))
        if not self.items:
            return f"视频暂时未能识别（{notes or '没有可用结果'}），可以请对方描述或重发。"
        header = (
            f"视频总长 {timestamp(self.duration)}，分析范围 00:00–{timestamp(self.covered)}。"
            "画面来自间隔抽帧，不能推断未采样的动作或细节。"
        )
        if notes:
            header += f"注意：{notes}。仅依据已得到的内容回答，不要声称已完整看完。"
        lines = [f"{label}：{text}" for _, label, text in sorted(self.items)]
        return header + "\n" + "\n".join(lines)


def file_uri_to_path(u: str) -> str:
    """把 file:// URI 转成本机路径；http(s) 原样返回。

    兼容三类（Windows / Linux / 裸路径）：
      file:///C:/x/y  -> C:/x/y        （Windows：去掉前导斜杠）
      file:///tmp/y   -> /tmp/y        （Linux：保留开头的斜杠！）
      /tmp/y          -> /tmp/y
    """
    s = str(u).strip()
    if s.lower().startswith(("http://", "https://")):
        return s
    if s.lower().startswith("file://"):
        s = s[7:]  # 去掉 "file://"（三个斜杠时这里留下路径自带的那个 /）
        if re.match(r"^/[A-Za-z]:", s):  # Windows 的 /C:/... -> C:/...
            s = s[1:]
        return s
    return s


async def download_capped(
    session: "aiohttp.ClientSession", url: str, max_bytes: int, timeout_total: int
) -> bytes | None:
    """流式下载并限制大小；超限/超时/非 200 返回 None（调用方走降级分支）。"""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout_total)
        ) as r:
            if r.status != 200:
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in r.content.iter_chunked(65536):
                size += len(chunk)
                if size > max_bytes:
                    logger.warning(
                        "[face_convert] 媒体超过大小上限 %d 字节，已放弃下载", max_bytes
                    )
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except (asyncio.TimeoutError, OSError, aiohttp.ClientError):
        return None


async def run_bounded(
    proc: asyncio.subprocess.Process, timeout: float
) -> tuple[int, bytes]:
    """超时或外层取消都杀进程、排空管道并回收；外层取消继续传播。"""
    communication = asyncio.create_task(proc.communicate())
    try:
        _, err = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
        return proc.returncode or 0, err or b""
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await communication
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.warning("[face_convert] 子进程超时（%ss），已强制终止", timeout)
        return -1, b"timeout"


@register(
    "astrbot_plugin_face_convert",
    "dafeiyu-tools",
    "把 LLM 回复中的 [流泪][doge] 等文字表情自动转换为真实 QQ 表情段",
    "1.3.0",
)
class FaceConvertPlugin(Star):
    # 静默暗号只在“整条回复就是它”时生效；正常解释这个标记时不能吞整条。
    NO_REPLY_RE = re.compile(
        r"(?:\s*[\[【]\s*NO[_ -]?REPLY\s*[\]】]\s*)+", re.IGNORECASE
    )
    # 私聊里明确喊停时，取消上一轮还没发完的气泡/语音。
    INTERRUPT_RE = re.compile(
        r"^(?:等等|等一下|先别(?:说|发|回)?|停(?:一下)?|打住|别说了|不是这个意思)"
        r"[\s！!。.…？?~～]*$"
    )

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        map_path = os.path.join(PLUGIN_DIR, "face_map.json")
        with open(map_path, encoding="utf-8") as f:
            self.face_map: dict[str, int] = json.load(f)
        # 反向表：id -> 名字（用于把收到的表情翻译成文字喂给 LLM）
        self.id2name: dict[int, str] = {}
        for name, fid in self.face_map.items():
            self.id2name.setdefault(fid, name)
        # 手工优先级：通称覆盖
        self.id2name[277] = "旺柴"
        self.id2name[179] = "doge"
        self.pattern = re.compile(r"\[([^\[\]]{1,8})\]")
        # 轻量配置：同目录 config.json 可覆盖（缺省即默认值）
        self.cfg = DEFAULT_CONFIG.copy()
        try:
            with open(os.path.join(PLUGIN_DIR, "config.json"), encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.cfg.update({k: loaded[k] for k in loaded if k in self.cfg})
        except (OSError, ValueError):
            pass
        # 配置错误不能把队列锁死或产生无上限的媒体任务。
        for key, low, high in (
            ("max_media_mb", 1, 200), ("video_timeout_s", 10, 900),
            ("video_max_duration_s", 1, 300), ("video_max_frames", 1, 32),
            ("video_audio_chunk_s", 10, 60), ("video_workers", 1, 2),
            ("vl_timeout_s", 1, 120), ("asr_timeout_s", 1, 120),
            ("max_concurrent_media", 1, 4), ("media_queue_timeout_s", 1, 60),
        ):
            try:
                self.cfg[key] = min(high, max(low, int(self.cfg[key])))
            except (TypeError, ValueError, OverflowError):
                self.cfg[key] = DEFAULT_CONFIG[key]
        self._media_sem = asyncio.Semaphore(
            max(1, int(self.cfg.get("max_concurrent_media", 2)))
        )
        # 表情冷却：按会话记录她最近用过的脸 [(脸id, 时间戳)]（治"又发好多狗头"；
        # 早期版本是全局共享列表，会跨群串扰，已改为按会话隔离 + 时间过期）
        self._face_history: dict[str, list[tuple[int, float]]] = {}
        # 听写/看图凭证与管理员名单。凭证按「独立配置 -> 环境变量 ->
        # 旧版 dashscope_tts provider」顺序解析，不再绑定 TTS 的 id。
        self.asr_key = ""
        self.asr_model = str(self.cfg.get("asr_model") or "qwen3-asr-flash")
        self.vl_model = str(self.cfg.get("vl_model") or "qwen3-vl-plus")
        self.admins: list[str] = []
        key_source = "未配置"
        try:
            self.asr_key = str(self.cfg.get("asr_api_key") or "").strip()
            if self.asr_key:
                key_source = "config.json:asr_api_key"
            if not self.asr_key:
                self.asr_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
                if self.asr_key:
                    key_source = "环境变量 DASHSCOPE_API_KEY"

            cfg_path = os.path.abspath(
                os.path.join(PLUGIN_DIR, "..", "..", "cmd_config.json")
            )
            with open(cfg_path, encoding="utf-8-sig") as f:
                cfg = json.load(f)
            if not self.asr_key:
                # 兼容旧部署：只按 provider 类型找 DashScope，不再写死 id。
                for prov in cfg.get("provider", []):
                    if (
                        prov.get("type") in ("dashscope_tts", "dashscope")
                        or prov.get("provider") == "dashscope"
                    ) and prov.get("api_key"):
                        self.asr_key = prov["api_key"]
                        key_source = f"旧版 provider {prov.get('id', '?')}（建议改为 asr_api_key）"
                        logger.warning(
                            "[face_convert] ASR 仍在借用 TTS provider 的凭证；"
                            "请在 config.json 设置 asr_api_key 或环境变量 "
                            "DASHSCOPE_API_KEY 以彻底解耦。"
                        )
                        break
            self.admins = [str(a).strip() for a in (cfg.get("admins_id") or [])]
        except Exception as e:
            logger.warning(f"[face_convert] 读取 ASR 凭证失败: {e}")
        logger.info(
            f"[face_convert] 表情字典载入完成，共 {len(self.face_map)} 条；"
            f"语音转写{'已就绪' if self.asr_key else '未配置'}"
            f"（{key_source}，模型 {self.asr_model}/{self.vl_model}）"
        )

    async def _transcribe_voice(self, event: AstrMessageEvent, rec) -> str | None:
        """语音消息 -> 文字：NapCat 转 mp3 -> 百炼 qwen3-asr-flash"""
        if not self.asr_key:
            return None
        try:
            file_ref = getattr(rec, "file", None) or getattr(rec, "url", None) or getattr(rec, "path", None)
            if not file_ref:
                return None
            # 1) 让 NapCat 把语音转成 mp3（它自带 silk 解码）
            try:
                resp = await event.bot.get_record(file=file_ref, out_format="mp3")
            except Exception:
                resp = await event.bot.get_record(file=file_ref)
            mp3 = None
            if isinstance(resp, dict):
                mp3 = (resp.get("data") or {}).get("file") or resp.get("file")
            if not mp3:
                return None
            # 2) 解析到本地文件
            path = file_uri_to_path(mp3)
            tmp_owned = False  # 自己下载的临时文件，用完负责清理
            if path.startswith("http"):
                async with aiohttp.ClientSession() as sess:
                    body = await download_capped(
                        sess,
                        path,
                        max_bytes=int(self.cfg.get("max_media_mb", 20)) * 1024 * 1024,
                        timeout_total=30,
                    )
                if not body:
                    return None
                tmp = os.path.join(
                    tempfile.gettempdir(), f"asr_{int(time.time() * 1000)}.mp3"
                )
                with open(tmp, "wb") as f:
                    f.write(body)
                tmp_owned = True
            else:
                tmp = path
            # 3) 转写
            try:
                return await self._asr_file(tmp)
            finally:
                if tmp_owned:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"[face_convert] 语音转写失败: {str(e)[:150]}")
            return None

    @staticmethod
    def _extract_text(resp) -> str | None:
        """从 DashScope 多模态返回里抽出文本。"""
        try:
            def get(obj, key):
                return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

            out = get(resp, "output")
            choices = (get(out, "choices") or []) if out else []
            for ch in choices:
                msg = get(ch, "message")
                content = get(msg, "content") if msg else None
                if isinstance(content, str):
                    return content.strip() or None
                if isinstance(content, list):
                    texts = [str(item["text"]).strip() for item in content
                             if isinstance(item, dict) and item.get("text")]
                    if texts:
                        return "\n".join(texts)
        except Exception:
            pass
        return None

    async def _mm_call(self, model: str, content_items: list, timeout: int = 60) -> str | None:
        """可取消的 DashScope HTTP 请求，避免超时后 SDK 工作线程继续上传。"""
        if not self.asr_key:
            return None
        try:
            import dashscope
            base_url = dashscope.base_http_api_url
        except ImportError:
            base_url = "https://dashscope.aliyuncs.com/api/v1"
        payload = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content_items}]},
            "parameters": {"result_format": "message"},
        }
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True
        ) as session:
            async with session.post(
                base_url.rstrip("/") + "/services/aigc/multimodal-generation/generation",
                headers={"Authorization": f"Bearer {self.asr_key}"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return self._extract_text(await resp.json())

    @staticmethod
    def _media_data_url(path: str, mime: str) -> str:
        # 留出 base64 的 4/3 膨胀空间，编码后低于 ASR 的 10 MB 输入上限。
        with open(path, "rb") as f:
            data = f.read(7_000_001)
        if len(data) > 7_000_000:
            raise ValueError("单段媒体过大，请先切段或压缩")
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")

    async def _asr_file(self, path: str) -> str | None:
        """本地音频文件 -> 文字（qwen3-asr-flash）。"""
        return await self._mm_call(
            self.asr_model,
            [{"audio": self._media_data_url(
                path, "audio/wav" if path.lower().endswith(".wav") else "audio/mpeg"
            )}],
            timeout=self.cfg["asr_timeout_s"],
        )

    async def _vl_describe(self, frames: list[tuple[float, str]]) -> str | None:
        """每批至多四帧，带时间戳一起理解，保留场景变化。"""
        content = []
        for at, path in frames:
            content.extend([
                {"text": f"视频 {timestamp(at)} 的截图："},
                {"image": self._media_data_url(path, "image/jpeg")},
            ])
        content.append({"text": (
            "这些截图按视频时间排列。请按给定时间戳描述人物、动作、场景变化和关键文字，"
            "每帧不超过80字。只描述截图能确认的事实，不补写帧间剧情，不要客套。"
        )})
        return await self._mm_call(
            self.vl_model, content, timeout=self.cfg["vl_timeout_s"],
        )

    @staticmethod
    def _ffmpeg_exe() -> str | None:
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            import shutil

            return shutil.which("ffmpeg")

    async def _download_ok(self, url: str, tmpdir: str) -> str | None:
        """下载并做防伪检查（避免下到报错网页）。成功返回本地路径。"""
        vf = os.path.join(tmpdir, "in.mp4")
        complete = False
        try:
            limit = self.cfg["max_media_mb"] * 1024 * 1024
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as sess:
                async with sess.get(url) as resp:
                    if resp.status != 200 or (resp.content_length or 0) > limit:
                        return None
                    size = 0
                    with open(vf, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > limit:
                                return None
                            f.write(chunk)
            with open(vf, "rb") as f:
                head = f.read(12)
            if size < 1000 or head.lstrip().startswith(b"<"):
                return None
            complete = True
            return vf
        except Exception as e:
            logger.info(f"[face_convert] 下载失败: {str(e)[:80]}")
            return None
        finally:
            if not complete:
                try:
                    os.remove(vf)
                except OSError:
                    pass

    async def _resolve_video(self, event: AstrMessageEvent, comp, tmpdir: str) -> str | None:
        """把视频字段解析成本地文件（多候选兜底：url -> 路径 -> 凭单号找 NapCat 要）。"""
        file_f = getattr(comp, "file", None)
        url_f = getattr(comp, "url", None)
        path_f = getattr(comp, "path", None)
        logger.info(
            f"[face_convert] 视频字段: file={str(file_f)[:70]!r} "
            f"url={str(url_f)[:70]!r} path={str(path_f)[:70]!r}"
        )
        cands = []
        for f in (url_f, path_f, file_f):
            if f and str(f).strip() and str(f).strip() not in cands:
                cands.append(str(f).strip())
        for cand in cands:
            p = file_uri_to_path(cand)
            if p.startswith("http"):
                vf = await self._download_ok(p, tmpdir)
                if vf:
                    return vf
            elif os.path.exists(p):
                return p
            else:
                # 光杆文件名 -> 凭单号找 NapCat 提货
                try:
                    resp = await event.bot.get_file(file=cand)
                    d = resp.get("data") if isinstance(resp, dict) else None
                    rp = None
                    if isinstance(d, dict):
                        rp = d.get("file") or d.get("path") or d.get("url")
                    if not rp and isinstance(resp, dict):
                        rp = resp.get("file") or resp.get("url")
                    if rp:
                        rp = file_uri_to_path(rp)
                        logger.info(f"[face_convert] NapCat 提货: {rp[:90]}")
                        if rp.startswith("http"):
                            vf = await self._download_ok(rp, tmpdir)
                            if vf:
                                return vf
                        elif os.path.exists(rp):
                            return rp
                except Exception as e:
                    logger.info(f"[face_convert] get_file({cand[:40]}) 失败: {str(e)[:80]}")
        return None

    async def _run_ffmpeg(self, ff: str, *args: str, timeout: float = 60):
        proc = await asyncio.create_subprocess_exec(
            ff, "-hide_banner", "-nostdin", *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        return await run_bounded(proc, timeout)

    async def _probe_video(self, ff: str, video: str) -> tuple[float, bool]:
        # imageio-ffmpeg 只附带 ffmpeg，读头信息避免新增 ffprobe 部署要求。
        _, err = await self._run_ffmpeg(ff, "-i", video, timeout=15)
        info = err.decode("utf-8", "ignore")
        match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
        if not match:
            raise ValueError("无法读取视频时长")
        hours, minutes, seconds = map(float, match.groups())
        duration = hours * 3600 + minutes * 60 + seconds
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("视频时长无效")
        return duration, bool(re.search(r"Stream #[^\r\n]*Audio:", info))

    async def _analyze_video(self, event, comp, tmpdir: str, result: VideoAnalysis):
        video = await self._resolve_video(event, comp, tmpdir)
        if not video:
            result.notes.append("下载失败或文件超过大小限制")
            return
        # NapCat 共享的本地文件也执行大小检查，但只清理本插件创建的文件。
        if os.path.getsize(video) > self.cfg["max_media_mb"] * 1024 * 1024:
            result.notes.append("文件超过大小限制")
            return
        ff = self._ffmpeg_exe()
        if not ff:
            result.notes.append("服务器缺少 ffmpeg")
            return
        result.duration, has_audio = await self._probe_video(ff, video)
        result.covered = min(result.duration, self.cfg["video_max_duration_s"])
        if result.duration > result.covered:
            result.notes.append(f"只分析前 {timestamp(result.covered)}，后续内容未处理")
        if not has_audio:
            result.notes.append("视频没有音轨")

        times = frame_times(result.covered, self.cfg["video_max_frames"])
        # 帧按四张一批；声音按段，队列按视频时间交错，避免只处理画面或声音。
        jobs = [(batch[0], "画面", batch) for i in range(0, len(times), 4)
                if (batch := times[i:i + 4])]
        if has_audio:
            step = self.cfg["video_audio_chunk_s"]
            jobs.extend((float(start), "声音", [min(step, result.covered - start)])
                        for start in range(0, math.ceil(result.covered), step))
        queue = asyncio.Queue()
        for job in sorted(jobs):
            queue.put_nowait(job)

        async def worker():
            while not queue.empty():
                start, kind, payload = queue.get_nowait()
                label = f"{timestamp(start)} {kind}"
                try:
                    if kind == "画面":
                        frames = []
                        for at in payload:
                            path = os.path.join(tmpdir, f"f_{at:.3f}.jpg")
                            rc, _ = await self._run_ffmpeg(
                                ff, "-loglevel", "error", "-y", "-ss", str(at),
                                "-threads", "1", "-i", video, "-map", "0:v:0",
                                "-frames:v", "1", "-an", "-filter_threads", "1",
                                "-vf", "scale='min(960,iw)':'min(960,ih)':force_original_aspect_ratio=decrease",
                                "-q:v", "4", "-threads", "1", path,
                            )
                            if rc == 0 and os.path.exists(path) and os.path.getsize(path):
                                frames.append((at, path))
                            else:
                                result.notes.append(f"{timestamp(at)} 抽帧失败")
                        label = f"{timestamp(start)}–{timestamp(payload[-1])} 画面"
                        text = await self._vl_describe(frames) if frames else None
                    else:
                        length = payload[0]
                        label = f"{timestamp(start)}–{timestamp(start + length)} 声音"
                        path = os.path.join(tmpdir, f"a_{start:.3f}.wav")
                        rc, _ = await self._run_ffmpeg(
                            ff, "-loglevel", "error", "-y", "-ss", str(start),
                            "-threads", "1", "-i", video, "-map", "0:a:0", "-vn",
                            "-ac", "1", "-ar", "16000", "-t", str(length),
                            "-c:a", "pcm_s16le", path,
                        )
                        text = (await self._asr_file(path)
                                if rc == 0 and os.path.exists(path) and os.path.getsize(path) > 44
                                else None)
                    if text:
                        result.items.append((start, label, text))
                    else:
                        result.notes.append(f"{label}没有识别到内容")
                except Exception as e:
                    logger.warning(f"[face_convert] {label}失败: {type(e).__name__}")
                    result.notes.append(f"{label}识别失败，其他结果保留")
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.cfg["video_workers"])]
        try:
            await asyncio.gather(*workers)
        finally:
            # 收回所有任务之后才能删除临时文件，不能让 ffmpeg / HTTP 留在后台。
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _process_video(self, event: AstrMessageEvent, comp) -> VideoAnalysis:
        """最长五分钟，超时/单段失败仍返回已识别结果；外部取消继续传播。"""
        result = VideoAnalysis()
        if not self.asr_key:
            result.notes.append("未配置 DashScope key")
            return result
        with tempfile.TemporaryDirectory(prefix="vid_") as tmpdir:
            try:
                await asyncio.wait_for(
                    self._analyze_video(event, comp, tmpdir, result),
                    timeout=self.cfg["video_timeout_s"],
                )
            except asyncio.TimeoutError:
                result.notes.append("处理超时，未完成部分已停止，已识别内容保留")
            except Exception as e:
                logger.warning(f"[face_convert] 视频处理失败: {type(e).__name__}")
                result.notes.append("视频解析失败，已识别内容保留")
        logger.info(f"[face_convert] 视频解读: {len(result.items)}段结果，{len(result.notes)}条提示")
        return result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def inbound_face_to_text(self, event: AstrMessageEvent):
        """把收到的 QQ 表情翻译成 [名字] 文本。

        纯表情消息的 message_str 为空，上游会当"空消息"处理导致她装死不回；
        这里把表情名拼进文本，让整条链路正常运转。
        """
        # 私聊里用户明确喊停：取消上一轮还在生成/发送的回复，避免旧气泡
        # 一条条追着新消息发。群聊不自动打断，免得误伤别人的回复。
        try:
            raw = (event.message_str or "").strip()
            if raw and event.is_private_chat() and self.INTERRUPT_RE.fullmatch(raw):
                from astrbot.core.utils.active_event_registry import (
                    active_event_registry,
                )

                stopped = active_event_registry.stop_all(
                    event.unified_msg_origin, exclude=event
                )
                logger.info(
                    f"[face_convert] 收到打断指令「{raw}」，已停止 {stopped} 条旧回复"
                )
        except Exception as e:
            logger.debug(f"[face_convert] 打断处理失败: {str(e)[:80]}")

        try:
            comps = event.get_messages()
        except Exception:
            return
        # 验尸记录仪：遇到不认识的组件类型就备案（排查神秘消息用）
        known = (Face, Image, Record, Video, Plain)
        unknown = [type(c).__name__ for c in comps if not isinstance(c, known)]
        if unknown:
            logger.info(f"[face_convert] 未知消息组件: {unknown}")
        names = []
        img_count = 0
        recs = []
        vids = []
        for c in comps:
            if isinstance(c, Face):
                names.append(f"[{self.id2name.get(c.id, f'表情{c.id}')}]")
            elif isinstance(c, Image):
                img_count += 1
            elif isinstance(c, Record):
                recs.append(c)
            elif isinstance(c, Video):
                vids.append(c)
        for _ in range(min(img_count, 3)):
            names.append("[对方发来一张图片或表情包]")
        if recs:
            async with self._media_sem:
                txt = await self._transcribe_voice(event, recs[0])
            names.append(
                f"[对方发来一条语音，你清楚地听到他说：{txt}]" if txt
                else "[对方发来一条语音（你没听清）]"
            )
        if vids:
            analysis = VideoAnalysis()
            acquired = False
            try:
                await asyncio.wait_for(
                    self._media_sem.acquire(), timeout=self.cfg["media_queue_timeout_s"]
                )
                acquired = True
                analysis = await self._process_video(event, vids[0])
            except asyncio.TimeoutError:
                analysis.notes.append("正在处理其他媒体，请稍后再试")
            except Exception as e:
                logger.warning(f"[face_convert] 视频处理异常: {type(e).__name__}")
                analysis.notes.append("视频处理异常")
            finally:
                if acquired:
                    self._media_sem.release()
            event.set_extra("_fy_video_incomplete", bool(analysis.notes or not analysis.items))
            names.append(f"[对方发来的视频，识别结果：{analysis.render()}]")
        if names:
            extra = " ".join(names)
            if extra not in (event.message_str or ""):
                event.message_str = ((event.message_str or "") + " " + extra).strip()
                logger.info(f"[face_convert] 收信翻译: {extra}")

        # 身份标记（只进她的上下文，对方看不到字面量）：
        # 群聊 -> [昵称(QQ号)]，管理员标「主人」；陌生人私聊 -> [主人的朋友 昵称(QQ号)]
        try:
            if not event.get_extra("_fy_tagged"):
                sid = str(event.get_sender_id())
                is_group = bool(event.get_group_id())
                is_guest = (not is_group) and event.is_private_chat() and sid not in self.admins
                if is_group or is_guest:
                    name = re.sub(r"[\[\]\r\n]", "", (event.get_sender_name() or "").strip())[:16]
                    if is_group and sid in self.admins:
                        tag = f"主人({sid})"
                    elif is_group:
                        tag = f"{name}({sid})" if name else f"群友({sid})"
                    else:
                        tag = f"主人的朋友 {name}({sid})" if name else f"主人的朋友({sid})"
                    text = (event.message_str or "").strip()
                    if text:
                        event.message_str = f"[{tag}] {text}"
                        event.set_extra("_fy_tagged", True)
                        logger.info(f"[face_convert] 身份标记: {tag}")
                    elif is_group and event.is_wake and not any(
                        (isinstance(c, At) and str(c.qq) == str(event.get_self_id()))
                        or (
                            isinstance(c, Reply)
                            and str(getattr(c, "sender_id", "")) == str(event.get_self_id())
                        )
                        for c in comps
                    ):
                        # 只喊了名字、没带内容：给她一个回应的由头
                        event.message_str = f"[{tag}] （喊了你一声）"
                        event.set_extra("_fy_tagged", True)
                        logger.info(f"[face_convert] 群聊空唤醒标记: {tag}")
        except Exception as e:
            logger.warning(f"[face_convert] 身份标记失败: {str(e)[:100]}")

    # 嘴硬橡皮擦：只擦「隐约听见/看到…」这种回声腔。
    # “看不清/没听清/模模糊糊”是诚实的不确定性说明，必须保留；
    # 旧版把它们一并擦掉，会把真实的能力边界也吞了。
    _HEDGE_STOP = r",，\.。！？!?;；、：:~…“”\"‘’（）()【】\[\]\s"
    HEDGE_RE = re.compile(
        f"[^{_HEDGE_STOP}]{{0,6}}"
        f"隐约(?:听见|听到|看到|飘来|飘进)"
        f"[^{_HEDGE_STOP}]{{0,20}}[，、：]?"
    )

    # 格式壳拆包：被"按格式输出"类的注入带偏时，她可能把回复套进程序结构里
    # （如 [{'type': 'text', 'text': '…'}] 这种），不是人话。拆壳把正文救回来。
    _SHELL_BLOCK = re.compile(
        r"\[(?:\s*\{[\"']type[\"']\s*:\s*[\"']text[\"']\s*,\s*[\"']text[\"']\s*:"
        r"\s*[\"'](?:[^\"'\\]|\\.)*[\"']\s*\}\s*,?)*\s*\]"
    )
    _SHELL_TEXT = re.compile(r"[\"']text[\"']\s*:\s*[\"']((?:[^\"'\\]|\\.)*)[\"']")

    # meta 口癖关键字（引用了注入内容或自我说明的句子，整句丢）
    _META_BASE = (
        r"PROTOCOL_ENFORCEMENT|必须生成|必须输出|系统等待|系统自检|System:|星绘|报告前辈|"
        r"一个字都不理|不吃这套|又来又来|不照(?:着|谁)|钓鱼的饵|这饵"
    )
    META_KW = re.compile(_META_BASE)  # 私聊版：留着她"喊冤/自我修正"的通道
    # 群聊版：把"框框/顶着嘴"这类也擦掉——朋友们不该看到她拆自己的台
    META_KW_GROUP = re.compile(_META_BASE + r"|框框|顶着.{0,3}嘴")
    # 清理内部标记前先保护：行内代码、链接、中文引号里的引用内容。
    # 这些是用户要的信息，不能因为句子里混了 meta 关键词就整句删掉。
    _PROTECT_INLINE = re.compile(
        r"`[^`\n]+`|https?://\S+|“[^”\n]{1,300}”|【[^】\n]{1,160}】"
    )

    @staticmethod
    def _protect_inline(text: str) -> tuple[str, list[str]]:
        saved: list[str] = []

        def repl(match: re.Match) -> str:
            saved.append(match.group(0))
            return f"\x00{len(saved) - 1}\x00"

        return FaceConvertPlugin._PROTECT_INLINE.sub(repl, text), saved

    @staticmethod
    def _restore_inline(text: str, saved: list[str]) -> str:
        for index, value in enumerate(saved):
            text = text.replace(f"\x00{index}\x00", value)
        return text

    @staticmethod
    def _normalize_punctuation(text: str) -> str:
        """把「」换成“”，并纠正“顿号当逗号用”的高频写法。

        只处理低风险场景：顿号后面直接跟“也/就/但/所以/或者”这类分句连接词。
        并列短词、数据列表（“2.7 万播放、85 条弹幕”）保持原样，避免误伤。
        """
        if not text or ("、" not in text and "「" not in text and "」" not in text):
            return text

        def fix(segment: str) -> str:
            segment = segment.replace("「", "“").replace("」", "”")
            if "、" not in segment:
                return segment
            return _PUNCT_DUNHAO_CLAUSE_RE.sub("，", segment)

        # ``` 代码块原样保留
        segments = text.split("```")
        for i in range(0, len(segments), 2):
            segments[i] = fix(segments[i])
        return "```".join(segments)

    def _scrub_meta_sentences(self, text: str, event: AstrMessageEvent) -> str:
        """按句丢弃 meta 口癖；代码块原样保留，避免误伤用户要的代码/数据。"""
        kw = self.META_KW_GROUP if event.get_group_id() else self.META_KW
        if not text or not kw.search(text):
            return text

        def scrub_segment(segment: str) -> str:
            protected, saved = self._protect_inline(segment)
            if not kw.search(protected):
                return segment
            # 按句丢弃带 meta 标记的句子（她那些"不理它/不照模板走"的前戏）
            parts = re.split(r"(?<=[。！？\n])", protected)
            # 含代码/链接/引用的句子整句保留，宁可少擦也不能误删内容
            kept = [s for s in parts if "\x00" in s or not kw.search(s)]
            joined = "".join(kept).replace("---", "")
            joined = self._restore_inline(joined, saved)
            joined = re.sub(r"\n{2,}", "\n", joined).strip().strip("，、")
            # meta 句被整句丢掉以后可能留下配对不上的括号残骸（比如结尾一个
            # 孤零零的"）"）。按数量补删：哪种括号多出来就删哪个，配对好的不动。
            for op, cl in (("（", "）"), ("(", ")"), ("【", "】"), ("《", "》")):
                while joined.count(cl) > joined.count(op):
                    joined = joined[::-1].replace(cl, "", 1)[::-1]
                while joined.count(op) > joined.count(cl):
                    joined = joined[::-1].replace(op, "", 1)[::-1]
            joined = joined.strip()
            return joined if joined.strip("，。！？、：;；~… \n") else "……"

        # ``` 代码块只清洗块外正文，块内代码/JSON 原样保留
        segments = text.split("```")
        for i in range(0, len(segments), 2):
            segments[i] = scrub_segment(segments[i])
        return "```".join(segments)

    @filter.on_llm_response()
    async def scrub_drafts(self, event: AstrMessageEvent, response) -> None:
        """发送前的两道橡皮擦：①嘴硬回声 ②meta 口癖句。

        双括号草稿机制已在 v1.4.0 移除：`((a+b))` 这类合法表达式不能被
        当成内心戏撕掉。真正需要隐藏的内部标记由 meta 句清理处理。
        """
        try:
            text = getattr(response, "completion_text", None) or ""
            if not text:
                return
            cleaned = text
            # 第〇道：格式壳拆包（被注入带偏时，她会把回复套进程序结构里出壳）
            if ("'type'" in cleaned and "'text'" in cleaned) or (
                '"type"' in cleaned and '"text"' in cleaned
            ):
                pre = cleaned
                cleaned = self._SHELL_BLOCK.sub(
                    lambda m: "".join(
                        p.replace("\\n", "\n")
                        .replace("\\'", "'")
                        .replace('\\"', '"')
                        for p in self._SHELL_TEXT.findall(m.group(0))
                    ),
                    cleaned,
                )
                if cleaned != pre:
                    logger.info("[face_convert] 已拆格式壳")
            # 嘴硬橡皮擦：只擦“隐约听见/看到…”这种回声腔。真实识别失败时
            # 她会说“没听清，请再说一次”——这些诚实说明必须保留；含请求词
            # 的整条也放行。config.json 里 hedge_eraser=false 可整体关闭。
            _request_words = r"重发|再发|再说|再听|再看|发一遍|发一张|换一张|重新发|发个"
            hedged = (
                self.HEDGE_RE.sub("", cleaned)
                if self.cfg.get("hedge_eraser", True)
                and not event.get_extra("_fy_video_incomplete")
                and not re.search(_request_words, cleaned)
                else cleaned
            )
            if hedged != cleaned:
                cleaned = hedged
                logger.info("[face_convert] 已擦掉嘴硬小尾巴")
            scrubbed = self._scrub_meta_sentences(cleaned, event)
            if scrubbed != cleaned:
                cleaned = scrubbed
                logger.info("[face_convert] 已清扫 meta 口癖")
            normalized = self._normalize_punctuation(cleaned)
            if normalized != cleaned:
                cleaned = normalized
                logger.info("[face_convert] 已规范中文标点")
            cleaned = cleaned.strip()
            if not cleaned.strip("，。！？、：;；~… \n"):
                # 整条都被擦光 -> 换成鱼的沉默
                cleaned = "……"
            if cleaned != text:
                response.completion_text = cleaned
        except Exception as e:
            logger.warning(f"[face_convert] 草稿清理失败: {e}")

    @filter.on_decorating_result()
    async def convert_faces(self, event: AstrMessageEvent):
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return

        # 思维链防漏：EmotionAI 的 <thought> 块偶尔会漏进消息（甚至被 TTS 念出来）。
        # 在"出门关口"再查一遍，不影响 EmotionAI 自己的情绪提取。
        if any(
            isinstance(c, Plain) and c.text and "<thought" in c.text
            for c in result.chain
        ):
            cleaned_chain = []
            for c in result.chain:
                if isinstance(c, Plain) and c.text:
                    t = re.sub(r"<thought>.*?</thought>", "", c.text, flags=re.DOTALL)
                    # 未闭合的 <thought>：从它一直截到结尾
                    t = re.sub(r"<thought>.*$", "", t, flags=re.DOTALL)
                    t = t.replace("</thought>", "").strip()
                    if not t:
                        continue
                    c.text = t
                cleaned_chain.append(c)
            logger.info("[face_convert] 已摘掉漏出的 <thought> 思维链块")
            if not cleaned_chain:
                result.chain = []
                return
            result.chain = cleaned_chain

        # 暗号：只有整条回复就是 [NO_REPLY] 时才静默。旧逻辑只要出现这个
        # 字符串就吞掉整条，连“这个标记是静默暗号”的解释也会被吃掉。
        plain_comps = [
            c for c in result.chain if isinstance(c, Plain) and c.text
        ]
        joined = "".join(c.text for c in plain_comps)
        if (
            plain_comps
            and len(plain_comps) == len(result.chain)
            and self.NO_REPLY_RE.fullmatch(joined.strip())
        ):
            result.chain = []
            logger.info("[face_convert] 收到 [NO_REPLY] 暗号，已吞掉这条回复")
            return
        # 不是整条暗号时，只丢掉“单独成段”的暗号，解释文字照常保留。
        marker_only = [
            c for c in plain_comps if self.NO_REPLY_RE.fullmatch(c.text.strip())
        ]
        if marker_only:
            marker_ids = {id(c) for c in marker_only}
            result.chain = [c for c in result.chain if id(c) not in marker_ids]
            logger.info("[face_convert] 已移除单独成段的 [NO_REPLY] 标记")
            if not result.chain:
                return

        # 工具循环（tool_loop_agent_runner）的最终回复不经过 on_llm_response，
        # 在出门关口再擦一遍 meta 口癖和中文标点；只处理 Plain 文本，不碰图片/工具结果。
        meta_changed = False
        meta_chain = []
        for c in result.chain:
            if isinstance(c, Plain) and c.text:
                new_text = self._normalize_punctuation(
                    self._scrub_meta_sentences(c.text, event)
                )
                if new_text != c.text:
                    c.text = new_text
                    meta_changed = True
                if not c.text.strip():
                    continue
            meta_chain.append(c)
        if meta_changed:
            result.chain = meta_chain or [Plain("……")]
            logger.info("[face_convert] 已清扫 meta 口癖/标点（结果关口）")

        new_chain = []
        changed = 0
        stripped = 0
        dropped_faces = 0
        per_msg = 0
        for comp in result.chain:
            if not isinstance(comp, Plain) or not comp.text:
                new_chain.append(comp)
                continue
            text = comp.text
            pos = 0
            for m in self.pattern.finditer(text):
                if changed >= MAX_REPLACE:
                    break
                name = m.group(1).strip()
                fid = self.face_map.get(name)
                if fid is None:
                    # 兜底：像表情名的短词（1~6个中文/字母）但字典不认 ->
                    # 去掉方括号保留文字，起码不是一堆 [ ] 裸奔
                    if re.fullmatch(r"[A-Za-z一-鿿]{1,6}", name):
                        if m.start() > pos:
                            new_chain.append(Plain(text[pos : m.start()]))
                        new_chain.append(Plain(name))
                        pos = m.end()
                        stripped += 1
                    continue
                # 表情冷却：同一张脸最近 2 次用过就消失；每条最多 2 张
                # （按会话隔离 + 时间过期，避免 A 群用过的脸把 B 群也冷却掉）
                hist = self._face_history.setdefault(event.unified_msg_origin, [])
                _cutoff = time.time() - float(
                    self.cfg.get("face_cooldown_minutes", 10)
                ) * 60
                hist[:] = [t for t in hist if t[1] >= _cutoff]
                if per_msg >= 2 or fid in [t[0] for t in hist[-2:]]:
                    if m.start() > pos:
                        new_chain.append(Plain(text[pos : m.start()]))
                    pos = m.end()
                    dropped_faces += 1
                    continue
                if m.start() > pos:
                    new_chain.append(Plain(text[pos : m.start()]))
                new_chain.append(Face(id=fid))
                hist.append((fid, time.time()))
                del hist[:-8]
                per_msg += 1
                pos = m.end()
                changed += 1
            if pos < len(text):
                new_chain.append(Plain(text[pos:]))

        if changed or stripped or dropped_faces:
            result.chain = new_chain
            logger.info(
                f"[face_convert] 已转换 {changed} 个文字表情 -> 真实表情段"
                + (f"，脱括号 {stripped} 个（字典不认的）" if stripped else "")
                + (f"，冷却丢弃 {dropped_faces} 个" if dropped_faces else "")
            )

    # ---------------- 语音开关（会话级，走 AstrBot 官方会话配置） ----------------

    @filter.command("语音开")
    async def voice_on(self, event: AstrMessageEvent):
        """开启本会话的语音条。"""
        await SessionServiceManager.set_tts_status_for_session(
            event.unified_msg_origin, True
        )
        yield event.plain_result("开麦！想听我说话就多喊我两句呀~")

    @filter.command("语音关")
    async def voice_off(self, event: AstrMessageEvent):
        """关闭本会话的语音条（文字照常聊）。"""
        await SessionServiceManager.set_tts_status_for_session(
            event.unified_msg_origin, False
        )
        yield event.plain_result("好嘛好嘛，我先闭嘴，文字照聊！（小声嘟囔）")

    @filter.command("语音状态")
    async def voice_status(self, event: AstrMessageEvent):
        """查询本会话的语音条开关状态。"""
        on = await SessionServiceManager.is_tts_enabled_for_session(
            event.unified_msg_origin
        )
        yield event.plain_result(f"语音条现在是{'开' if on else '关'}的哦~")
