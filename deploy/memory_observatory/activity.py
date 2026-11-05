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
        "projects": {"state": "unavailable", "enabled": False, "items": [], "total": 0, "truncated": False},
        "notice": "没有活动记录可供判断。房间布景不表示她正在生活、休息或在线。"}


def demo_room():
    at = 1793356200
    return {"schema": "dafeiyu-room/v1", "mode": "demo", "connection": "demo", "as_of": at,
        "scope_label": "虚构演示 · 不是真实活动", "state": "ended", "zone": "desk",
        "flags": {"enabled": True, "life_enabled": True, "life_decision_enabled": True, "periodic_enabled": False, "life_rhythm_enabled": True},
        "paused": False, "pending": 0,
        "goals": [{"id": "demo-goal", "title": "把海里的微光，写成一封信", "why": "想弄清海洋发光生物怎样找到彼此，再整理成自己的小笔记。",
            "status": "paused", "next_step": "核对第二份资料，再补上那段关于光的解释。", "updated_at": at - 200}],
        "works": [{"id": "demo-work", "title": "深海来信", "phase": "write", "status": "draft", "revision": 2, "updated_at": at - 200}],
        "decisions": [{"id": "demo-3", "at": at - 900, "action": "writing", "reason": "昨天留下的资料还在，想把没写完的小笔记接着写。", "goal_id": "demo-goal", "topic": "深海微光", "run_status": "completed", "started": at - 900, "ended": at - 200,
            "rhythm": {"phase": "day", "at": at - 900, "reason": "这个时段可以继续研究，或跟着自己的兴趣找一个小问题。", "recent_activity_minutes": 12, "window_minutes": 120, "limit_minutes": 20, "next_eligible_at": None}},
            {"id": "demo-2", "at": at - 8000, "action": "rest", "reason": "刚才的问题还没有新资料，先留一点安静。", "goal_id": "", "topic": "", "run_status": "rested", "started": None, "ended": None},
            {"id": "demo-1", "at": at - 86000, "action": "research", "reason": "对海里的光有点好奇，先看看它从哪里来。", "goal_id": "demo-goal", "topic": "深海微光", "run_status": "completed", "started": at - 86000, "ended": at - 85600}],
        "contacts": [{"source": "life", "kind": "thought", "status": "sent", "created": at - 150}],
        "projects": {"state": "ready", "enabled": True, "total": 2, "truncated": False, "items": [
            {"id": "demo-project-light", "title": "收集海里的光", "why": "从一小束微光出发，慢慢弄清深海生物怎样找到彼此。",
             "status": "active", "max_stages": 3, "updated": at-200, "stages": [
                {"position": 1, "title": "光从哪里来", "goal_status": "completed", "next_step": "", "updated": at-172800,
                 "work": {"title": "微光的第一封信", "revision": 2, "excerpt": "【虚构演示作品】\n\n资料观察\n这份演示笔记从发光生物的例子开始。\n\n我的想法\n想把每一种光放回它生活的地方，看看它与周围有什么联系。\n\n待核实\n演示中的表述并未查证，不作为海洋知识。", "excerpt_truncated": False, "source_count": 2, "sources_available": True},
                 "review_state": "recorded", "review": {"revision": 2, "reflection": "把零散的观察整理到了一起，也发现自己更好奇光在交流中的作用。", "limitations": "这里只有虚构示例，还缺少独立资料和反例。", "next_question": "没有光的时候，它们怎样找到彼此？", "created": at-86400, "truncated": False}},
                {"position": 2, "title": "没有光的时候", "goal_status": "paused", "next_step": "先比较两份资料，再决定下一封信写什么。", "updated": at-200,
                 "work": {"title": "深海来信", "revision": 2, "excerpt": "【虚构演示草稿】\n\n窗边留了一张便笺：把还没弄清的问题也留下，不急着填上答案。", "excerpt_truncated": False, "source_count": 1, "sources_available": True},
                 "review": None, "review_state": "none"}]},
            {"id": "demo-project-rain", "title": "一页雨声笔记", "why": "把读过的片段整理成一页有出处的小笔记。",
             "status": "completed", "max_stages": 2, "updated": at-260000, "stages": [
                {"position": 1, "title": "给雨声留一页", "goal_status": "completed", "next_step": "", "updated": at-350000,
                 "work": {"title": "雨声旁边的留白", "revision": 1, "excerpt": "【虚构演示作品】\n\n记下阅读时留下的疑问。文字可以留白，来源不能凭空补齐。", "excerpt_truncated": False, "source_count": 1, "sources_available": True},
                 "review_state": "recorded", "review": {"revision": 1, "reflection": "这一页先写到这里，暂时没有需要延伸的问题。", "limitations": "这是界面示例，不代表她真的听雨或经历过这些。", "next_question": "", "created": at-260000, "truncated": False}}]}]},
        "notice": "场景、目标、作品与足迹均为虚构演示。布景与轻微动画不是她的情绪、意识或真实在线状态。"}
