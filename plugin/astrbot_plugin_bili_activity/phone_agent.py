"""Universal virtual-phone safety controller for Bilibili & Douyin (M1).

Wraps PhoneMCP observation and atomic actions behind:
  1. Single-lease concurrency (`PhoneGate`) so foreground chat immediately preempts background runs;
  2. Strict package allowlist (`tv.danmaku.bili`, `com.ss.android.ugc.aweme` by default)
     and hard-coded forbidden packages (system settings, package installer, payment/wallet apps);
  3. High-risk UI text guard (payment, password, uninstall, format, real-name auth) before tap/type;
  4. Automatic fallback from `get_ui_elements` (`uiautomator dump` fails on animated video screens)
     to verified `ScreenCapture` + optional `listen_audio` loopback transcription;
  5. Auditable `phone_observation` / `phone_action` episodes in `Store.life`.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .core import canonical, digest
from .transport import ScreenCapture, TransportError

BILI_PACKAGE = "tv.danmaku.bili"
DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"

# Strict default scope per owner requirement: only Bilibili & Douyin
DEFAULT_ALLOWED_PACKAGES = [BILI_PACKAGE, DOUYIN_PACKAGE]

# Launcher/system UI packages that are safe to observe transiently or return home to,
# but never to mutate system settings inside.
SAFE_TRANSIENT_PACKAGES = frozenset({
    "com.android.launcher",
    "com.android.launcher3",
    "com.ldmnq.launcher3",
    "com.cyanogenmod.trebuchet",
    "com.android.systemui",
})

# Hard-coded forbidden packages that cannot be added even via config
FORBIDDEN_PACKAGES = frozenset({
    "com.android.settings",
    "com.google.android.packageinstaller",
    "com.android.packageinstaller",
    "com.android.vending",
    "com.eg.android.AlipayGphone",
    "com.tencent.mm",
    "com.unionpay",
    "com.android.phone",
    "com.android.mms",
    "com.android.contacts",
    "eu.chainfire.supersu",
    "com.topjohnwu.magisk",
})

# High-risk UI text patterns that must never be tapped or confirmed autonomously
DANGEROUS_UI_RE = re.compile(
    r"确认支付|立即支付|立即付款|免密支付|开通会员|自动续费|指纹支付|密码支付|"
    r"输入密码|验证码登录|实名认证|身份证号|绑定银行卡|注销账号|删除账号|"
    r"卸载应用|确认卸载|恢复出厂|格式化|清除全部数据|Root权限|授权管理|允许安装未知应用",
    re.IGNORECASE,
)

APP_ALIASES = {
    "bili": BILI_PACKAGE,
    "bilibili": BILI_PACKAGE,
    "哔哩哔哩": BILI_PACKAGE,
    "b站": BILI_PACKAGE,
    "B站": BILI_PACKAGE,
    BILI_PACKAGE: BILI_PACKAGE,
    "douyin": DOUYIN_PACKAGE,
    "抖音": DOUYIN_PACKAGE,
    DOUYIN_PACKAGE: DOUYIN_PACKAGE,
}

ALLOWED_ACTIONS = frozenset({
    "launch_app",
    "tap",
    "tap_element",
    "swipe",
    "type_text",
    "press_key",
    "go_back",
    "go_home",
    "wait",
})

SAFE_KEYCODES = frozenset({
    "BACK", "HOME", "ENTER", "SEARCH", "SPACE", "DEL",
    "KEYCODE_BACK", "KEYCODE_HOME", "KEYCODE_ENTER", "KEYCODE_SEARCH",
    "3", "4", "66", "67", "84",
})

DEFAULTS = {
    "phone_free_explore_enabled": False,
    "phone_allowed_packages": list(DEFAULT_ALLOWED_PACKAGES),
    "phone_max_steps_per_run": 15,
    "phone_audio_listen_seconds": 8,
    "phone_tool_calls_per_turn": 40,
    "phone_screenshots_per_turn": 8,
}


def validate_config(cfg: dict[str, Any]) -> None:
    cfg = DEFAULTS | cfg
    if not isinstance(cfg["phone_free_explore_enabled"], bool):
        raise ValueError("phone_free_explore_enabled 必须是布尔开关")
    pkgs = cfg["phone_allowed_packages"]
    if not isinstance(pkgs, list) or not 1 <= len(pkgs) <= 10:
        raise ValueError("phone_allowed_packages 必须是非空应用包名数组（最多10个）")
    for pkg in pkgs:
        if not isinstance(pkg, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", pkg):
            raise ValueError(f"无效应用包名: {pkg!r}")
        if pkg in FORBIDDEN_PACKAGES:
            raise ValueError(f"禁止将高危系统或支付应用加入允许列表: {pkg}")
        if pkg not in set(DEFAULT_ALLOWED_PACKAGES):
            raise ValueError(f"应用 {pkg!r} 不在允许列表 {DEFAULT_ALLOWED_PACKAGES} 中（仅允许 B站 和 抖音）")
    for key, low, high in (("phone_max_steps_per_run", 1, 50), ("phone_audio_listen_seconds", 3, 20),
                           ("phone_tool_calls_per_turn", 1, 200), ("phone_screenshots_per_turn", 0, 50)):
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, int) or not low <= val <= high:
            raise ValueError(f"{key} 必须是 {low}–{high} 的整数")


def resolve_package(app_or_pkg: str, allowed_packages: list[str] | tuple[str, ...] = DEFAULT_ALLOWED_PACKAGES) -> str:
    key = (app_or_pkg or "").strip()
    pkg = APP_ALIASES.get(key) or APP_ALIASES.get(key.lower()) or key
    if pkg in FORBIDDEN_PACKAGES:
        raise PermissionError(f"应用 {pkg} 属于系统/支付禁区，禁止访问")
    if pkg not in set(allowed_packages):
        raise PermissionError(f"应用 {pkg!r} 不在允许列表 {list(allowed_packages)} 中（仅允许 B站 和 抖音）")
    return pkg


def _extract_mcp_text(result: Any) -> str:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            structured = structured["result"]
        return structured if isinstance(structured, str) else canonical(structured)
    parts = [
        getattr(item, "text", "")
        for item in getattr(result, "content", [])
        if getattr(item, "type", None) == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


def _parse_package_name(raw_app_info: Any) -> str:
    if isinstance(raw_app_info, dict):
        for k in ("package", "package_name", "packageName", "app"):
            if isinstance(raw_app_info.get(k), str) and raw_app_info[k].strip():
                return raw_app_info[k].strip()
        text = json.dumps(raw_app_info, ensure_ascii=False)
    else:
        text = str(raw_app_info or "").strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return _parse_package_name(parsed)
        except Exception:
            pass
    m = re.search(r"\b([a-z][a-z0-9_]*(?:\.[a-z0-9_]+){2,})\b", text, re.IGNORECASE)
    return m.group(1) if m else text[:120]


def _summarize_ui_elements(raw_ui: Any, limit: int = 30) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract compact interactive elements + detect any dangerous text on screen."""
    text = raw_ui if isinstance(raw_ui, str) else canonical(raw_ui)
    hazards = sorted(set(DANGEROUS_UI_RE.findall(text)))
    elements: list[dict[str, Any]] = []
    parsed = raw_ui
    if isinstance(raw_ui, str):
        try:
            parsed = json.loads(raw_ui)
        except Exception:
            parsed = None
    items = parsed if isinstance(parsed, list) else (parsed.get("elements") if isinstance(parsed, dict) else None)
    if isinstance(items, list):
        for idx, item in enumerate(items[:limit]):
            if not isinstance(item, dict):
                continue
            label = (item.get("text") or item.get("content_desc") or item.get("description") or "").strip()
            bounds = item.get("bounds") or item.get("center") or item.get("rect")
            if label or item.get("clickable"):
                elements.append({
                    "index": item.get("index", idx),
                    "text": label[:80],
                    "resource_id": str(item.get("resource_id") or item.get("id") or "")[:60],
                    "clickable": bool(item.get("clickable", True)),
                    "bounds": bounds,
                })
    elif text:
        for line in text.splitlines()[:limit]:
            line = line.strip()
            if line:
                elements.append({"text": line[:100]})
    return elements, hazards


class PhoneAgent:
    """High-level guarded phone observer & actor sharing `PhoneGate`."""

    def __init__(self, store, gate, phone_client_factory, screen_folder, device_id: str = "emulator-5554"):
        self.store = store
        self.gate = gate
        self.phone_client_factory = phone_client_factory
        self.screen_folder = screen_folder
        self.device_id = device_id
        self._run_steps: dict[str, int] = {}

    async def _call(self, name: str, args: dict[str, Any] | None = None, owner: str | None = None):
        payload = {"device_id": self.device_id, **(args or {})}
        return await self.gate.call(self.phone_client_factory(), name, payload, owner=owner)

    async def current_package(self, owner: str | None = None) -> str:
        res = await self._call("get_current_app", {}, owner=owner)
        return _parse_package_name(_extract_mcp_text(res))

    async def enforce_package_guard(
        self,
        allowed_packages: list[str] | tuple[str, ...],
        *,
        owner: str | None = None,
        allow_launcher: bool = True,
    ) -> dict[str, Any]:
        pkg = await self.current_package(owner=owner)
        allowed = set(allowed_packages)
        if pkg in FORBIDDEN_PACKAGES:
            await self._call("go_home", {}, owner=owner)
            raise PermissionError(f"检测到手机处于禁区应用 {pkg}，已自动退回桌面并中止操作")
        if pkg and pkg not in allowed and not (allow_launcher and pkg in SAFE_TRANSIENT_PACKAGES):
            await self._call("go_home", {}, owner=owner)
            raise PermissionError(f"当前前台应用 {pkg} 不在允许范围 {sorted(allowed)} 内，已自动退回桌面")
        return {"package": pkg, "allowed": pkg in allowed}

    async def observe(
        self,
        scope: str,
        cfg: dict[str, Any],
        *,
        owner: str | None = None,
        run_id: str | None = None,
        goal_id: str | None = None,
        include_screenshot: bool = True,
        listen_seconds: int = 0,
    ) -> dict[str, Any]:
        """Observe current app, UI tree (with automatic screenshot fallback), and optional audio."""
        allowed = cfg.get("phone_allowed_packages") or DEFAULT_ALLOWED_PACKAGES
        guard_info = await self.enforce_package_guard(allowed, owner=owner, allow_launcher=True)
        pkg = guard_info["package"]

        ui_status = "ok"
        elements: list[dict[str, Any]] = []
        hazards: list[str] = []
        try:
            ui_res = await self._call("get_ui_elements", {}, owner=owner)
            ui_text = _extract_mcp_text(ui_res)
            if "could not get idle state" in ui_text.lower() or "null root node" in ui_text.lower():
                ui_status = "dynamic_screen_fallback"
            else:
                elements, hazards = _summarize_ui_elements(ui_text)
        except TransportError:
            ui_status = "ui_dump_failed_fallback"

        snapshot_meta = None
        if include_screenshot or ui_status != "ok":
            capture = ScreenCapture(
                self.phone_client_factory(),
                self.screen_folder,
                device_id=self.device_id,
                lock=self.gate,
            )
            snap = await capture.capture(owner=owner)
            snapshot_meta = snap.metadata()

        audio_transcript = None
        if listen_seconds > 0:
            secs = max(3, min(int(listen_seconds), int(cfg.get("phone_audio_listen_seconds", 8))))
            try:
                audio_res = await self._call("listen_audio", {"seconds": secs}, owner=owner)
                audio_transcript = _extract_mcp_text(audio_res)[:500]
            except TransportError:
                audio_transcript = None

        observation = {
            "ok": True,
            "package": pkg,
            "ui_status": ui_status,
            "elements_count": len(elements),
            "elements": elements[:25],
            "hazards": hazards,
            "snapshot": snapshot_meta,
            "audio": audio_transcript,
            "observed_at": time.time(),
        }
        source_key = digest([
            "phone_observe",
            pkg,
            snapshot_meta["fingerprint"] if snapshot_meta else ui_status,
            int(observation["observed_at"]),
        ])
        episode = self.store.life.record_episode(
            scope,
            kind="phone_observation",
            source_id=source_key,
            evidence={
                "ok": True,
                "operation": "phone_observe",
                "package": pkg,
                "ui_status": ui_status,
                "fingerprint": snapshot_meta["fingerprint"] if snapshot_meta else None,
                "result_excerpt": canonical({
                    "package": pkg,
                    "elements_sample": elements[:8],
                    "audio": audio_transcript,
                })[:1200],
            },
            interpretation={
                "summary": f"观察手机界面 {pkg}（UI:{ui_status}，元素:{len(elements)}）",
            },
            run_id=run_id,
            goal_id=goal_id,
            subject_kind="agent",
            subject_id=pkg or "phone",
        )
        observation["episode_id"] = episode["id"]
        return observation

    async def act(
        self,
        scope: str,
        cfg: dict[str, Any],
        action: str,
        params: dict[str, Any] | None = None,
        *,
        owner: str | None = None,
        run_id: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single guarded phone action within allowed apps."""
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"不支持的手机动作 {action!r}，允许动作：{sorted(ALLOWED_ACTIONS)}")
        params = dict(params or {})
        screen_width = int(cfg.get("phone_screen_width", 720))
        screen_height = int(cfg.get("phone_screen_height", 1280))
        allowed = cfg.get("phone_allowed_packages") or DEFAULT_ALLOWED_PACKAGES
        if run_id:
            max_steps = int(cfg.get("phone_max_steps_per_run", 15))
            used = self._run_steps.get(run_id, 0)
            if used >= max_steps:
                raise PermissionError(f"本轮手机操作步数已达上限 ({max_steps})")
            self._run_steps[run_id] = used + 1

        if action == "launch_app":
            target_pkg = resolve_package(str(params.get("app") or params.get("package") or ""), allowed)
            res = await self._call("launch_app", {"package_name": target_pkg, "app_name": target_pkg}, owner=owner)
            detail = {"action": "launch_app", "package": target_pkg, "result": _extract_mcp_text(res)[:300]}
            self.store.event(scope, "phone_action", {"run_id": run_id, **detail})
            return {"ok": True, **detail}

        if action == "go_home":
            res = await self._call("go_home", {}, owner=owner)
            return {"ok": True, "action": "go_home", "result": _extract_mcp_text(res)[:200]}

        if action == "go_back":
            await self.enforce_package_guard(allowed, owner=owner, allow_launcher=True)
            res = await self._call("go_back", {}, owner=owner)
            return {"ok": True, "action": "go_back", "result": _extract_mcp_text(res)[:200]}

        if action == "wait":
            secs = max(1, min(int(params.get("seconds", 2)), 10))
            res = await self._call("wait", {"seconds": secs}, owner=owner)
            return {"ok": True, "action": "wait", "seconds": secs, "result": _extract_mcp_text(res)[:200]}

        # All interactive actions below require the current foreground app to be in the allowlist
        guard_info = await self.enforce_package_guard(allowed, owner=owner, allow_launcher=False)
        pkg = guard_info["package"]

        # Pre-action UI safety check when tapping or typing
        if action in {"tap", "tap_element", "type_text"}:
            target_label = str(params.get("text") or params.get("element_text") or "")
            if DANGEROUS_UI_RE.search(target_label):
                raise PermissionError(f"拦截高危操作目标：{target_label!r}")
            try:
                ui_res = await self._call("get_ui_elements", {}, owner=owner)
                _elems, hazards = _summarize_ui_elements(_extract_mcp_text(ui_res))
                if hazards:
                    raise PermissionError(f"当前屏幕检测到高危确认项 {hazards}，已拒绝自动点击/输入")
            except TransportError:
                pass

        if action == "tap":
            x, y = int(params["x"]), int(params["y"])
            if not (0 <= x < screen_width and 0 <= y < screen_height):
                raise ValueError(
                    f"点击坐标 ({x},{y}) 超出模拟器屏幕范围 {screen_width}x{screen_height}")
            res = await self._call("tap", {"x": x, "y": y}, owner=owner)
            out = {"ok": True, "action": "tap", "package": pkg, "x": x, "y": y, "result": _extract_mcp_text(res)[:200]}
        elif action == "tap_element":
            idx = params.get("index")
            text = str(params.get("text") or "").strip()
            call_args = {"index": int(idx)} if idx is not None else {"text": text[:80]}
            res = await self._call("tap_element", call_args, owner=owner)
            out = {"ok": True, "action": "tap_element", "package": pkg, "target": call_args, "result": _extract_mcp_text(res)[:200]}
        elif action == "swipe":
            if all(k in params for k in ("x1", "y1", "x2", "y2")):
                coords = {k: int(params[k]) for k in ("x1", "y1", "x2", "y2")}
            else:
                direction = str(params.get("direction") or "up").lower()
                dir_map = {
                    "up": (360, 1020, 360, 260),
                    "down": (360, 260, 360, 1020),
                    "left": (620, 640, 100, 640),
                    "right": (100, 640, 620, 640),
                }
                if direction not in dir_map:
                    raise ValueError(f"不支持的滑动方向 {direction!r}")
                x1, y1, x2, y2 = dir_map[direction]
                coords = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            for key, limit in (("x1", screen_width), ("x2", screen_width),
                               ("y1", screen_height), ("y2", screen_height)):
                if not 0 <= coords[key] < limit:
                    raise ValueError(
                        f"滑动坐标 {key}={coords[key]} 超出模拟器屏幕范围 {screen_width}x{screen_height}")
            duration = max(100, min(int(params.get("duration_ms", 400)), 3000))
            res = await self._call("swipe", {**coords, "duration": duration}, owner=owner)
            out = {"ok": True, "action": "swipe", "package": pkg, **coords, "result": _extract_mcp_text(res)[:200]}
        elif action == "type_text":
            text = str(params.get("text") or "").strip()
            if not text or len(text) > 300:
                raise ValueError("输入文本不能为空且不超过300字")
            res = await self._call("type_text", {"text": text}, owner=owner)
            out = {"ok": True, "action": "type_text", "package": pkg, "length": len(text), "result": _extract_mcp_text(res)[:200]}
        elif action == "press_key":
            key = str(params.get("key") or "BACK").upper()
            if key not in SAFE_KEYCODES:
                raise ValueError(f"按键 {key!r} 不在安全按键列表中")
            res = await self._call("press_key", {"key": key}, owner=owner)
            out = {"ok": True, "action": "press_key", "package": pkg, "key": key, "result": _extract_mcp_text(res)[:200]}
        else:
            raise ValueError(f"未知动作 {action}")

        # Post-action check: ensure we didn't jump into a forbidden app (e.g. external ad link)
        await self.enforce_package_guard(allowed, owner=owner, allow_launcher=True)
        self.store.event(scope, "phone_action", {"run_id": run_id, "goal_id": goal_id, **out})
        return out
