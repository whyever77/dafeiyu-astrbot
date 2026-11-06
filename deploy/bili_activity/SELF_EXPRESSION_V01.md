# 近况回顾与自我表达 v0.1

2026-11-02，本地实现、默认关闭、未部署。第六阶段为大肥鱼提供有来源的近况回顾，让现有模型沿用现有人格自然表达；不另造情绪分数、固定口头禅或一套人格。

## 依据与范围

| 现有能力 | 本阶段使用方式 |
| --- | --- |
| goals 与 episodes | 读取当前意图、完成记录、实际读取来源及修订关系 |
| video/video_note + interest | 复用上一阶段的来源校验，比较前后两个时间窗口的自身兴趣记录 |
| creative_works / creative_revisions / work_checked | 核对已有检查记录与当前作品版本、哈希、来源是否对应；只读，不调用会写缓存的 check |
| runs | 读取失败、超时、取消、预算结束与重启中断；不从模型总结里猜原因 |
| 原生 Agent / on_llm_request | 在真实人格、已有情绪和会话上下文后追加资料；前台私聊和生活轮次共用一个入口 |
| LivingMemory / 经历记忆 | 保留现有写入与遗忘策略；本回顾不直接写记忆或强化次数，不当成新经历 |

只读 `self_expression.py` 不创建表，不改变目标/作品/运行状态，不调用模型、设备、网络或QQ发送。`memory_policy.py` 将已有兴趣来源校验抽为 `observed_interests()`，供原巩固逻辑与本阶段共用；原有28项记忆策略测试包含在最终回归中。

## 回顾内容

### 正在关注什么

返回最多3个当前目标：标题、当时理由、下一步、状态和更新时间。显式标为意图；ready/paused/blocked/waiting_approval 都不等于已经执行，查看也不会领取目标或解除审批。

### 兴趣有没有变化

默认比较**最近14天**与**此前14天**，均为滚动时间窗口；输出时间使用 Asia/Shanghai。窗口可改为7..90天。

- 每条兴趣须引用同会话的 video/video_note，具备实际画面观察或字幕，视频标识一致、时间有效、来源未被修订。主人偏好、运行总结、空观察、外会话和未来记录不作为自身兴趣证据。
- 同主题/视频 bvid 只取最近立场；同视频分P和重复表述不增加独立来源。近期重复看以前的视频也不能人为补足前后两期的证据。
- 最近只有一次独立观察：`tentative`，单次感想。
- 最近多次但方向矛盾：`mixed`，保留矛盾。
- 最近至少两个不同视频且方向一致：`repeated_recent`，只能说明近期有多次记录。
- 两期各至少两个不同视频且方向一致：同方向为 `consistent_between_windows`，相反方向为 `changed_between_windows`。
- 没有近期记录不解释成“兴趣消失”。以上均是有来源的主观感想，不是事实正确性、永久性格或主人的偏好。

最多展示3个近期主题，每期最多3个来源引用，同时提供独立来源计数；不把这些计数当作情绪强度。

### 哪些事情做到了

只取窗口内最近3个已完成目标。普通研究目标仍须找到当前可用的实际读取证据；关联作品则须存在检查记录，且作品ID、版本、哈希和来源列表与当前版本对应，来源仍可读取且未被修订。

检查记录缺失、头版本改变或来源失效时返回 `needs_recheck`，不能声称已经核实。检查仅支持“有记录的目标收尾/作品检查”，不证明结论正确；本回顾不会重新做全文结构检查。单次 run 的 completed 不计为目标完成。

这为模型表达做成某事后的想法提供依据，但不自动赋值“成就感”。已有 EmotionAI 与人格仍负责原来的表达背景。

### 为什么暂时没做下去

最多展示3条近期中断记录，只陈述可确定的边界：达到时间/预算上限、被取消、重启恢复时记为中断、失败。取消和失败的具体原因不能只凭状态断言；不从运行总结复制原因，不推断主人的态度、责任或机器人的情绪。中断也不等于整个目标失败。

### 2026-11-06 拟人化增量：作息、跨日项目、前台打断衔接、攒着没发的话、聊天线索与好奇心反哺

1. **当前作息与跨日项目并入近况快照（`rhythm` 与 `projects`）**：
   - `rhythm` 只读复用 `rhythm.snapshot`，在开启生活节律时返回当前时段（`morning` / `day` / `wind_down` / `rest_after_activity` / `sleep`）、原因、本地时间与窗口内已活动分钟数，供前台聊天感知当下作息阶段（如睡前仅整理作品、活动后留白休息），不生成生理疲劳或睡梦。
   - `projects` 在开启跨日项目且表已初始化时（`ready(create=False)`，绝不建表或写备份），只读返回最多3个当前会话项目、当前阶段状态、回看是否已记录、后续问题（`next_question`）及是否到点待回看/待推进（`due_task: review | advance`）；来源被修订或失效时降为 `portfolio_valid=false` 并提示重新核对。
2. **被主人前台操作/消息打断的衔接（`foreground_preempt` 与 `recent_preemption`）**：
   - 中断记录关联同轮 `trigger`（活动类型 `browsing` / `writing` / `project_review`）与 `goal_selected`（同会话 agent 目标标题）。
   - 当 `status='cancelled'` 且运行时间窗口内存在受控白名单的 `foreground_preempt` 事件（如主人请求截图、使用手机、调整目标/项目/作品等），记录 `preempt_reason` 与 `preempt_explanation`；非白名单字符串、外会话事件或超出时间窗口的事件一律忽略。
   - 若最近一次中断属于白名单前台打断、发生在最近15分钟（`900` 秒）内且此后尚无新运行结束，快照额外提供 `recent_preemption`，让她在主人开口时能自然接话“刚才正在看/写什么，因为你找我刚停下”。
3. **系统提示词改为自然中文备忘（`_format_context`）**：
   - `df_self_reflect` 工具与 `df_life_status.self_expression` 继续返回结构化 JSON；而注入 `req.system_prompt` 的 `SelfExpression.context()` 改为紧凑中文备忘条目，剔除 `body_hash`、`check_episode_id`、`source_episode_id`、`episode_scan_limit` 等内部字段，减少公文汇报腔。
4. **话到嘴边留着下次聊（`share_held` 与 `held_thoughts`）**：
   - 后台生活轮次调用 `df_share_thought` 或 `df_workshop(..., action="share")` 被联系时机/留白/每日分享额度门禁拦截时（受控白名单原因 `HELD_SHARE_REASONS`），只记录 `share_held` 事件，不发 QQ 消息。
   - `SelfExpression.snapshot` 只读暴露最近48小时内最多2条尚未发出的 `held_thoughts`（含摘录、被拦原因及关联作品/目标）；一旦同内容随后成功发出或状态不确定（`deliveries.status in {'sent','uncertain'}`），或关联作品修订/来源失效，自动从 `held_thoughts` 中移除，绝不把“攒着没发”说成“已经发过”。
5. **聊过的话题纳入闲暇探索候选（`owner_chat_topic` 与 `chat_topics`）**：
   - 主人私聊除了显式偏好（`record_user_preference`）外，日常提到的在玩/在看/想聊话题（`record_chat_topic`）以只读事件 `owner_chat_topic` 记录在当前会话，不改动 `episodes` 或长期记忆表。
   - `LifePlanner.choose` 在生成 `explore` 候选时读取主人正向显式偏好与近期聊天话题作为 `chat_topics`（显式标注 `subject_kind="user"`，加4分 `owner_chat_topic_available`），在无自身探索主题时于 `reason` 提供“顺着之前和主人聊过的……看看资料”参考；`decision["topic"]` 与 `own_interest_evidence` 仍严格归属大肥鱼自身观察（`subject_kind="agent"`），绝不把主人提过的话题混作自身既定喜好。
6. **阶段回看新问题与非视频核实作品反哺好奇心（`curiosity_threads`）**：
   - 跨日项目已完成阶段回看留下的 `next_question`（`project_followup`，需阶段作品及来源有效）与独立已完成目标中通过结构核实的阅读/视频作品 `## 待核实` 段落（`work_open_question`，需当前头版本及 `read`/`video` 来源有效）共同汇总为 `curiosity_threads`。
   - `SelfExpression` 在快照与中文备忘中展示最多3条 `curiosity_threads`（“留着想继续弄清的问题”）；当没有未展开的视频兴趣时，`LifePlanner.choose` 的 `explore` 候选可优先顺着这些有来源的待核实问题（`project_followup_question` +6分 / `work_open_question` +5分）开启下一轮探索。

### 2026-11-06 增量：攒着的话择机带出

前台主人私聊的自动上下文改用 `expression_cadence.py`：每条消息最多提供1条有效摘录，同一候选6小时内不再次自动提供。候选仍只来自最近48小时的被拦分享，最多考察只读快照中的2条。重复记录相同内容的被拦分享不能绕过冷却；作品头版本改变或来源失效后，旧版摘录不再提供。

稳定的消息ID与候选指纹只以摘要写入 `events.kind=held_thought_context`，状态固定为 `context_only`。同一消息的工具续轮复用原选择，不重复计账；若原候选已失效则不提供，也不换成另一条。挑选和记账使用原有SQLite事务，防止两个并发请求同时挑中同一候选。账本重开后仍遵守冷却。

这不是送达或消费确认：模型可能没采用摘录，生成也可能失败，均不删除原记录、不标记已说过、不重发QQ消息。`qq_sent=false` 仅描述原先被拦的主动分享；普通聊天是否提过、是否送达均未知。中文备忘明确区分这层含义。

`SelfExpression.snapshot/context`、`df_self_reflect` 和状态查询继续只读。主人主动问近况时仍可查询全部有效候选，不受自动提供的6小时冷却限制。生活/回复等后台轮次、缺稳定消息ID、活动关闭时不自动提供这些摘录；DND、生活暂停、非主人或其他会话不产生曝光记录。没有候选时也不记账。

事件不复制聊天正文或摘录，不写长期记忆，不改变联系额度、审批、发送路径或活动开关。固定扫描最近48小时最多500条曝光事件，超过上限或账本内容损坏时保守省略自动摘录，显式只读查询仍可用；未来时间的曝光记录也不会被当作已过冷却。无新增配置，沿用 `life_enabled` / `self_expression_enabled`，默认关闭。

## 接入和行为边界

- 新工具 `df_self_reflect` 只读返回上述资料；`df_life_status.self_expression` 同时提供状态快照。
- 对话钩子在开关开启、生活功能开启、主人私聊范围与发送者一致时追加 `[自身近况依据]`。明确要求只在相关话题自然带出一两点，无关时不提，不逐项报告、不念内部字段。
- 原生 Agent 在 runner reset 之前经过该钩子；生活轮次只在开关开启时将工具加入许可集合。默认关闭不会让旧轮次因缺少这个可选工具而失败。
- B站公开回复监听不获得这份私聊近况上下文或工具。
- 保留原主人校验、DND、暂停、工具范围、模型预算、联系额度和审批规则。回顾不授权公开写入、不主动发送新消息、不创建新活动。
- 回顾入口不会直接入队 LivingMemory，不从回顾文本或运行总结反推新兴趣。原有会话采集及运行总结入库是独立链路，仍由原配置和上一阶段记忆策略控制；本功能不承诺改变上游自动记忆行为。
- 不修改 `persona/大肥鱼-人格.md` 或 EmotionAI。规则只能约束模型的依据与提示，不能用离线单测证明实际说话已经自然。

## 开启、查看与回退

| 配置 | 默认 | 条件 |
| --- | --- | --- |
| `self_expression_enabled` | false | 主人私聊与既有DND配置有效；`life_enabled=true` |
| `self_expression_window_days` | 14 | 7..90整数；近期与此前窗口使用相同长度 |

不要求开启 `life_memory_enabled`，两者可独立关闭。旧 control.json 缺键会得到默认值。

以下是将来明确部署后的操作示例，本次未执行；请替换为已核对的测试目录：

```bash
python3 ops.py --data-dir <测试数据目录> configure --self-expression-window-days 14
python3 ops.py --data-dir <测试数据目录> switch self_expression_enabled on
python3 ops.py --data-dir <测试数据目录> switch self_expression_enabled off
```

开启不会连带启用周期任务、公开互动或收藏管理。关闭后，新请求不再注入回顾，新工具调用拒绝；原生活和记忆功能继续按各自开关工作。已构建的模型请求不能撤回其上下文，立即停止仍使用现有暂停机制。无数据库迁移，无需删记录或回滚数据库。

## 验证

本阶段开始时，上一阶段记忆策略尚未提交的改动已存在并保留；基线包括这些改动。本阶段新增 `self_expression.py`、`test_bili_self_expression.py` 和本文；增量修改 `main.py`、`ops.py`、`memory_policy.py`、README、CHANGELOG、进化清单。未修改前端、角色动画或星海60秒同步。

```powershell
$env:Path='C:\Program Files\Git\bin;' + $env:Path
$testPython='C:\Users\ACER\AppData\Local\Temp\dafeiyu-rhythm-venv\Scripts\python.exe'
& $testPython -B -m unittest discover -s tests -p 'test_bili_self_expression.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_bili_*.py' -v
git diff --check
```

| 检查 | 实际结果 |
| --- | --- |
| 修改前基线 | 485项，29.904秒，OK（11 skip） |
| 最终定向测试 | 33项，1.126秒，OK（0 skip） |
| 最终全量回归 | 518项，24.893秒，OK（11 skip） |
| 补公开回复隔离前的一次全量回归 | 517项，30.990秒，OK（11 skip） |
| 2026-11-06 拟人化增量（1/2/5）定向测试（无网只读容器 Python 3.12） | 37项，2.645秒，OK（0 skip） |
| 2026-11-06 拟人化增量（3/4/6）定向测试（无网只读容器 Python 3.12） | `test_bili_self_expression.py` 40项 OK，`test_bili_planner.py` 31项 OK（0 skip） |
| 2026-11-06 全量 `test_bili_*.py` 回归（无网只读容器 Python 3.12） | 615项，42.944秒，OK（1 skip：Windows junction 宿主专属测试） |
| `git diff --check` | 通过；只有既有 Git 换行转换提示，无空白错误 |
| 受保护模块、人格、手机启动脚本 diff | 空 |

11个跳过与基线一致：6项缺ffmpeg、1项Windows符号链接权限、4项Linux限定安全导出。全部使用临时库、fake/mock和本地测试服务，没有真实B站/QQ/模型调用。日志保留在本机临时目录 `dafeiyu-self-expression-baseline.log`、`dafeiyu-self-expression-focused.log`、`dafeiyu-self-expression-final.log`。

反例覆盖：单次/重复/分P不构造变化，两期来源与时间边界，矛盾/无近期记录/用户偏好/外会话/未来/修订来源的保守处理；意图不当完成、完成运行不当完成目标、作品头版本与来源失效；错误自述不当中断原因；攒着没发的话在发出/不确定/来源失效后自动清除；主人聊天线索不覆写自身探索主题；项目回看与作品待核实问题须来源有效才纳入好奇心；反复查看全库数据不变；缺省关闭、开关回退、不扩写权限；主人与背景轮次检查、DND/暂停、公开回复隔离；原生 Agent 钩子保留人格/已有情绪并在执行前注入资料。

## 尚未验证

真实 AstrBot 注册与模型生成、QQ表达、EmotionAI/LivingMemory 在线联动、长期使用和 Linux **UNVERIFIED**。本阶段未跑 WSL；上一阶段已确认其 Python 缺少 mcp。未调用真实账户、未改云端或生产配置、未 push/部署。

扫描和输出都有上限：兴趣来源复用最近500条未被修订经历；每个研究目标最多检查50条来源、作品最多50条检查记录；中断查看最近12条已结束运行。条数达到截断条件时标注覆盖限制；窗口没有结果不证明历史上没有发生。

主人日后可先在隔离会话用低额度、公开写关闭的配置验收：

1. 核对新工具已注册；问近期在研究什么，回答须对应当前目标，并区分打算与做完。
2. 比较有两期独立证据、只有一次观察、近期矛盾三种测试数据；核对措辞不把证据不足说成性格变化。
3. 使用有检查记录和来源已修订的测试作品，确认后者说明需要核对；超时/取消不得归咎于主人或编出感受。
4. 核对已有情绪和人格仍在，普通无关聊天不频繁自报近况，公开回复监听不带私聊回顾。
5. 关闭开关，确认新请求无新增上下文、调用工具被拒，原始记忆与活动记录保持原样。

下一阶段是更丰富的自主项目：跨日主题、作品集、完成后回看与新的问题；不在本阶段扩写活动权限或新调度任务。
