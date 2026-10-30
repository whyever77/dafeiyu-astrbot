# -*- coding: utf-8 -*-
"""日程注入回归：cron/一次性任务、会话隔离、UTC→本地时间。

跑法（纯标准库）：
    python tests/test_schedule_awareness.py
"""
import importlib.util
import json
import logging
import os
import sqlite3
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(
    ROOT, "plugin", "astrbot_plugin_schedule_awareness", "main.py"
)


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _Star:
    def __init__(self, *args, **kwargs):
        pass


def _register(*args, **kwargs):
    def deco(cls):
        return cls

    return deco


class _Filter:
    def __getattr__(self, _):
        def deco(*args, **kwargs):
            def wrap(fn):
                return fn

            return wrap

        return deco


logging.basicConfig(level=logging.CRITICAL)
_stub("astrbot", api=types.ModuleType("astrbot.api"))
_stub("astrbot.api", logger=logging.getLogger("t"))
_stub("astrbot.api.event", AstrMessageEvent=object, filter=_Filter())
_stub("astrbot.api.star", Context=object, Star=_Star, register=_register)

spec = importlib.util.spec_from_file_location("schedule_plugin", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


plugin = object.__new__(mod.ScheduleAwarenessPlugin)
UMO = "qq:FriendMessage:10001"
OTHER = "qq:FriendMessage:10002"

jobs = [
    {
        "name": "大肥鱼叫起床",
        "description": "私聊主人叫他起床",
        "cron_expression": "0 8 * * *",
        "timezone": "Asia/Shanghai",
        "payload": json.dumps({"session": UMO}),
        "run_once": 0,
        "next_run_time": "2026-10-21 00:00:00",
    },
    {
        "name": "大肥鱼催睡觉",
        "description": "催他早点睡",
        "cron_expression": "30 23 * * *",
        "timezone": "Asia/Shanghai",
        "payload": json.dumps({"session": UMO}),
        "run_once": 0,
        "next_run_time": "2026-10-21 15:30:00",
    },
    {
        "name": "叫主人起床（07:00 一次性）",
        "description": "一次性叫醒",
        "cron_expression": None,
        "timezone": "Asia/Shanghai",
        "payload": json.dumps(
            {"session": UMO, "run_at": "2026-10-21T07:00:00+08:00"}
        ),
        "run_once": 1,
        "next_run_time": "2026-10-20 23:00:00",
    },
    {
        "name": "别人的任务",
        "description": "不该出现",
        "cron_expression": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "payload": json.dumps({"session": OTHER}),
        "run_once": 0,
        "next_run_time": "2026-10-21 01:00:00",
    },
]

block = plugin._format_schedule(jobs, UMO)
check("contains wake task", "大肥鱼叫起床" in block)
check("contains 08:00", "每天 08:00" in block, block)
check("contains 23:30", "每天 23:30" in block, block)
check("contains one-time", "一次性，10-21 07:00" in block, block)
check("session isolation", "别人的任务" not in block, block)
check("agency line", "不要说自己没有定时任务" in block, block)
check("no other session", OTHER not in block, block)

check("utc to local", plugin._local_time("2026-10-21 00:00:00", "Asia/Shanghai") == "10-21 08:00")
check("cron human", plugin._human_cron("0 8 * * *") == "每天 08:00")
check("cron raw fallback", plugin._human_cron("0 8 * * 1-5").startswith("cron "))

# 真实 DB 路径可读时，确认能读到任务（不在部署环境时跳过）
if os.path.exists(mod.DB_PATH):
    loaded = plugin._load_jobs()
    check("db readable", isinstance(loaded, list))
else:
    print("SKIP db (not present)")

print()
if failures:
    print("结果：%d 项失败" % len(failures))
    sys.exit(1)
print("结果：全部通过 OK")
