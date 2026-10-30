"""Optional read-only room sources, configured only at server startup."""
import json
import os
from pathlib import Path

from .activity_reader import ActivityError, read_activity
from .livingmemory import SSHMemorySource


class LocalActivitySource:
    def __init__(self, folder):
        self.folder = Path(folder)

    def load(self):
        return read_activity(self.folder)


class SSHActivitySource(SSHMemorySource):
    def load(self):
        script = Path(__file__).with_name("activity_reader.py").read_text(encoding="utf-8")
        try:
            result = self.run(self.command, input=script, capture_output=True, text=True, encoding="utf-8",
                errors="strict", timeout=20, **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            if result.returncode or len(result.stdout.encode()) > 256 * 1024:
                raise ValueError()
            data = json.loads(result.stdout)
            if data.get("ok") is not True or data["snapshot"].get("schema") != "dafeiyu-room/v1":
                raise ValueError()
            return data["snapshot"]
        except Exception:
            raise ActivityError("活动账本同步失败；请检查 SSH 与表结构。") from None


def empty_room(state="unconfigured"):
    return {"schema": "dafeiyu-room/v1", "mode": "unavailable", "connection": state, "as_of": None,
        "scope_label": "活动来源尚未连接", "state": "unknown", "zone": "window", "flags": {},
        "paused": False, "goals": [], "works": [], "decisions": [], "contacts": [], "pending": 0,
        "notice": "没有活动记录可供判断。房间布景不表示她正在生活、休息或在线。"}


def demo_room():
    at = 1793356200
    return {"schema": "dafeiyu-room/v1", "mode": "demo", "connection": "demo", "as_of": at,
        "scope_label": "虚构演示 · 不是真实活动", "state": "ended", "zone": "desk",
        "flags": {"enabled": True, "life_enabled": True, "life_decision_enabled": True, "periodic_enabled": False},
        "paused": False, "pending": 0,
        "goals": [{"id": "demo-goal", "title": "把海里的微光，写成一封信", "why": "想弄清海洋发光生物怎样找到彼此，再整理成自己的小笔记。",
            "status": "paused", "next_step": "核对第二份资料，再补上那段关于光的解释。", "updated_at": at - 200}],
        "works": [{"id": "demo-work", "title": "深海来信", "phase": "write", "status": "draft", "revision": 2, "updated_at": at - 200}],
        "decisions": [{"id": "demo-3", "at": at - 900, "action": "writing", "reason": "昨天留下的资料还在，想把没写完的小笔记接着写。", "goal_id": "demo-goal", "topic": "深海微光", "run_status": "completed", "started": at - 900, "ended": at - 200},
            {"id": "demo-2", "at": at - 8000, "action": "rest", "reason": "刚才的问题还没有新资料，先留一点安静。", "goal_id": "", "topic": "", "run_status": "rested", "started": None, "ended": None},
            {"id": "demo-1", "at": at - 86000, "action": "research", "reason": "对海里的光有点好奇，先看看它从哪里来。", "goal_id": "demo-goal", "topic": "深海微光", "run_status": "completed", "started": at - 86000, "ended": at - 85600}],
        "contacts": [{"source": "life", "kind": "thought", "status": "sent", "created": at - 150}],
        "notice": "场景、目标、作品与足迹均为虚构演示。布景与轻微动画不是她的情绪、意识或真实在线状态。"}
