# -*- coding: utf-8 -*-
"""气泡分组 / 整段语音文本的纯逻辑回归（不依赖 AstrBot）。

跑法：python tests/test_chat_layout.py
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUT = os.path.join(ROOT, "deploy", "patches", "chat_layout.py")


def load_layout():
    spec = importlib.util.spec_from_file_location("chat_layout", LAYOUT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_layout()


class Plain:
    def __init__(self, text):
        self.text = text


class Face:
    def __init__(self, fid=1):
        self.id = fid


class Record:
    def __init__(self, file="/tmp/a.wav"):
        self.file = file


failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def texts(bubble):
    return [getattr(c, "text", "<face>") for c in bubble]


# 1) 表情夹在句子中间：一句话仍是一个气泡
chain = [Plain("前半句"), Face(), Plain("后半句")]
bubbles = mod.group_bubbles(chain, Plain, Record, 3)
check("inline-face-one-bubble", len(bubbles) == 1 and texts(bubbles[0]) == ["前半句", "<face>", "后半句"], repr(bubbles))

# 2) 第一句已经结束：表情留在上一泡，第二句另起一泡
chain = [Plain("第一句。"), Face(), Plain("第二句")]
bubbles = mod.group_bubbles(chain, Plain, Record, 3)
check("sentence-boundary", len(bubbles) == 2 and texts(bubbles[0]) == ["第一句。", "<face>"] and texts(bubbles[1]) == ["第二句"], repr(bubbles))

# 3) 超过上限的句子并进最后一泡，不刷屏
chain = [Plain(f"第{i}句。") for i in range(1, 6)]
bubbles = mod.group_bubbles(chain, Plain, Record, 3)
check("cap-three-bubbles", len(bubbles) == 3, repr(bubbles))
check("cap-merges-tail", texts(bubbles[-1]) == ["第3句。", "第4句。", "第5句。"], repr(bubbles[-1]))

# 4) 语音单独一泡，不跟文字混发
chain = [Plain("好呀。"), Record()]
bubbles = mod.group_bubbles(chain, Plain, Record, 3)
check("record-own-bubble", len(bubbles) == 2 and isinstance(bubbles[1][0], Record), repr(bubbles))

# 5) 语音之后再有文字，另起一泡
chain = [Record(), Plain("补充一句。")]
bubbles = mod.group_bubbles(chain, Plain, Record, 3)
check("text-after-record", len(bubbles) == 2 and texts(bubbles[1]) == ["补充一句。"], repr(bubbles))

# 6) 标点判断：引号/括号收尾也算句末
for s, want in (
    ("好呀。", True),
    ("好呀。”", True),
    ("好呀", False),
    ("好呀~", True),
    ("好呀\n", True),
    ("好呀 \n", True),
    ("", False),
):
    got = mod.text_ends_sentence(s)
    check("ends: " + repr(s), got == want, repr(got))

# 7) 朗读文本：去掉表情/链接/代码，保留语义和标点
tts = mod.make_tts_text("你好[旺柴]，看这个 https://example.com/x 吧。```py\nprint(1)\n```")
check("tts-no-emoji", "[旺柴]" not in tts, tts)
check("tts-no-url", "https://" not in tts and "链接" in tts, tts)
check("tts-no-code", "print(1)" not in tts, tts)
check("tts-keeps-punctuation", "你好" in tts and "吧。" in tts, tts)
check("tts-pure-punct-empty", mod.make_tts_text("……") == "", repr(mod.make_tts_text("……")))

# 8) 音频文件校验：WAV 头通过，HTML 错误页/过小文件拒绝
with tempfile.TemporaryDirectory() as td:
    wav = os.path.join(td, "ok.wav")
    with open(wav, "wb") as f:
        f.write(b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 2048)
    html = os.path.join(td, "bad.wav")
    with open(html, "wb") as f:
        f.write(b"<html>" + b"x" * 2048)
    tiny = os.path.join(td, "tiny.wav")
    with open(tiny, "wb") as f:
        f.write(b"RIFF")
    check("wav-ok", mod.is_valid_audio_file(wav))
    check("html-rejected", not mod.is_valid_audio_file(html))
    check("tiny-rejected", not mod.is_valid_audio_file(tiny))
    check("missing-rejected", not mod.is_valid_audio_file(os.path.join(td, "nope.wav")))

print()
if failures:
    print("结果：%d 项失败" % len(failures))
    sys.exit(1)
print("结果：全部通过 OK")
