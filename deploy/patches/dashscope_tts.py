import asyncio
import base64
import logging
import os

import aiohttp
import dashscope
from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

try:
    from dashscope.aigc.multimodal_conversation import MultiModalConversation
except (
    ImportError
):  # pragma: no cover - older dashscope versions without Qwen TTS support
    MultiModalConversation = None

from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter


@register_provider_adapter(
    "dashscope_tts",
    "Dashscope TTS API",
    provider_type=ProviderType.TEXT_TO_SPEECH,
)
class ProviderDashscopeTTSAPI(TTSProvider):
    def __init__(
        self,
        provider_config: dict,
        provider_settings: dict,
    ) -> None:
        super().__init__(provider_config, provider_settings)
        self.chosen_api_key: str = provider_config.get("api_key", "")
        self.voice: str = provider_config.get("dashscope_tts_voice", "loongstella")
        # 补丁2: 情绪指令 + 语速/音高（qwen-audio-3.1-tts-flash 支持自然语言指令）
        self.instruction: str = provider_config.get("dashscope_instruction", "")
        self.speech_rate: float = float(provider_config.get("dashscope_speech_rate", 1.0))
        self.pitch_rate: float = float(provider_config.get("dashscope_pitch_rate", 1.0))
        self.language_type: str = provider_config.get("dashscope_language_type", "")
        self.set_model(provider_config["model"])
        self.timeout_ms = float(provider_config.get("timeout", 20)) * 1000
        dashscope.api_key = self.chosen_api_key

    async def get_audio(self, text: str) -> str:
        model = self.get_model()
        if not model:
            raise RuntimeError("Dashscope TTS model is not configured.")

        temp_dir = get_astrbot_temp_path()
        os.makedirs(temp_dir, exist_ok=True)

        if self._is_qwen_tts_model(model):
            audio_bytes, ext = await self._synthesize_with_qwen_tts(model, text)
        else:
            audio_bytes, ext = await self._synthesize_with_cosyvoice(model, text)

        if not audio_bytes:
            raise RuntimeError(
                "Audio synthesis failed, returned empty content. The model may not be supported or the service is unavailable.",
            )
        if not self._looks_like_audio(audio_bytes):
            raise RuntimeError(
                "Audio synthesis returned a non-audio body (likely an HTML/JSON error page).",
            )

        path = os.path.join(temp_dir, f"dashscope_tts_{generate_timestamp_id()}{ext}")
        with open(path, "wb") as f:
            f.write(audio_bytes)
        if os.path.getsize(path) < 512:
            raise RuntimeError("Audio synthesis returned a file that is too small to decode.")
        return path

    def _style_instruction(self) -> str:
        """把情绪/语速/音高转成模型能理解的文字指令。

        不是所有模型都接受 speed/pitch 数值：qwen-audio-3.1 走自然语言
        instruction，CosyVoice 才支持数值参数。统一在这里做能力映射。
        """
        parts: list[str] = []
        if self.instruction.strip():
            parts.append(self.instruction.strip())
        if abs(self.speech_rate - 1.0) > 0.01:
            parts.append(f"语速约为常速的 {self.speech_rate:.2f} 倍")
        if abs(self.pitch_rate - 1.0) > 0.01:
            parts.append(
                "音调比默认略高" if self.pitch_rate > 1.0 else "音调比默认略低"
            )
        return "，".join(parts)

    @staticmethod
    def _looks_like_audio(data: bytes | None) -> bool:
        if not data or len(data) < 512:
            return False
        head = data[:16]
        if head.lstrip()[:1] in (b"<", b"{"):
            return False
        return True

    @staticmethod
    def _detect_ext(data: bytes) -> str:
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return ".wav"
        if data[:3] == b"ID3" or data[:1] == b"\xff":
            return ".mp3"
        return ".wav"

    def _call_qwen_tts(self, model: str, text: str):
        if MultiModalConversation is None:
            raise RuntimeError(
                "dashscope SDK missing MultiModalConversation. Please upgrade the dashscope package to use Qwen TTS models.",
            )

        kwargs = {
            "model": model,
            "headers": self.request_headers.copy(),
            "messages": None,
            "api_key": self.chosen_api_key,
            "voice": self.voice or "Cherry",
            "text": text,
        }
        # qwen3-tts-instruct-* 用 instructions（自然语言风格控制）；
        # 普通 qwen-tts 不支持该字段，传了反而可能报参数错误。
        style = self._style_instruction()
        if style and "instruct" in model.lower():
            kwargs["instructions"] = style
        if self.language_type:
            kwargs["language_type"] = self.language_type
        if not self.voice:
            logging.warning(
                "No voice specified for Qwen TTS model, using default 'Cherry'.",
            )
        return MultiModalConversation.call(**kwargs)

    async def _synthesize_with_qwen_tts(
        self,
        model: str,
        text: str,
    ) -> tuple[bytes | None, str]:
        loop = asyncio.get_running_loop()
        timeout = max(self.timeout_ms / 1000, 1) if self.timeout_ms else 20
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(None, self._call_qwen_tts, model, text),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Qwen TTS timed out after {timeout:.0f}s; falling back to text."
            ) from exc
        audio_bytes = await self._extract_audio_from_response(response)
        if not audio_bytes:
            raise RuntimeError(
                f"Audio synthesis failed for model '{model}'. {response}",
            )
        return audio_bytes, self._detect_ext(audio_bytes)

    async def _extract_audio_from_response(self, response) -> bytes | None:
        output = getattr(response, "output", None)
        audio_obj = getattr(output, "audio", None) if output is not None else None
        if not audio_obj:
            return None

        data_b64 = getattr(audio_obj, "data", None)
        if data_b64:
            try:
                data = base64.b64decode(data_b64)
            except (ValueError, TypeError):
                logging.exception("Failed to decode base64 audio data.")
                return None
            return data if self._looks_like_audio(data) else None

        url = getattr(audio_obj, "url", None)
        if url:
            return await self._download_audio_from_url(url)
        return None

    async def _download_audio_from_url(self, url: str) -> bytes | None:
        if not url:
            return None
        timeout = max(self.timeout_ms / 1000, 1) if self.timeout_ms else 20
        max_bytes = 20 * 1024 * 1024
        try:
            async with (
                aiohttp.ClientSession(
                    headers={"User-Agent": self.request_headers["User-Agent"]}
                ) as session,
                session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response,
            ):
                if response.status != 200:
                    logging.error(
                        "Failed to download TTS audio: HTTP %s from %s",
                        response.status,
                        url,
                    )
                    return None
                if (response.content_length or 0) > max_bytes:
                    logging.error("TTS audio download exceeds the 20 MiB limit")
                    return None
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        logging.error("TTS audio download exceeds the 20 MiB limit")
                        return None
                data = bytes(body)
                return data if self._looks_like_audio(data) else None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            logging.exception(f"Failed to download audio from URL {url}: {e}")
            return None

    async def _synthesize_with_cosyvoice(
        self,
        model: str,
        text: str,
    ) -> tuple[bytes | None, str]:
        synth_kwargs = {
            "headers": {
                name.lower(): value for name, value in self.request_headers.items()
            },
            "model": model,
            "voice": self.voice,
            "format": AudioFormat.WAV_24000HZ_MONO_16BIT,
            "instruction": self._style_instruction() or None,
        }
        # qwen-audio 走自然语言 instruction；数值 speech_rate/pitch_rate 只对
        # CosyVoice 系列有意义，避免给不支持该参数的模型硬塞。
        if not model.lower().startswith("qwen-audio"):
            synth_kwargs["speech_rate"] = self.speech_rate
            synth_kwargs["pitch_rate"] = self.pitch_rate
        synthesizer = SpeechSynthesizer(**synth_kwargs)
        loop = asyncio.get_running_loop()
        timeout = max(self.timeout_ms / 1000, 1) if self.timeout_ms else 20
        try:
            audio_bytes = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    synthesizer.call,
                    text,
                    self.timeout_ms,
                ),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"TTS timed out after {timeout + 5:.0f}s; falling back to text."
            ) from exc
        if not audio_bytes:
            resp = synthesizer.get_response()
            detail = f" {resp}".strip() if resp and isinstance(resp, dict) else ""
            raise RuntimeError(
                f"Audio synthesis failed for model '{model}'.{detail}".strip(),
            )
        if not self._looks_like_audio(audio_bytes):
            raise RuntimeError(
                f"Audio synthesis for model '{model}' returned a non-audio body.",
            )
        return audio_bytes, self._detect_ext(audio_bytes)

    def _is_qwen_tts_model(self, model: str) -> bool:
        model_lower = model.lower()
        # 补丁1: qwen-audio-* (含声音复刻音色) 走 tts_v2 WebSocket 接口, 不走 HTTP
        if model_lower.startswith("qwen-audio"):
            return False
        return "tts" in model_lower and model_lower.startswith("qwen")
