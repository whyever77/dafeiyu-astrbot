# EmotionAI v3.4：主对话去注入 + 独立情感分析

上游插件：`astrbot_plugin_emotionai` v3.4（作者：腾天，仓库 `tengtian3/astrbot-plugin-emotionai`）。
本目录是云端运行版本的补丁副本，不是完整上游包；部署时覆盖同名文件，并在配置里补上新键。

## 为什么改

旧实现 `inject_emotional_context` 每轮往主对话追加三条伪造消息：

- 假用户：`（系统自检：请报告当前情感状态）`
- 假助手：带 `<thought>` 的「报告前辈，星绘……」
- 假 system：`[PROTOCOL_ENFORCEMENT] … REQUIRED ACTION: Generate XML block`

模型会服从 system 级 XML 要求，主回复里出现空 ` ```xml ` 围栏；`face_convert` 只摘掉 `<thought>` 内容，剩下的围栏和「代码片段」标签被发到 QQ。旧测试记录还因此反复进入上下文。

## 改了什么

- `inject_emotional_context`：只保留对旧测试样本的清理，不再追加任何伪造消息；情感状态和语气只注入 `system_prompt`。
- `_build_cognitive_context`：删除「必须 `<thought>`」「输出格式强制协议」「XML 模板」，只保留情感面板和语气要求。
- `process_emotional_update`：主回复先做内部残留清洗；情感更新改为**独立分析调用**。
- 独立分析只分析**最终可见回复**，工具调用中间轮不分析；输入当前情感值、最近一条用户消息和刚生成的回复，要求 `deepseek-chat` 返回：
  ```json
  {"updates": {"joy": 3, "favor": 2}, "reason": "一句话理由"}
  ```
  不写入主对话历史，不调用工具，不把用户原文写日志；失败或超时保留原情感值。
- 原有情感维度、`change_min/max`、favor/intimacy 上限、黑名单和持久化格式不变。
- 兜底清洗只删除完整 `<thought>` 块、`[PROTOCOL_ENFORCEMENT]` 块和空 `xml/text` 围栏；非空代码块原样保留。

## 安装/更新

云端插件目录：

```bash
plugin=/opt/dafeiyu/astrbot/data/plugins/astrbot_plugin_emotionai
ts=$(date +%Y%m%d_%H%M%S)
cp -a "$plugin/main.py" "$plugin/main.py.bak_$ts"
cp -a "$plugin/_conf_schema.json" "$plugin/_conf_schema.json.bak_$ts"
install -m 644 main.py "$plugin/main.py"
install -m 644 _conf_schema.json "$plugin/_conf_schema.json"
# 把 config.example.json 的新键合并到
# /opt/dafeiyu/astrbot/data/config/astrbot_plugin_emotionai_config.json
# 不要覆盖旧的好感度/管理员配置。
docker restart astrbot
```

配置新键默认值：

| 键 | 默认 | 说明 |
|---|---|---|
| `emotion_analysis_mode` | `separate` | 独立 JSON 分析；`off` 关闭分析 |
| `emotion_analysis_provider_id` | 空 | 留空用当前聊天 provider；可填 `deepseek-reasoner` |
| `emotion_analysis_timeout` | `8` | 秒；失败保留原值 |
| `emotion_analysis_max_tokens` | `160` | 建议 96–256 |
| `inject_emotion_state` | `true` | 只注入状态/语气，不注入 XML |

当前主聊天是 `deepseek-chat`，它没有原生 `reasoning_content`；`deepseek-reasoner` 有，但切换主聊天会改变延迟/成本/语气，所以默认不切换。原生 reasoning 只作为以后可选路径，不用于当前默认实现。

## 验证记录（2026-10-29）

- 云端备份：`/opt/dafeiyu/backups/emotionai_separate_20261029_225640`
- 已从 conversation 2 / 5 / 6 删除旧伪造三连；全库扫描 `[PROTOCOL_ENFORCEMENT]`、`星绘`、`报告前辈`、精确假用户消息均零命中。
- 另外清理了历史 assistant/system 消息里的空 `xml` 围栏和 `<thought>` 块（含一条只有 thought、没有可见正文的旧消息）；用户可见正文保留。
- 历史正文里仍有一句正常的「被系统自检打断」叙述，不是伪造注入消息，未删除。
- 插件加载成功（v3.4），OneBot v11 适配器已连接。
- 容器内纯逻辑测试通过：旧上下文清洗、`<thought>`/空围栏清洗、JSON 解析、`process_emotional_update` 流程。
- 真实 `deepseek-chat` 分析样例返回合法 JSON：`joy/trust/favor/intimacy`，未出现 Markdown 围栏。
- 非空 Python 代码块在清洗测试中原样保留。

## 回滚

1. 快速停用分析：`emotion_analysis_mode = "off"`，重启 AstrBot；主聊天仍会去注入，但没有情感更新。
2. 完全回滚：恢复备份的 `main.py`、`_conf_schema.json`、`astrbot_plugin_emotionai_config.json`，重启 AstrBot。
3. 数据库备份保留在 `/opt/dafeiyu/backups/emotionai_separate_20261029_225640`；不要用旧库覆盖新库来“撤销”已发生的情感变化。

## 注意

- 上游插件升级会覆盖 `main.py`；升级后必须重新合并本目录改动，否则旧注入会回来。
- 独立分析默认每轮多一次低 token 调用；如只需情绪状态显示、不需要更新，可设 `off`。
- 本补丁不改变主聊天模型，也不会把 `<thought>`、XML、情感更新数值发给 QQ。
