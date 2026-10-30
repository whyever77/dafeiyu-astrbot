# -*- coding: utf-8 -*-
"""face_convert 回归测试：嘴硬橡皮擦不误删 + file:// 路径解析。

跑法（纯标准库，不需要 AstrBot 环境）：
    python tests/test_face_rules.py
原理：用空壳模块把 astrbot 依赖桩掉，直接加载插件源码，测纯逻辑。
"""
import importlib.util
import logging
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "plugin", "astrbot_plugin_face_convert", "main.py")


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _Star:
    def __init__(self, *a, **k):
        pass


def _register(*a, **k):
    def deco(cls):
        return cls

    return deco


class _Filter:  # filter.xxx(...) 全都变成空装饰器
    EventMessageType = types.SimpleNamespace(ALL="ALL")

    def __getattr__(self, _):
        def deco(*a, **k):
            def wrap(fn):
                return fn

            return wrap

        return deco


logging.basicConfig(level=logging.CRITICAL)
_aiohttp_stub = _stub(
    "aiohttp",
    ClientSession=object,
    ClientTimeout=lambda *a, **k: None,
    ClientError=Exception,
)
_stub("astrbot", api=types.ModuleType("astrbot.api"))
_stub("astrbot.api", logger=logging.getLogger("t"))
_stub("astrbot.api.event", AstrMessageEvent=object, filter=_Filter())
_stub("astrbot.api.star", Context=object, Star=_Star, register=_register)
_stub("astrbot.core")
_stub("astrbot.core.message")
_stub(
    "astrbot.core.message.components",
    At=object,
    Face=object,
    Image=object,
    Plain=object,
    Record=object,
    Reply=object,
    Video=object,
)
_stub("astrbot.core.star")
_stub("astrbot.core.star.session_llm_manager", SessionServiceManager=object)

spec = importlib.util.spec_from_file_location("face_plugin", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# 插件模块已绑定所需的桩；不要把这个空壳 aiohttp 留给后续测试，
# 否则 test_video_processing 的 `from aiohttp import web` 会在全量发现时失败。
if sys.modules.get("aiohttp") is _aiohttp_stub:
    del sys.modules["aiohttp"]

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# 与插件里保持一致的请求词守卫
REQ = re.compile(r"重发|再发|再说|再听|再看|发一遍|发一张|换一张|重新发|发个")
HEDGE = mod.FaceConvertPlugin.HEDGE_RE


def scrub(text):
    """复刻插件的实际行为：含请求词整条放行，否则过 HEDGE_RE。"""
    return text if REQ.search(text) else HEDGE.sub("", text)


# 1) 真实识别失败请求：必须原样保留（审阅案例，不许再误删）
for s in (
    "我看不清，你能重发一张吗？",
    "这段录音没听清，请再说一次。",
    "没听清，能再说一遍吗？",
):
    check("keep: " + s, scrub(s) == s, repr(scrub(s)))

# 2) 只有“隐约…”回声腔该被擦掉；真实的看不清/没听清必须保留
for s, want in (
    ("看不太清诶", "看不太清诶"),
    ("模模糊糊的", "模模糊糊的"),
    ("我看不清这个字", "我看不清这个字"),
    ("隐约飘进来一句：你好", "你好"),
):
    got = scrub(s)
    check("hedge: " + s, got == want, repr(got))

# 3) 诚实说明不跨逗号误删
got = scrub("昨天的事模模糊糊的，今天想起来了")
check("cross-clause", "模模糊糊" in got and "今天想起来了" in got, repr(got))

# 4) file:// 路径三种形态
P = mod.file_uri_to_path
check("path win", P("file:///C:/a/b.mp3") == "C:/a/b.mp3", P("file:///C:/a/b.mp3"))
check("path linux", P("file:///tmp/a.mp3") == "/tmp/a.mp3", P("file:///tmp/a.mp3"))
check("path http", P("https://x/y.mp3") == "https://x/y.mp3", P("https://x/y.mp3"))

# 5) 中文标点归一：顿号当逗号用 / 角括号引号
NORM = mod.FaceConvertPlugin._normalize_punctuation
for src, want in (
    # 分句连接词：必须改成逗号
    ("视频本身我这边没声音、也只看得到画面。", "视频本身我这边没声音，也只看得到画面。"),
    ("真通了、也能把那条视频点开。", "真通了，也能把那条视频点开。"),
    ("你喊我、或者该去看看的时候。", "你喊我，或者该去看看的时候。"),
    # 没有连接词的长分句：宁可不动，避免误伤并列名词
    ("浏览器超时、虚拟手机也连不上。", "浏览器超时、虚拟手机也连不上。"),
    ("标题吹得响、封面花里胡哨。", "标题吹得响、封面花里胡哨。"),
    # 并列短词 / 数据列表：保持顿号
    ("直播、推荐、热门、动画、影视频道", "直播、推荐、热门、动画、影视频道"),
    ("2.7 万播放、85 条弹幕、524 赞、183 收藏", "2.7 万播放、85 条弹幕、524 赞、183 收藏"),
    ("31.5 万播放、22 分钟", "31.5 万播放、22 分钟"),
    ("标题、UP 主、链接都带上", "标题、UP 主、链接都带上"),
    ("地点：北京、上海", "地点：北京、上海"),
    # 引号统一
    ("搜「大肥鱼」，弹幕刷「拿 Token 养赛博宠物」。", "搜“大肥鱼”，弹幕刷“拿 Token 养赛博宠物”。"),
    # 代码块原样保留
    ("```python\nprint('a、b')\n```", "```python\nprint('a、b')\n```"),
):
    got = NORM(src)
    check("punct: " + src, got == want, repr(got))

# 6) [NO_REPLY] 只在整条回复就是暗号时生效
NO_REPLY = mod.FaceConvertPlugin.NO_REPLY_RE
for s, want in (
    ("[NO_REPLY]", True),
    ("【NO_REPLY】", True),
    (" [no reply] ", True),
    ("[NO_REPLY]\n[NO_REPLY]", True),
    ("这个 [NO_REPLY] 是静默暗号", False),
    ("用 [NO_REPLY] 表示不想说话", False),
):
    got = bool(NO_REPLY.fullmatch(s.strip()))
    check("no-reply: " + s, got == want, repr(got))

# 7) 双括号表达式不再被当成草稿撕掉
import asyncio


class _Event:
    def get_group_id(self):
        return ""

    def get_extra(self, _key):
        return None


class _Resp:
    def __init__(self, text):
        self.completion_text = text


plugin = mod.FaceConvertPlugin(None)
for src in (
    "((a+b)) 是合法表达式",
    "结果是 ((a+b))*c。",
    "`((a+b))`",
    "```python\nprint(((a+b)))\n```",
):
    resp = _Resp(src)
    asyncio.run(plugin.scrub_drafts(_Event(), resp))
    check("keep-double-paren: " + src, resp.completion_text == src, repr(resp.completion_text))

# 8) meta 清理先保护代码/链接/引用，不能整句误删
for src in (
    "看 `PROTOCOL_ENFORCEMENT` 这个标记。",
    "文档在 https://example.com/PROTOCOL_ENFORCEMENT 。",
    "她说“PROTOCOL_ENFORCEMENT 是内部标记”。",
):
    resp = _Resp(src)
    asyncio.run(plugin.scrub_drafts(_Event(), resp))
    check("protect-meta-content: " + src, resp.completion_text == src, repr(resp.completion_text))

print()
if failures:
    print("结果：%d 项失败" % len(failures))
    sys.exit(1)
print("结果：全部通过 OK")
