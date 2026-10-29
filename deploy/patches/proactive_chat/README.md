# proactive_chat 联网与主动联系协调补丁

对应插件：`astrbot_plugin_proactive_chat`（AGPL-3.0）。旧文档标注 v1.2.6，
但上游该标签的目录结构与本补丁不同；现在按具体源码哈希校验，不以版本号代替兼容性检查。
目标：让**主动消息**也能调用 AstrBot 内置网页搜索（本机配置为 AnySearch 匿名额度），
而不是只会接着旧话题开口。

## 为什么需要补丁

插件原本直接构造 `ProviderRequest` 调 `provider.text_chat()`：

- 没有把 `func_tool` 放进请求，模型手里根本没有搜索工具；
- 即使挂上工具，也没有“执行工具 → 带结果重问”的循环，模型返回 tool_call 后拿不到正文。

## 补丁内容

- `astrbot_provider.py`
  - `build_provider_request()` 新增可选 `func_tool` 参数；
  - `invoke_provider()` 透传 `tool_calls_result`（兼容旧 Provider 的签名裁剪逻辑保持不变）。
- `llm_service.py`
  - `attach_web_search_tools()`：复用 AstrBot 的 `_apply_web_search_tools()`，
    按当前 QQ 会话的 `provider_settings` 挂载搜索工具；
  - `run_tool_loop()`：执行模型请求的工具调用，把结果作为 `contexts` 重问，
    最多 3 轮；最终仍只把正文交给主动消息发送流程。

搜索是否真的调用由模型决定。生产提示词末尾已加一句“想聊新鲜话题时可先联网搜索”，
不强制每条主动消息都搜。

## 联系协调 v0.1

`contact_bridge.py` 连接生活插件的共享账本，默认关闭；`llm_service.py` 生成前预检，
`sender_service.py` 在整次发送前预留、每次派发前复查。TTS/分段只算一次联系。
结果只记社区发送器报告，不冒充独立 QQ 回读。原内部传输兜底逻辑保留。
配置、状态语义、限制和真实 smoke 见 [CONTACT_V01.md](../../bili_activity/CONTACT_V01.md)。

sender 源码固定于[上游提交 2dab585](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/tree/2dab58514e0a2055400ed3bf7b3907ae684c0c54)。
安装器要求 sender 符合该原始文件，LLM/provider 已有本仓库旧联网补丁（或文件已是本次版本）。
这是指定基线的增量安装器，不是适配任意社区版本的新装器。

## 安装 / 升级后检查

以下由主人后续操作，本轮未部署。先确认标准 AstrBot data/plugins 与 plugin_data 布局。
默认只检查；版本不匹配时不生成备份、不修改目标文件。不要绕过哈希检查强行覆盖。

```bash
python install_contact.py --plugin-dir <现有社区插件目录>
# 检查通过、备份配置并停止 AstrBot 后：
python install_contact.py --plugin-dir <现有社区插件目录> --apply
```

安装器只改三个 services 文件，原 provider 不覆盖但会校验；先保存时间戳备份，复制失败恢复原文件。
不重启、不启用配置、不访问网络。首次安装/升级到其他源码基线需人工复核并更新受测补丁，不能沿用旧 cp 指令。
本次 llm_service 已依赖 contact_bridge，不能只复制一个文件。
恢复时先关闭协调、停 AstrBot，把备份里的两个旧 services 文件还原，再移除新 contact_bridge。
插件升级可能覆盖补丁；重新检查后再开启共享协调。

来源：https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat
上游派生源码及本目录补丁按 AGPL-3.0 随项目分发。
