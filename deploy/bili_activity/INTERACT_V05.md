# B站评论区互动 v0.5：增量审计与验收

审计日期：2026-10-26。基线 `origin/main=662cd5d`，分支 `codex/bili-interact-v05`。
已重新 fetch；复用本次会话刚从该基线创建的分支，不使用旧 worktree。
本提交只有本审计文档，尚未修改业务代码。

## 现状与 gap

| 已有能力 | 缺什么 | 拟改文件（路径相对插件或 deploy/bili_activity） | 新配置 | 离线测试计划 |
| --- | --- | --- | --- | --- |
| `Store.propose/offered/decide` 冻结参数、QQ 主人批准、过期/重复消息防护 | 三种新动作、精确展示 | 插件 `core.py`、`main.py` | `engagement_actions=false` | 默认关闭、先预览、未批准零 POST、开关运行中关闭 |
| `BiliAPI.prepare_write` 校验账号/目标，`Executor` 持久化 operation_id/fingerprint | like/coin/danmaku 参数与回读；API 数值 0 目前被 `or {}` 吞掉 | `bili_api.py`、`service.py` | 无新服务/依赖 | 三动作 prepare/verify、0/1、投币 1/2、毫秒/色值/字号、错账号/错 CID、相同 ID replay、不同参数拒绝 |
| `PUBLIC={comment,reply}` 与 collection 开关 | danmaku 属公开写；like/coin 单独分组 | 插件 `core.py`、`main.py`，运维 `ops.py` | `engagement_actions=false`；原开关不变 | comment/reply/danmaku、like/coin、收藏/关注三组隔离 |
| `Budget` 为后台时间/模型/QQ 分享预算 | 所有互动写入的每轮/每日动作预算、业务判重 | 新增插件 `interactions.py`，接入 `main.py` | `max_actions_per_run=3`、`max_actions_per_day=20`；comment/reply/danmaku/like/coin 每日分别 3/5/1/10/1 | SQLite 事务并发、上海跨日、到顶拒绝、失败不退额、同视频/同楼父回复/同 UID 判重 |
| Executor 不重试 POST，重启 executing→uncertain | 风控码传递、停止本轮、持久冷却、登录失效、动作间随机等待 | `bili_api.py`、`service.py`，插件 `interactions.py`、`main.py` | `action_interval_ms=[1500,4000]`、`risk_cooldown_minutes=60` | 12014/-412/-352、-101、取消/超时、等待后再检查开关、错误脱敏 |
| events/proposals/operations 现有账本 | interactions/daily_actions、最近动作/待审批/登录/冷却状态工具 | 插件 `interactions.py`、`main.py` | `interaction_mode="approval"` | `df_bili_status` 会话隔离、最近 5 条、事件必需字段、无 cookie/URL/响应体 |
| 评论详情回读有 rpid、内容、账号核对 | 独立公开可见性；弹幕延迟不可冒称公开 | `bili_api.py`、插件结果文案 | 无（有界分页） | verified 与 visibility 独立；public/not_public_or_folded/unknown |
| `read(replies)`、原调度器与原生 Agent | 自己评论楼的新回复发现/过滤/去重/上限 | P1 新增回复逻辑，接入插件 `main.py`/`interactions.py` | `reply_watch_enabled=false`、`reply_poll_minutes=3`、`reply_max_threads_per_tick=15`、`reply_min_len`、`reply_lookback_hours`、每楼每日限额 | 自己/旧回复/水评/广告过滤、每 rpid 一次、分页与轮询上限、approval 链 |
| 历史 read 经历和按会话回忆 | 1–3 条高信息量评论观察、分数/来源/安全提示 | P1 `interactions.py`、`main.py` | `comment_learning_enabled=false`、`learn_min_score`、`learn_recent_notes`、`learn_prompt` | 低分不入库、复读去重、作用域隔离、提示词把评论标为资料 |
| 主人消息处理器、CLI 配置备份 | 可选自动模式与 owner 互动命令、使用/回滚文档 | P2 `main.py`、`ops.py`、README/CHANGELOG/本文 | `auto_public=false`，可选 `allowed_uids`/`allowed_keywords` | approval 永不自动写；auto_low_risk 仅 like/coin；auto_public 多门联锁；旧配置兼容 |

## 保留与扩展边界

- 一行不改：`transport.py`、`life.py`、`budget.py`、`agent_budget.py`、`workshop.py`、`methods.py`、`proxy.py`、`phone_video.py`、媒体处理、登录/扫码代码，以及 `persona/大肥鱼-人格.md`。
- 不重写 proposal、Executor 或 MCP/QQ transport。保留 `Store.propose/offered/decide` 审批门；仅扩动作集合、增加必要的本地拒绝终态，外围接入互动控制器。
- Executor 继续用已有操作表、锁、fingerprint、单次 POST 与重启 uncertain 语义；仅补新动作回读和安全数值错误码。原始写工具继续不向模型暴露。
- 新表增量创建，变更前按现有 SQLite backup 模式保存一致性备份，不改旧数据/主 schema 版本。`control.json` 为运行时文件：默认合并新键，不覆盖旧文件；CLI init 对已有文件仍不改写。
- 不新增依赖，不动云端，不 push/PR/Release；所有真实写入与扫码 smoke 由主人手动做。

## 设计决定与待确认

1. 配额计数为尝试次数（包括结果未知），在外部调用前原子预留；coin 的每日上限是投币动作次数，单次 multiply 仍限定 1/2。失败不返还，避免超时重试绕过限额。
2. 每轮用真实后台 run_id；前台使用来源 QQ 请求作为轮次，批准消息沿用提议的来源轮次。日界线固定 Asia/Shanghai。
3. comment/danmaku 按 `(scope,bvid,action)`、reply 按 `(scope,bvid,root_rpid,parent_rpid)`、follow 按 `(scope,uid)` 判重；未知/失败保守阻止自动再提议，原操作 ID 可查询已有 Executor 结果，不再 POST。
4. `verified` 只说明接口读回目标状态。P0 对评论可见性明确 unknown；P1 才补公开列表检查。公开列表应匿名读取；分页达到安全上限但未穷尽只能 unknown，不能误报 folded。
5. 弹幕按返回 dmid 与当前 XML 池核对，缺 ID 或延迟出现为 `verified=false, visibility=unknown`；禁止仅凭 code:0 声称已公开。
6. 本地 fixture 可证明调用契约和控制流，不能证明 B站现时接受这些端点/参数。以任务书端点和现有 request 封装为基础；真实返回、登录/风控行为未验证，留主人小号 smoke。
7. DSH 仅为能力目标，未提供其仓库/固定版本，不声明逐行兼容。P0 完整并绿后再进入 P1；若 P1/P2 不交付，会明确列 TODO，不合入半成品。初始 P0 所有模式均保留审批，自动放行只在 P2 完整实现和测试后启用。

## 基线验证

- 默认 `python` 指向未安装 mcp 的 Conda 环境；首次沙箱运行还遭遇 Windows 临时目录 ACL，不能算基线通过。
- 使用仓库既有验证环境 `C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe`，并将 `C:\Program Files\Git\bin` 放到 PATH 最前（避免 WSL bash 收到 Windows 路径）：

  `python.exe -m unittest discover -s tests -p "test_bili_*.py" -v`

  **263 项，0 失败，11 跳过，14.621 秒**。跳过为现有 6 项 ffmpeg、4 项 Linux export、1 项 Windows symlink；没有改测试条件。日志：本地 `bili-baseline-windows.log`（不提交）。
- 本地 WSL Ubuntu 22.04 只有 Python 3.10，缺 ensurepip/venv；仓库运行代码需要 `asyncio.timeout`（3.11+）。Linux 基线待隔离解释器准备后记录，不复用云端环境。

## 分阶段提交与验收

1. 本审计单独 `docs: audit bili interaction v0.5 gaps`。
2. P0 动作/风控/状态与 `tests/test_bili_interact.py`，全量离线回归通过再 commit。
3. P1 回复监听/可见性/学习与测试，完整实现后 commit。
4. P2 自动模式/owner 命令/文档与测试，完整实现后 commit。
5. 最终补上实际命令、结果/skip、改动文件、限制及主人 smoke 清单；未实测项明确“未验证”。
