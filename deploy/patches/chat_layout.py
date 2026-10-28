"""Shared layout helpers for the chat pipeline patches.

This file is bind-mounted as ``astrbot/core/pipeline/chat_layout.py``.  It has
no AstrBot imports on purpose: the repo's regression tests load it directly,
and the two patched stages import only these pure functions.

Keeping the rules here means the display bubbles and the TTS text are derived
from the same semantic source instead of being re-split independently.
"""

from __future__ import annotations

import os
import re

# A bubble should end only at a real sentence boundary.  Tilde / ellipsis are
# included because the bot's persona uses them as sentence enders.
SENTENCE_END = "。！？!?…~～"
_TRAILING_CLOSERS = "\"'”’）)】」》〉"

_EMOJI_MARK = re.compile(r"\[[A-Za-z一-鿿]{1,6}\]")
_NO_REPLY_MARK = re.compile(r"[\[【]\s*NO[_ -]?REPLY\s*[\]】]", re.IGNORECASE)
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://\S+")
_MARKDOWN_MARKS = re.compile(r"(\*\*|__|~~|^#{1,6}\s+|^>\s*)", re.MULTILINE)
_SPACES = re.compile(r"[ \t　]{2,}")


def text_ends_sentence(text: str) -> bool:
    """Return True when *text* ends with a sentence boundary.

    Trailing quotes/brackets are ignored so ``好呀。”`` still counts as an
    ending.  Newlines are handled by the caller as hard boundaries.
    """
    if text and text.rstrip(" \t　").endswith("\n"):
        return True
    s = (text or "").rstrip()
    if not s:
        return False
    while s and s[-1] in _TRAILING_CLOSERS:
        s = s[:-1].rstrip()
    return bool(s) and (s[-1] in SENTENCE_END or s.endswith("\n"))


def _is_record(comp, record_type) -> bool:
    return record_type is not None and isinstance(comp, record_type)


def group_bubbles(
    chain,
    plain_type,
    record_type=None,
    max_bubbles: int = 3,
):
    """Group a decorated message chain into chat bubbles.

    ``ResultDecorateStage`` emits one ``Plain`` per semantic sentence.  Face
    conversion may later split a single ``Plain`` into ``Plain / Face /
    Plain`` around an inline emoji.  A bubble is therefore continued when the
    next ``Plain`` follows a non-``Plain`` component and the previous text did
    not end a sentence; otherwise a new bubble starts.

    Records are always isolated: QQ delivers a voice segment as its own
    message.  Text bubbles are capped at *max_bubbles*; extra sentences are
    merged into the last text bubble instead of producing a wall of messages.
    """
    if max_bubbles is None or max_bubbles <= 0:
        max_bubbles = 0

    bubbles: list[list] = []

    def text_bubble_count() -> int:
        return sum(1 for b in bubbles if not any(_is_record(c, record_type) for c in b))

    def merge_into_last_text_bubble(comp) -> bool:
        for bubble in reversed(bubbles):
            if not any(_is_record(c, record_type) for c in bubble):
                bubble.append(comp)
                return True
        return False

    for comp in chain:
        if not bubbles:
            bubbles.append([comp])
            continue

        current = bubbles[-1]

        if isinstance(comp, plain_type):
            # Text after a voice segment always starts a new bubble.
            if _is_record(current[-1], record_type):
                bubbles.append([comp])
                continue

            previous_plain = next(
                (c for c in reversed(current) if isinstance(c, plain_type)),
                None,
            )
            starts_new = previous_plain is not None and text_ends_sentence(
                getattr(previous_plain, "text", "")
            )
            if starts_new and max_bubbles and text_bubble_count() >= max_bubbles:
                # Cap reached: keep the remaining sentences together instead
                # of turning the tail into a pile of short messages.
                merge_into_last_text_bubble(comp)
            elif starts_new:
                bubbles.append([comp])
            else:
                current.append(comp)
        elif _is_record(comp, record_type):
            bubbles.append([comp])
        else:
            current.append(comp)

    return bubbles


def make_tts_text(text: str) -> str:
    """Turn display text into text that is pleasant to read aloud.

    Code, URLs and emoji markers are not useful speech.  Punctuation is kept
    so the synthesizer can phrase the whole reply as one coherent utterance.
    """
    s = text or ""
    s = _FENCED_CODE.sub("（代码部分看文字）", s)
    s = _INLINE_CODE.sub("（代码）", s)
    s = _URL.sub("链接", s)
    s = _NO_REPLY_MARK.sub("", s)
    s = _EMOJI_MARK.sub("", s)
    s = _MARKDOWN_MARKS.sub("", s)
    s = _SPACES.sub(" ", s)
    s = "\n".join(line.strip() for line in s.splitlines())
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if not s.strip("…。.，,!！?？~～、：:;； \n\t　"):
        return ""
    return s


def is_valid_audio_file(path: str) -> bool:
    """Cheap decode sanity check before handing a file to the QQ adapter.

    DashScope occasionally returns an HTML/JSON error body with HTTP 200.
    Writing that to ``.wav`` and sending it makes NapCat fail silently, so the
    caller must fall back to text instead.
    """
    try:
        path = str(path or "")
        if not path or not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        if size < 512:
            return False
        with open(path, "rb") as f:
            head = f.read(16)
        if head.lstrip()[:1] in (b"<", b"{"):
            return False
        lower = path.lower()
        if lower.endswith(".wav"):
            return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
        if lower.endswith(".mp3"):
            return head[:3] == b"ID3" or head[:1] == b"\xff"
        # Other containers (ogg/opus/silk/...) are accepted as long as the
        # body does not look like a text error page.
        return True
    except OSError:
        return False
