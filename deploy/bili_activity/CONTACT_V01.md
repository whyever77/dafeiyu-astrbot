# 主动联系协调 v0.1

2026-10-30，已在云端部署并开启协调；proactive_chat 社区补丁也已安装，额度设为 1/120/5/1。真实 QQ 联动 smoke 尚未执行（当前处于 23:00–08:00 免打扰）。接续[生活决策](LIFE_DECISION_V01.md)：
让她保留自己的活动，又能在刚分享过、主人正在聊天或连续没有回应时安静下来。
本阶段协调联系时机，不增加定时任务，不生成虚构经历，不改变人格。

## 已实现的范围

| 入口 | 接入位置 | 计数方式 |
| --- | --- | --- |
| B站/生活后台感想、截图、作品摘录、审批邀请 | `main.py` 的 `reserve_share` / `contact_send` | 每次完整联系一次；原活动预算仍同时生效 |
| proactive_chat 主人私聊 | 生成前检查、整个主动发送过程预留、每次 `send_chain` 前复查 | TTS、文字和分段共用一次预留 |
| 主人发来的消息 | 现有 `incoming` 的主人/会话校验之后 | 按消息 ID 去重，记录时间，不保存正文 |
| 查询 | `df_life_status.contacts`、`ops.py contacts` | 前者含今日用量，后者只读列出最近八次联系 |

主人主动要求的回复、被动截图、必要的登录失效通知与执行结果不占这份主动联系额度。
其他私聊、群聊保持原 proactive_chat 行为。这不是机器人全部消息的总限额。
社区插件原有调度、会话开关、免打扰与未回应策略继续生效；共享检查只追加限制。
未安装社区补丁时只能限制生活侧，不能声称两个入口已完成真实联动。

## 配置

均位于原 B站插件 `control.json`，缺新键时按默认值加载，不覆盖旧配置。

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `contact_coordination_enabled` | `false` | 同时启用两端协调，需要先安装两个插件的本次改动 |
| `contact_daily_limit` | `3` | 上海日期内完整主动联系上限；`0` 拒绝受管入口的主动联系 |
| `contact_cooldown_minutes` | `90` | 两次主动联系至少间隔多久，跨午夜仍有效 |
| `contact_owner_quiet_minutes` | `5` | 主人刚说过话，先让出聊天时间 |
| `contact_unanswered_limit` | `2` | 当天最近一次主人消息之后最多主动联系几次 |

共享层使用原 `owner_scope` / `owner_id` 和 `dnd_ranges`。不推断新的免打扰时段。
不要求开启 B站公开写操作；生活侧仍必须通过现有 enabled、life、goal、methods、审批等检查。

2026-10-30 已执行以下配置（实际额度 1、间隔 120、主人留白 5、未回应 1）：

```bash
python ops.py --data-dir <原B站插件数据目录> configure --contact-daily-limit 1 --contact-cooldown-minutes 120 --contact-unanswered-limit 1
python ops.py --data-dir <原B站插件数据目录> switch contact_coordination_enabled on
python ops.py --data-dir <原B站插件数据目录> contacts
```

关闭协调开关会恢复两个插件各自原有策略，**不等于禁止所有主动聊天**。
要暂停受管主动联系，可保持协调开启并将 `contact_daily_limit` 设为 `0`；完全停用时还需关闭各插件原有主动触发。

## 账本与结果语义

在原 `activity.sqlite3` 增加 `contact_attempts` / `contact_owner_events`，沿用原备份。
SQLite 事务先预留容量，再发生网络等待；另一入口/另一连接不能同时抢到相同容量。
记录来源、类别、日期、时间、内容哈希、状态及安全格式的消息 ID，不记录正文、Cookie 或完整响应。

| 状态 | 能证明什么 |
| --- | --- |
| `reserved` | 已预留，未进入发送 |
| `dispatching` | 可能开始发送；按已用容量计算 |
| `sent` | 生活侧原 QQDelivery 返回消息 ID，不额外宣称人已阅读 |
| `reported` | 社区发送器返回 True，没有消息 ID/独立回读证明 |
| `uncertain` | 发送后取消、返回失败或结果无法确定；不授权自动补发 |
| `cancelled` | 在发送前取消，不计已用容量 |

同一联系 ID 不能重放，改变参数会拒绝；归一化文本重复在 24 小时内被抑制。
未知结果的相同内容持续禁止自动重发，但不同内容在冷却/额度允许后可继续。
截图哈希还包含图片指纹。不是语义判重，不承诺识别换句话重复同一话题。
未派发预留十分钟后可释放；派发中的记录到期转为未知，冷却依据固定到期时间，
不会因为反复检查而不断延长。仅查询不会清理记录，过期状态在下一次协调检查时处理。

主人在 TTS 合成或分段间发来消息、开关变化时，会在下一个发送检查点终止。
已在途的网络请求不能撤回。已发部分内容后停止会保留为未知，不冒充完整送达。

## 社区插件版本与边界

见[补丁安装说明](../patches/proactive_chat/README.md)。现有目录文档曾只标 v1.2.6，
但上游该标签与 `core/services` 结构不一致；不能仅凭 metadata 版本号覆盖。
`sender_service.py` 来自[固定上游提交](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/tree/2dab58514e0a2055400ed3bf7b3907ae684c0c54)，仅增加桥接入口。
安装器按 LF 归一化后的 SHA-256 校验具体文件，并要求已有本仓库联网 provider 补丁。
版本不匹配直接拒绝；默认只检查，显式安装前备份，复制失败恢复。没有自动重启或云端操作。

桥接通过同一 AstrBot Context 中的 `dafeiyu_contact_v1` 获取协调器。
按标准 `data/plugins/...` 布局读取相邻 `data/plugin_data/astrbot_plugin_bili_activity/control.json`。
启用时协调器缺失/重载/配置损坏则跳过，不无声绕过。非标准数据布局需先人工核对，尚未验证。

社区原传输内部仍可能在异常后执行平台兜底；本次没有重写它，也不承诺网络层恰好发送一次。
装饰钩子内自行发送的消息、其他插件入口未逐一接管。社区纯媒体且没有文本身份的主动消息，启用协调时暂不放行。
生成前检查可省掉已知不允许的模型请求，但两端恰好同时开始生成仍可能消耗模型额度；
模型 token 预算并未跨插件合并。本次不改 LivingMemory，也未实现跨插件语义话题去重。

## 离线验证

使用临时 SQLite、假的 QQ/TTS/模型与临时安装目录；未读取真实 B站凭据、未调用真实写接口。
社区测试执行补丁中的 SenderService/LlmService 方法，框架对象为替身，不代表真实 AstrBot 插件加载已验收。

```powershell
$env:Path = 'C:\Program Files\Git\bin;' + $env:Path
& 'C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_bili_contact*.py' -v
& 'C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_bili_*.py' -v
```

- 新增定向：`Ran 32 tests in 1.188s`，`OK`。
- 修复后全量：`Ran 435 tests in 24.228s`，`OK (skipped=11)`。
- 11 项 skip：6 项缺 ffmpeg、1 项 Windows 创建符号链接需权限、4 项只支持 Linux 的安全文件导出。
- 全量日志：`C:\Users\ACER\AppData\Local\Temp\dafeiyu-contact-full.log`。
- 初始基线：403 项，1 error、11 skip。旧 `test_success_only_counts_checked_distinct_goals_and_cannot_replay`
  触发“success needs a new work revision produced after method selection”；其模块单独复跑 19 项通过，
  本次最终全量也通过。代码比较条件涉及作品/方法选择时间，是否为 Windows 时钟粒度导致尚未证实。
  没有修改 methods.py 或放宽原断言；初始日志为同一 Temp 下 `dafeiyu-outreach-baseline.log`。
- `git diff --check` 干净；transport/life/budget/agent_budget/workshop/methods/proxy/phone_video/media/login/persona 零改动。
- 本阶段未运行 WSL/Linux；真实 QQ 回执、TTS/装饰器联动、插件热重载和长期跨日运行均 **UNVERIFIED**。

新增测试覆盖：跨来源/跨连接抢占、上海午夜与跨日冷却、未回应限制、owner 事件去重、
停用/免打扰复查、预留和发送后取消、重启未知结果防重、过期预留释放、生成前省调用、
TTS/分段计数、主人插话、缺协调器、默认关闭、审批仍需主人确认、安装哈希/备份/回滚。

## 部署记录（2026-10-30）

- B站插件已部署 main `7912bcc`；备份 `bili-activity-20261029T163641.318699Z.tar.gz`。
- proactive_chat 补丁已安装；备份 `.dafeiyu-contact-backup-20261029T163946620609Z`。
- `contact_coordination_enabled=true`，`contact_daily_limit=1`、`contact_cooldown_minutes=120`、`contact_owner_quiet_minutes=5`、`contact_unanswered_limit=1`。
- `enabled/periodic_enabled/life_enabled/life_decision_enabled` 仍为 true；proactive_chat 私聊已启用。
- AstrBot 两插件加载正常，OneBot v11 适配器已连接；`contacts` 目前为空，真实发送 smoke 待免打扰结束后执行。

## 主人后续最小 smoke 与回滚

1. 已完成：备份现有配置与 activity.sqlite3，社区补丁只读检查通过并已安装。
2. 已完成：安装本次生活插件及社区补丁，保持 B站公开写入关闭，联系额度 1、间隔 120 分钟、未回应上限 1。
3. 先由生活侧分享一个有真实来源的感想，再触发社区主动聊天；第二次不发送，账本来源/额度正确。
4. 换一个测试日/独立临时配置反向验证社区先发；观察 reported 不冒充 QQ 已核验回执。
5. 主人在 TTS 合成或第一段后插话，确认剩余内容停下；确认前台正常聊天不受主动额度阻挡。
6. 重启后额度/未知记录保留，免打扰和原审批仍有效；观察异常是否被原社区内部兜底重复发送。

回滚先关闭协调，再停止 AstrBot、恢复社区安装器备份和原生活插件。
新表可保留，旧代码不使用；不删除旧配置或生产数据库，不重放未知发送。
后续里程碑仍是虚拟房间/生活状态呈现及记忆策略；本次没有把它们混入实现。
