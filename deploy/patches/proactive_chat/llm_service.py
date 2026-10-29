"""LLM 调用与提示词构建服务。

由旧 core/llm_adapter.py 的提示词与 Provider 调用逻辑等价搬迁而来。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from astrbot.api import logger

from ...utils.time_utils import format_current_time
from ..adapters.astrbot_event import dispatch_event_hook
from ..adapters.astrbot_provider import build_provider_request, invoke_provider
from .contact_bridge import preflight

try:  # pragma: no cover - 取决于 AstrBot 版本
    from astrbot.core.star.star_handler import EventType
except ImportError:  # pragma: no cover
    EventType = None  # type: ignore[assignment]


class LlmService:
    """提示词构建与 LLM 调用服务。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    # ------------------------------------------------------------------
    # 动态内容块
    # ------------------------------------------------------------------
    def build_dynamic_context_text(
        self,
        unanswered_count: int,
        session_config: dict | None = None,
        *,
        include_time: bool = True,
        include_unanswered: bool = True,
    ) -> str:
        """构建每轮都会变化的运行时上下文块。"""
        lines: list[str] = []

        if include_time:
            lines.append(f"- 当前时间：{format_current_time(self.plugin.timezone)}")
        if include_unanswered:
            lines.append(f"- 本次主动消息的未回复累计次数：{unanswered_count}")

        session_type = ""
        if isinstance(session_config, dict):
            session_type = str(session_config.get("_session_type") or "")
        type_label = {"friend": "私聊", "group": "群聊"}.get(session_type, "")
        if type_label:
            lines.append(f"- 会话场景：{type_label}")

        if not lines:
            return ""

        return "<dynamic_context>\n" + "\n".join(lines) + "\n</dynamic_context>"

    def build_extra_content_parts(
        self,
        platform_context: str,
        prompt_template: str,
        unanswered_count: int,
        session_config: dict | None = None,
    ) -> list:
        """构建追加在本轮用户消息之后的临时内容块列表。"""
        parts: list = []
        if platform_context:
            part = self.plugin.context_service.make_temp_text_part(platform_context)
            if part is not None:
                parts.append(part)

        dynamic_text = self.build_dynamic_context_text(
            unanswered_count,
            session_config,
            include_time="{{current_time}}" not in prompt_template,
            include_unanswered="{{unanswered_count}}" not in prompt_template,
        )
        if dynamic_text:
            dynamic_part = self.plugin.context_service.make_temp_text_part(dynamic_text)
            if dynamic_part is not None:
                parts.append(dynamic_part)

        return parts

    # ------------------------------------------------------------------
    # 钩子派发
    # ------------------------------------------------------------------
    async def dispatch_llm_request_hooks(self, event: Any, req: Any) -> bool:
        """派发 LLM 请求前置钩子。"""
        if event is None or EventType is None:
            return False

        await dispatch_event_hook(event, EventType.OnWaitingLLMRequestEvent)
        if event.is_stopped():
            return True

        return await dispatch_event_hook(event, EventType.OnLLMRequestEvent, req)

    async def dispatch_llm_response_hooks(self, event: Any, resp: Any) -> bool:
        """派发 LLM 响应后置钩子。"""
        if event is None or EventType is None or resp is None:
            return False
        return await dispatch_event_hook(event, EventType.OnLLMResponseEvent, resp)

    # ------------------------------------------------------------------
    # Provider 解析
    # ------------------------------------------------------------------
    async def resolve_chat_provider(self, session_id: str) -> Any:
        """解析用于本轮请求的 Provider 实例。"""
        plugin = self.plugin
        provider_id = None
        try:
            provider_id = await plugin.context.get_current_chat_provider_id(session_id)
        except Exception as e:
            logger.warning(f"[主动消息] 获取当前对话 Provider 失败喵: {e}")

        if provider_id:
            try:
                provider = await plugin.context.provider_manager.get_provider_by_id(
                    provider_id
                )
                if provider:
                    return provider
            except Exception as e:
                logger.warning(f"[主动消息] 按 ID 获取 Provider 失败喵: {e}")

        try:
            return plugin.context.get_using_provider(umo=session_id)
        except Exception as e:
            logger.warning(f"[主动消息] 回退获取 Provider 失败喵: {e}")
            return None

    # ------------------------------------------------------------------
    # 工具调用（主动消息补丁：让主动开口也能联网搜索）
    # ------------------------------------------------------------------
    async def attach_web_search_tools(self, req: Any, session_id: str) -> None:
        """按会话配置给主动消息请求挂上 AstrBot 内置网页搜索工具。"""
        try:
            from types import SimpleNamespace

            from astrbot.core.astr_main_agent import _apply_web_search_tools

            # 主动消息没有 AstrMessageEvent，这里只需 unified_msg_origin
            # 让 AstrBot 读取该 QQ 会话实际使用的 provider_settings。
            fake_event = SimpleNamespace(unified_msg_origin=session_id)
            await _apply_web_search_tools(fake_event, req, self.plugin.context)
            func_tool = getattr(req, "func_tool", None)
            if func_tool and not func_tool.empty():
                logger.info(
                    f"[主动消息] 已挂载工具: {', '.join(func_tool.names())}"
                )
        except Exception as e:
            logger.warning(f"[主动消息] 挂载网页搜索工具失败喵: {e}")

    @staticmethod
    def _tool_result_to_text(result: Any) -> str:
        """把工具返回值统一转成可回填给模型的文本。"""
        if isinstance(result, str):
            return result
        content = getattr(result, "content", None)
        if content is not None:
            if isinstance(content, str):
                return content
            try:
                return json.dumps(content, ensure_ascii=False)
            except Exception:
                return str(content)
        return str(result)

    async def run_tool_loop(
        self,
        provider: Any,
        req: Any,
        llm_response: Any,
        session_id: str,
        history_messages: list,
        prompt: str,
        extra_parts: list,
        max_rounds: int = 3,
    ) -> Any:
        """执行模型请求的工具调用，并携带结果重问，最多 max_rounds 轮。"""
        func_tool = getattr(req, "func_tool", None)
        if not func_tool or func_tool.empty():
            return llm_response

        try:
            from astrbot.core.agent.message import (
                AssistantMessageSegment,
                ToolCall,
                ToolCallMessageSegment,
            )
        except Exception as e:  # pragma: no cover - 旧版本 AstrBot 兼容
            logger.warning(f"[主动消息] 当前 AstrBot 不支持工具消息结构，跳过搜索喵: {e}")
            return llm_response

        # 重问时不再重复发送 prompt，而是把“历史 + 本轮用户消息 + 工具结果”
        # 一起作为 contexts，避免提示词重复。
        messages: list[Any] = list(history_messages or [])
        user_text = prompt
        extra_texts = [
            str(getattr(part, "text", ""))
            for part in (extra_parts or [])
            if getattr(part, "text", None)
        ]
        if extra_texts:
            user_text = user_text + "\n\n" + "\n\n".join(extra_texts)
        messages.append({"role": "user", "content": user_text})

        for round_index in range(max_rounds):
            names = list(getattr(llm_response, "tools_call_name", None) or [])
            if not names:
                return llm_response
            args_list = list(getattr(llm_response, "tools_call_args", None) or [])
            ids = list(getattr(llm_response, "tools_call_ids", None) or [])
            tool_calls = []
            result_segments = []

            for idx, name in enumerate(names):
                args = args_list[idx] if idx < len(args_list) else {}
                call_id = ids[idx] if idx < len(ids) else f"call_{round_index}_{idx}"
                if not isinstance(args, dict):
                    args = {}
                tool = func_tool.get_tool(name)
                if not tool:
                    result_text = f"error: Tool {name} not found."
                else:
                    try:
                        expected = (
                            getattr(tool, "parameters", None) or {}
                        ).get("properties", {})
                        valid_args = (
                            {k: v for k, v in args.items() if k in expected}
                            if expected
                            else dict(args)
                        )
                        from types import SimpleNamespace

                        agent_ctx = SimpleNamespace(
                            context=self.plugin.context,
                            event=SimpleNamespace(unified_msg_origin=session_id),
                            extra={},
                        )
                        tool_ctx = SimpleNamespace(
                            context=agent_ctx,
                            messages=[],
                            tool_call_timeout=120,
                        )
                        result = await tool.call(tool_ctx, **valid_args)
                        result_text = self._tool_result_to_text(result)
                    except Exception as e:
                        logger.error(f"[主动消息] 工具 {name} 执行失败喵: {e}")
                        result_text = f"error: {e}"

                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        function=ToolCall.FunctionBody(
                            name=name,
                            arguments=json.dumps(args, ensure_ascii=False),
                        ),
                    )
                )
                result_segments.append(
                    ToolCallMessageSegment(content=result_text, tool_call_id=call_id)
                )
                logger.info(
                    f"[主动消息] 工具 {name} 执行完成，结果长度: {len(result_text)}"
                )

            messages.append(
                AssistantMessageSegment(
                    content=None, tool_calls=tool_calls
                ).model_dump()
            )
            messages.extend(segment.model_dump() for segment in result_segments)

            requery = build_provider_request(
                prompt=None,
                session_id=session_id,
                contexts=messages,
                system_prompt=getattr(req, "system_prompt", ""),
                extra_parts=[],
                conversation=getattr(req, "conversation", None),
                func_tool=func_tool,
            )
            try:
                llm_response = await invoke_provider(provider, requery)
                logger.info("[主动消息] 携带工具结果重问 LLM 成功喵。")
            except Exception as e:
                logger.error(f"[主动消息] 携带工具结果重问 LLM 失败喵: {e}")
                return llm_response

        return llm_response

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    async def generate_llm_response(
        self,
        session_id: str,
        session_config: dict,
        history_messages: list,
        system_prompt: str,
        unanswered_count: int,
        event: Any = None,
        conversation: Any = None,
        platform_context: str = "",
    ) -> tuple[Any | None, str]:
        """统一 LLM 调用入口。"""
        plugin = self.plugin
        if not preflight(plugin, session_id):
            return None, ""
        motivation_template = session_config.get("proactive_prompt", "") or ""
        now_str = format_current_time(plugin.timezone)
        final_user_simulation_prompt = motivation_template.replace(
            "{{unanswered_count}}", str(unanswered_count)
        ).replace("{{current_time}}", now_str)

        logger.debug("[主动消息] 已生成包含动机和时间的 Prompt 喵。")

        history_messages = plugin.context_service.sanitize_history_content(
            history_messages or []
        )
        extra_parts = self.build_extra_content_parts(
            platform_context=platform_context,
            prompt_template=motivation_template,
            unanswered_count=unanswered_count,
            session_config=session_config,
        )

        provider = await self.resolve_chat_provider(session_id)
        if not provider:
            logger.warning("[主动消息] 未找到 LLM Provider，放弃并重新调度喵。")
            return None, final_user_simulation_prompt

        req = build_provider_request(
            prompt=final_user_simulation_prompt,
            session_id=session_id,
            contexts=history_messages,
            system_prompt=system_prompt,
            extra_parts=extra_parts,
            conversation=conversation,
        )
        await self.attach_web_search_tools(req, session_id)

        try:
            stopped = await self.dispatch_llm_request_hooks(event, req)
        except Exception as e:
            logger.error(f"[主动消息] 派发 LLM 前置钩子失败喵: {e}")
            stopped = False
        if stopped:
            logger.info("[主动消息] LLM 前置钩子终止了事件传播，放弃本次请求喵。")
            return None, final_user_simulation_prompt

        llm_response_obj = None
        try:
            llm_response_obj = await invoke_provider(provider, req)
            llm_response_obj = await self.run_tool_loop(
                provider,
                req,
                llm_response_obj,
                session_id,
                history_messages,
                final_user_simulation_prompt,
                extra_parts,
            )
            logger.info("[主动消息] 调用 LLM 成功喵。")
            if plugin.telemetry and plugin.telemetry.enabled:
                plugin._track_task(
                    asyncio.create_task(
                        plugin.telemetry.track_feature(
                            "llm_generate_result",
                            {
                                "provider_mode": "provider_request",
                                "success": True,
                                "history_count": len(history_messages),
                                "extra_part_count": len(req.extra_user_content_parts),
                            },
                        )
                    )
                )
        except Exception as llm_error:
            logger.error(f"[主动消息] 调用 LLM 失败喵: {llm_error}")
            logger.info(f"[主动消息] 错误类型喵: {type(llm_error).__name__}")
            if plugin.telemetry and plugin.telemetry.enabled:
                plugin._track_task(
                    asyncio.create_task(
                        plugin.telemetry.track_error(
                            llm_error,
                            module="core.llm_adapter._generate_llm_response",
                        )
                    )
                )
            return None, final_user_simulation_prompt

        if llm_response_obj is None:
            logger.warning("[主动消息] LLM 返回空响应，重新调度喵。")
            return None, final_user_simulation_prompt

        response_stopped = False
        try:
            response_stopped = await self.dispatch_llm_response_hooks(
                event, llm_response_obj
            )
        except Exception as e:
            logger.error(f"[主动消息] 派发 LLM 后置钩子失败喵: {e}")

        if response_stopped:
            logger.info("[主动消息] LLM 后置钩子终止了事件传播，放弃本次生成结果喵。")
            return None, final_user_simulation_prompt

        response_text = self.extract_response_text(llm_response_obj)
        if not response_text:
            if getattr(llm_response_obj, "result_chain", None):
                logger.info("[主动消息] 生成结果无文本但有消息链，继续后续流程喵。")
                return llm_response_obj, self.resolve_final_user_prompt(
                    req, final_user_simulation_prompt
                )
            logger.warning("[主动消息] LLM 调用失败或返回空内容，重新调度喵。")
            if plugin.telemetry and plugin.telemetry.enabled:
                plugin._track_task(
                    asyncio.create_task(
                        plugin.telemetry.track_feature(
                            "llm_generate_result",
                            {
                                "provider_mode": "unknown",
                                "success": False,
                                "history_count": len(history_messages),
                            },
                        )
                    )
                )
            return None, final_user_simulation_prompt

        if response_text == "[object Object]":
            logger.error(
                "[主动消息] 喵呜！LLM 返回了意料之外的 '[object Object]' 字符串喵！"
            )
            logger.warning(
                "[主动消息] 这通常是因为上下文或 Prompt 中包含了无法解析的对象喵。已拦截本次发送喵。"
            )
            return None, final_user_simulation_prompt

        logger.info(f"[主动消息] LLM 已生成文本喵，长度: {len(response_text)}。")
        if plugin.telemetry and plugin.telemetry.enabled:
            plugin._track_task(
                asyncio.create_task(
                    plugin.telemetry.track_feature(
                        "llm_response_ready",
                        {
                            "response_length": len(response_text),
                            "session_type": session_config.get(
                                "_session_type", "unknown"
                            ),
                        },
                    )
                )
            )
        return llm_response_obj, self.resolve_final_user_prompt(
            req, final_user_simulation_prompt
        )

    @staticmethod
    def resolve_final_user_prompt(req: Any, fallback: str) -> str:
        """取钩子处理后的最终用户提示词。"""
        prompt = getattr(req, "prompt", None)
        if isinstance(prompt, str) and prompt.strip():
            return prompt
        return fallback

    @staticmethod
    def extract_response_text(llm_response_obj: Any) -> str:
        """从 LLM 响应中安全提取纯文本。"""
        if llm_response_obj is None:
            return ""
        try:
            text = llm_response_obj.completion_text
        except Exception:
            text = None
        if text is None:
            return ""
        return str(text).strip()

    @staticmethod
    def extract_response_chain(llm_response_obj: Any) -> list:
        """从 LLM 响应中安全提取消息链组件列表。"""
        if llm_response_obj is None:
            return []
        chain_obj = getattr(llm_response_obj, "result_chain", None)
        if chain_obj is None:
            return []
        chain = getattr(chain_obj, "chain", None)
        if chain is None:
            return []
        try:
            return [comp for comp in chain if comp is not None]
        except Exception:
            return []
