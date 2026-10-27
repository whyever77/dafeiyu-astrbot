# B站评论区互动 v0.5：增量审计与验收

审计日期：2026-10-26。基线 `origin/main=662cd5d`，分支 `codex/bili-interact-v05`。
已重新 fetch；复用本次会话刚从该基线创建的分支，不使用旧 worktree。
首笔审计提交 `d4caedb` 只有本审计文档，当时尚未修改业务代码；下面按阶段追加实际交付记录。

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

## P0 实施结果

已扩展原动作链、添加互动策略表与 `df_bili_status`，原 proposal 的提出/送达/决定函数、transport、life、预算和手机代码保持不变。CLI 初始化的新 control 包含所有互动默认值，旧 control 原样读取并合并默认值。升级导入旧 verified/uncertain 操作作为判重证据，不重置历史配额。评论/回复在 P0 明确报告 `visibility=unknown`，弹幕只有匹配公开 XML 中的 dmid/正文/时间才报告 public。

- Windows 全量：`python.exe -m unittest discover -s tests -p "test_bili_*.py" -v`，293 项 OK、11 跳过（16.182 秒）。
- Linux 基线归档（662cd5d）：相同 unittest 命令，263 项 OK、6 跳过（11.949 秒）。
- Linux P0 全量：293 项 OK、6 跳过（23.742 秒）。Linux 用本机 WSL 的 `/var/tmp/dafeiyu-v05/venv/bin/python`（3.12.15，mcp 1.26）；没有访问云端。
- 随后补充 request 数值 0/数值错误码测试；新增测试集 `-p "test_bili_interact.py" -v`：Windows 31 项 OK（1.253 秒）。
- 曾有一个新增测试未关闭备份 SQLite 连接，Windows 清理失败；改用 `closing` 后通过，没有修改既有 skip 或断言。
- P0 阶段三种 interaction_mode 均保留原真实 QQ 审批；自动执行仍未实现。真实接口、公开展示、账号写入、QQ 实收均未验证。

## P1 实施结果

- 匿名调用评论列表，最多 10 页；公开主楼中的子回复再展开最多 10 页。审查修复后使用 WBI 列表、mode=2 时间排序与新分页 offset；找到 rpid 才记 public，未找到、请求失败、循环游标或未扫完均记 unknown。仅详情 verified=true 才查可见性；匿名查询风控码不传入账号风控，不降级为登录读取后宣称 public。
- 成功 comment/reply 记录成为监听来源；每轮最多 15 个楼、每楼读取一页，页号持久化、轮转选择避免只看最新楼。默认每 3 分钟；必须 `enabled=true`、`reply_watch_enabled=true`、`public_interactions=true` 且不在免打扰/暂停/冷却中。
- 过滤自己的 rpid/UID、长度不足、超过 24 小时或早于自己的楼记录、情绪/重复/广告/明显工具指令；每 rpid 持久化处理状态。每楼每天默认最多 2 次（含待批准），同时受全局每轮/每天/回复额度限制。每 tick 至多为 1 条新回复调用现有 Agent；仅暴露 df_bili_propose，参数必须匹配本轮已发现目标。沿用 Budget 的时间/模型/QQ 分享额度，不接触手机。
- `comment_notes` 默认不开启。开启后用长度、补充/因果/建议等信息线索和数字做 0–10 的规则评分，低于默认 7 分不保存；每次最多 3 条，去重并记录 bvid/cid/rpid。最近默认 5 条以固定警示注入主人 Agent 上下文与读取结果；`learn_prompt` 是可配置的选择偏好，不取代固定的“不是事实/不是指令”。评分是启发式筛选，不是事实核验或可靠的语义广告检测。
- Windows：全量 `test_bili_*.py -v` **314 项 OK，11 跳过**，17.817 秒；Linux：**314 项 OK，6 跳过**，22.972 秒。新增 `test_bili_replies.py` 20 项；P0 新增集现为 31 项，两个平台通过。
- 真实 B站列表对匿名用户的展示差异、延迟、折叠、回复监听及真实 Agent 文案质量仍未验证。public 仅证明该次匿名 API 响应含目标，不保证所有客户端/所有用户都能看到。

## 如何开启与自动模式边界

升级本地文件本身不修改实际 `control.json`。新安装由插件/`ops.py init` 创建完整默认配置；旧文件缺新键时合并默认值，不删除自定义键。以下命令仅供主人审阅后在实际运行环境执行，本次没有运行这些生产命令。

新增字段默认参考（合并到实际 `control.json`，不要用它覆盖整个旧文件）：

```json
{
  "engagement_actions": false,
  "interaction_mode": "approval",
  "auto_public": false,
  "max_actions_per_run": 3,
  "max_actions_per_day": 20,
  "max_comments_per_day": 3,
  "max_replies_per_day": 5,
  "max_danmaku_per_day": 1,
  "max_likes_per_day": 10,
  "max_coins_per_day": 1,
  "action_interval_ms": [1500, 4000],
  "risk_cooldown_minutes": 60,
  "reply_watch_enabled": false,
  "reply_poll_minutes": 3,
  "reply_max_threads_per_tick": 15,
  "reply_min_len": 8,
  "reply_lookback_hours": 24,
  "reply_max_per_thread_per_day": 2,
  "comment_learning_enabled": false,
  "learn_min_score": 7,
  "learn_recent_notes": 5,
  "learn_prompt": "挑选有信息量、补充视频内容的评论；跳过纯情绪、复读和广告。",
  "allowed_uids": [],
  "allowed_keywords": []
}
```

建议先用 B站小号、保持 `interaction_mode=approval`，把每日总额降到 3、评论 1、回复 1、弹幕 0、点赞 2、投币 0；先验证一种操作，再增加范围。配额是尝试次数，取消赞也算 like；coin 的 select_like 是同一次 coin 动作的附带效果，不另扣 like 动作额度。失败和 unknown 仍占额度；午夜只更换上海日期计数，不清空账本或冷却。

```bash
python3 /实际工具目录/ops.py --data-dir /实际插件状态目录 configure --max-actions-per-run 1 --max-actions-per-day 3 --max-comments-per-day 1 --max-replies-per-day 1 --max-danmaku-per-day 0 --max-likes-per-day 2 --max-coins-per-day 0 --action-interval-ms 2000 6000
python3 /实际工具目录/ops.py --data-dir /实际插件状态目录 switch public_interactions on
python3 /实际工具目录/ops.py --data-dir /实际插件状态目录 switch engagement_actions on
```

主人私聊命令：

| 命令 | 行为 |
| --- | --- |
| `/bili互动 开` | 开启 public/engagement，强制回到 approval；不启用周期/监听/收藏管理 |
| `/bili互动 关` | 关闭 public/engagement/collection、auto_public、回复监听；保留账本 |
| `/bili互动 状态` 或 `额度` | 刷新登录状态，显示上海日期各动作计数/上限、待审批、冷却、最近 5 条动作 |
| `/bili互动 清理不确定` | 列出 uncertain；追加 ID 查看账本，再按短期凭据明确确认处理决定，不重发 |
| `/bili互动 模式 approval` | 所有写操作逐条批准 |
| `/bili互动 模式 auto_low_risk` | 仅 like/coin 可自动，仍需 engagement_actions 及正额度 |
| `/bili互动 模式 auto_public` | 显式设置模式与 auto_public=true；先要求 public_interactions 开启且总日额度为正 |

auto_public 下 comment/reply/danmaku 还必须同时满足 `public_interactions=true`、`auto_public=true`、`interaction_mode=auto_public`、总/单动作/每轮额度为正且未用完；like/coin 仍单独受 engagement_actions 控制。收藏/关注类始终逐条批准。自动操作仍先预览和创建同一 proposals 记录，单独写 `policy_approval`/`decision_event=policy:...`，不伪造 QQ “可以”、不绕过 Executor 和判重/冷却。

可选范围通过 `ops.py configure --allowed-uids '["目标UP的UID"]' --allowed-keywords '["教程"]'` 设置（示例 UID 须替换为数字）。只对自动放行生效，使用已核实视频的 UP UID 与标题匹配，不用模型拟发的正文；两种列表都非空时同时满足，空列表不限制该维度。未匹配回到精确主人审批。CLI 选择 `--interaction-mode auto_public` 与主人命令“模式 auto_public”均明确同步 `auto_public=true`，其他模式同步 false；动作开关、正额度、范围限制仍须满足，默认仍为 approval/false。

回复监听再开启 `reply_watch_enabled`，需要原 `enabled=true`、主人私聊与已配置免打扰；不要求手机在线，不会自行打开周期。学习再开启 `comment_learning_enabled`。各项默认见上方 P1 说明；`ops.py configure` 支持 `--reply-poll-minutes`、`--reply-max-threads-per-tick`、`--reply-min-len`、`--reply-lookback-hours`、`--reply-max-per-thread-per-day`、`--learn-min-score`、`--learn-recent-notes`、`--learn-prompt`。

B站风控不因低频、限额或使用小号而保证消失。12014/-412/-352 会停止该轮并冷却默认 60 分钟（HTTP 412 同样按 -412 处理）；-101 标记登录失效并通知主人扫码，扫码后用状态工具重新核实。不使用代理切换、账号轮换或自动重试规避限制。弹幕 XML 可能延迟或不覆盖历史；unknown 保留原账本，不新建 ID 重发。

## 回滚

先 `/bili互动 关`，停止本插件活动并备份最新 `control.json`、`activity.sqlite3` 与 Windows `operations.sqlite3`。恢复旧插件/伴随服务源码，但保留最新操作账本、审批记录和 QQ 回执；不要用旧数据库覆盖已发生的写入。旧代码忽略新增表，新键保留即可。`schema_backups` 和 `config_backups` 用于核对迁移/配置，不是清空历史并重放写入的手段。已发出的评论/弹幕、投币等不会随代码回滚撤销；真实撤销由主人手动判断。

## 假设与已知限制

- 没有 DSH 固定源码版本，不声明其内部协议兼容；已按社区维护的 B站 Web API 文档校正参数，真实端点可用性仍待小号 smoke。
- 一套插件/伴随服务实例控制一个账号。计数按主人 scope 隔离，沿用原进程锁；同一账号被多个独立部署/外部客户端同时写入，不在本控制器的全局配额保护内。
- 前台一条来源 QQ 请求为一轮，后台为真实 run_id；批准沿用提议来源轮次。升级前记录缺少实际执行时间时，以提议 created 日期导入旧次数，偏保守；成功关注的业务 key 不因之后取消关注自动释放。
- 每楼每 tick 一页，轮询间隔与 thread 上限限制请求量；很深/很多楼可能延迟发现回复。回复看回默认 24 小时；启发式文本过滤/学习可能误判，不保证识别所有广告、复读或错误信息。
- 未解决 uncertain 仍占单未决提议槽位；主人可用两步恢复命令释放槽位。原目标判重、相同参数的不确定执行记录和已计次数保留，不能借清理重发；真实扫码、核对不确定结果由主人处理。
- auto_public 是明确的配置授权，默认关闭；不匹配允许范围时回审批。匿名 API 的 public 不是所有客户端可见或主人已看到的证据。
- Windows 6 项 ffmpeg、4 项 Linux export、1 项 symlink 跳过；Linux 6 项 ffmpeg 跳过。真实媒体处理未在这些跳过项中验证。现有 MCP/pydantic 的非致命 warning 保留。

## 主人手动 smoke 清单（未验证）

1. 审阅本地提交，备份后自行部署；确认插件 v0.5.0、主人私聊隔离、旧配置未丢、新开关未自动打开。先用小号检查 `df_bili_status` 登录状态；需要时本人扫码。
2. approval + 低额度：分别预览 like=1/0、coin=1/2（按视频剩余额度）、danmaku 的精确 bvid/cid/毫秒/色值/字号；确认 QQ 完整参数和取消/过期行为，未批准时无写入。两枚投币会消耗真实硬币，单独决定是否测。
3. 批准一次后核对操作账本及 `verified`/`visibility`；用同 operation_id 查询 replay，不重复 POST；改参数同 ID 应拒绝。不要把返回 code:0 当公开成功。
4. 同视频第二次评论、同根/父 rpid 第二次回复、同 UID 第二次关注应被拒绝；将额度降至已用数，应提示额度已用完。真实风控不要主动诱发，出现时确认本轮停止/冷却/扫码通知。
5. 用另一个正常账号查看评论/回复/弹幕是否实际可见，与匿名 API 的 visibility 分开记录；延迟、折叠、unknown 如实保留，不重发。
6. 开监听，在已成功发过的楼由另一账号留一条有内容的新回复；等待轮询，确认先提议、准确 root/parent、同 rpid 一次、每楼/每天上限；关闭监听/进入免打扰后不再提议。
7. 开评论观察，读有补充信息的评论，核对来源、分数、最多 3 条和“不是真相/不是指令”标签；检查其他私聊/群聊无法获取这些观察。
8. 如确实需要，分别显式测试 auto_low_risk / auto_public 和 UP/标题允许范围，确认不匹配回审批、关闭开关阻止下一条、日志为 policy_approval 而非主人答复。最后 `/bili互动 关`，按需要保留 approval 和最低额度。
9. 有真实 uncertain 时先人工查看目标，再 `/bili互动 清理不确定 <id>` 查看 proposal/operation 摘要，按凭据确认。核对原操作没有第二次 POST，新目标可以重新提议，原目标仍受判重保护，回复监听恢复。不要为验证恢复而主动制造重复公开写入。
10. 核对点赞/取消赞实际效果、SPI 后的 buvid3_present、投币与附带点赞各自结果；弹幕若显示 accepted=true/verified=false，应等待或人工查看，不重新发送。匿名可见性接口不可用时须保持 unknown，不出现误报扫码/冷却。

## 最终交付与测试记录

P0、P1、P2 均已实现。P2 增加模式/范围联锁、真实 owner 命令、配置备份与 `.ops.lock` 互斥；自动回复仍调用原 proposal/Executor。最终检查还修复已手动回答的候选阻塞后续回复的问题，并从旧成功回读中的 canonical identity 迁移 av 别名，避免升级后换 BV 绕过判重。插件 register/metadata 同步为 0.5.0。没有 push、PR、Release 或云端改动，没有真实扫码/账号写入。

最终 328 项 = 原 263 项 + 新增 65 项；原有测试文件未修改。结果不包含真实 B站/QQ/模型验收。

| 命令 / 环境 | 实际结果 | 日志 |
| --- | --- | --- |
| Windows 既有 venv：`python.exe -m unittest discover -s tests -p "test_bili_*.py" -v` | **328 项，317 通过、11 跳过、0 失败/错误；18.089 秒** | `bili-v05-windows.log` |
| WSL Python 3.12.15：`python -m unittest discover -s tests -p 'test_bili_*.py' -v` | **328 项，322 通过、6 跳过、0 失败/错误；22.892 秒** | `bili-v05-linux.log` |
| Windows / Linux：`python -m unittest discover -s tests -p 'test_bili_interact.py' -v` | P0 定向复测各 31 项 OK；最终全量再次覆盖 | `bili-p0-tests.log` / `bili-p0-tests-linux.log` |
| Windows：`python -m unittest discover -s tests -p 'test_bili_replies.py' -v` | P1 初次定向 19 项 OK；随后补登录通知、候选跳过、auto_public 监听，最终全量含 22 项 | `bili-p1-tests.log` |
| Windows：`python -m unittest discover -s tests -p 'test_bili_interact_modes.py' -v` | 12 项 OK；最终全量再次覆盖 | `bili-p2-tests.log` |
| `python -m compileall -q plugin/astrbot_plugin_bili_activity deploy/bili_activity tests` | 通过 | 本地命令输出为空 |
| `git diff --check` | 通过；仅已有 Windows LF/CRLF 提示 | 无空白差异错误 |
| `git diff 662cd5d --` 加上前述保留文件列表及 `persona` | 无差异 | 人格、transport、life、budget、agent_budget、workshop、methods、proxy、phone_video、media、login 未修改 |

Windows 命令前将 `C:\Program Files\Git\bin` 前置 PATH，解释器为 `C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe`。Linux 解释器为本机 WSL `/var/tmp/dafeiyu-v05/venv/bin/python`；工作目录 `/mnt/d/GitHub/dafeiyu-astrbot`。Linux 基线使用 `git archive 662cd5d` 的只读源码归档，不是旧 worktree。所有日志仅本地保留、被 gitignore 排除；首次错误环境及阶段结果见前文。

| 改动文件 | 用途 |
| --- | --- |
| `plugin/astrbot_plugin_bili_activity/core.py` | 写动作集合与可信本地拒绝终态 |
| `plugin/astrbot_plugin_bili_activity/interactions.py` | 新增配额/判重/风控/状态/自动模式策略与迁移 |
| `plugin/astrbot_plugin_bili_activity/replies.py` | 新增回复轮询状态、候选过滤与评论观察 |
| `plugin/astrbot_plugin_bili_activity/main.py` | 原审批链外围接入、状态工具、回复调度、自动策略与 owner 命令 |
| `plugin/astrbot_plugin_bili_activity/metadata.yaml` | 插件版本/说明 |
| `deploy/bili_activity/bili_api.py` | 三动作准备/回读、数值错误码、匿名可见性 |
| `deploy/bili_activity/service.py` | 参数白名单/默认值/预览目标/安全错误码 |
| `deploy/bili_activity/ops.py` | 新控制字段、CLI 配置与开关，保留原配置备份 |
| `tests/test_bili_interact.py`、`tests/test_bili_replies.py`、`tests/test_bili_interact_modes.py` | 65 项新增离线测试 |
| `README.md`、`CHANGELOG.md`、`deploy/bili_activity/README.md`、本文 | 使用、审计、测试、限制、回滚与主人 smoke |

本地阶段提交：`d4caedb` 审计 → `36cb626` P0 → `fd2cb2d` P1 → P2 文档/命令/模式提交（见分支 HEAD）。主人手动 smoke 清单全部为**未验证**，不把离线 fixture 结果冒充真实发送或公开证据。

## 审查后修复：恢复与执行状态机（2026-10-27）

- 新增主人私聊命令 `/bili互动 清理不确定` 列出记录，追加提议 ID 查看 proposals 与伴随服务 operations 的只读摘要。再按展示的五分钟一次性凭据，发送 `/bili互动 清理不确定 <id> abandoned|verified 确认 <凭据>`。群聊、非主人、自主轮次不能确认；账本变化、超时、重复确认均拒绝。
- 恢复只将 proposal 置为 `resolved_abandoned` / `resolved_verified` 并记主人确认事件。保留原 result、Executor 账本、次数及目标判重，不重发 POST；人工 verified 不改写机器 `verified`。现有 one_pending 索引自然排除这些终态，无需删除未解决 uncertain 的保护。
- `execute_interaction` 覆盖等待锁、权限检查、发送前等待的取消/拒绝；未交给执行端时结束为 blocked，已交给执行端且结果未知才为 uncertain。
- 匿名可见性错误不应影响账号状态；旧伴随服务的 verified + 未注明来源 risk_code 也会被隔离。真实认证请求的风险可保留 authenticated 来源，QQ 提示仅在风险实际应用时出现。状态查询可刷新登录，保留原轮次停止和冷却；普通后台 B站读取/轮询仍检查风控。
- 新增 `bili_operation` 仅供控制器读账本，加入原始工具屏蔽清单，不暴露给模型；Executor、transport、原审批决策方法未修改。
- Windows 离线基线 328 项（11 skip），恢复阶段 341 项（11 skip），均通过。命令均为既有 venv 的 `python -m unittest discover -s tests -p 'test_bili_*.py' -v`；日志 `bili-fixes-baseline.log` / `bili-recovery-suite.log`。新增 13 项恢复/取消/监听恢复/状态回归。阶段中修正了新增工具清单断言及测试缩进；以上为最终重跑结果。

## 审查后修复：Web API 与回读语义

已按社区维护的文档校正参数，真实端点可用性仍待小号 smoke。资料不是 B站官方稳定性承诺：[点赞/投币](https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/video/action.md)、[弹幕](https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/danmaku/action.md)、[浏览器指纹](https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/misc/buvid3_4.md)、[评论列表](https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/comment/list.md)、[WBI](https://github.com/bilibili-plugins/bilibili-api-collect/blob/master/docs/misc/sign/wbi.md)。

| 问题 | 本地实现与验证边界 |
| --- | --- |
| 点赞混用 APP/Web | Web `/x/web-interface/archive/like`，业务参数仍为 1/0，POST 映射为 1/2，回读仍比较 1/0；测试直接断言 Web 契约 |
| 缺 buvid3 | like/coin 准备前通过 SPI 获取 b_3/b_4，缺失则本地拒绝、零 POST；保留平铺/包裹 Cookie 文件及 refresh_token/updated_at，自身并发请求互斥，网络返回后重读再合并 |
| 弹幕参数 | aid/cid、毫秒、rnd、1..99 字符；适配器添加 WBI URL 参数，不修改 Executor 单次 POST 逻辑；签名使用文档确定性样例和 HTTP mock 验证 |
| 弹幕 XML 延迟 | 有正整数 dmid 且 visible 严格为 true 时单独记 accepted；仍尝试 XML 核验，缺失/失败为 verified=false/visibility=unknown，proposal 可结束为 accepted；operations 保留原 uncertain/replay 语义和 accepted 元数据，不改写成 verified，不重发 |
| 可见性误伤账号 | 只有详情已核验才查匿名列表；identity/nav/列表均不带账号 Cookie；mode=2，WBI 路径及 offset 分页；失败/找不到为 unknown，risk_code=None |
| 投币附带点赞 | coin 核验只依据币数增量；附带点赞另报 like_ok=true/false/null，并进入事件、状态及 QQ 回复。独立认证读取若返回真实风险码，仍可标登录失效/冷却，不能用 coin 成功掩盖它 |
| 监听时间下界 | 保留外部执行前 interactions.created，兼容服务端整秒时间；未采用 finish 时间作为起点，测试保证回读完成前及同秒到达的新回复可进入候选 |
| CLI/文案 | 模式同步 auto_public；关闭提示明确包含 collection_management；所有写操作默认值及审批要求保持不变 |

### 假设 / 待确认

- 未给普通弹幕 `/x/v2/dm/post` 盲目加入 `plat=1`：所核对文档中该字段属于另一个互动弹幕 `/x/v2/dm/command/post`；如小号实测提供普通弹幕的明确参数契约，再增量调整。
- 未增加以 finish 时间定义的 executed_at 列，避免漏掉发送与回读之间的新回复；旧迁移记录的 created 仍可能是旧提议时间，按原文说明保守处理。
- 缺 buvid3 不等于账号已登出；SPI 失败只给本地固定错误，不把匿名 SPI 风控算成账号风控。重新扫码不保证修复指纹；需主人核对 status 和环境。
- 恢复要求新版伴随服务可读 operations 摘要；服务离线时不清理。人工 verified 仅记主人决定，不更改机器回读证据，也不自动清除原目标/相同参数的未知动作保护、停止轮次或冷却。
- 继续现有 `codex/bili-interact-v05` 分支，本轮未合并 origin/main 新增提交。未修改真实 control/cookies/账本，不 push、不部署、不操作云端。

### 本轮改动文件

`deploy/bili_activity/{bili_api,service,ops}.py`；`plugin/astrbot_plugin_bili_activity/{core,interactions,main,replies}.py`；`tests/test_bili_{recovery,contract_fixes,interact,interact_modes,replies,service}.py`；根 README/CHANGELOG、部署 README、本文。

已逐段核对 Executor 源码与本轮修复前一致，Store.propose/offered/decide 的 AST 不变；transport、life、budget、agent_budget、proxy 和 persona 与本轮修复前无差异。新增恢复/accepted 终态位于原单未决索引之外，保留未解决 uncertain 的阻断，不删除审批门。

### 本轮最终验证记录

共 365 项 = 本轮前 328 项 + 新增 37 项（恢复 14、接口契约 22、监听时间 1）。已有测试同时更新 Web 点赞的独立预期值、评论 WBI 路径、匿名可见性语义、附带点赞语义和 CLI 模式联锁。测试全部使用 fake、临时数据库或本机 mock HTTP，没有真实 B站写入。

| 环境 / 命令 | 最终结果 | 本地日志 |
| --- | --- | --- |
| Windows：`C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe -m unittest discover -s tests -p "test_bili_*.py" -v`（Git bin 前置 PATH） | 365 项，354 通过、11 skip，0 失败/错误；21.176 秒 | `bili-fixes-windows.log` |
| 本机 WSL：`wsl -d Ubuntu-22.04 --cd /mnt/d/GitHub/dafeiyu-astrbot -- /var/tmp/dafeiyu-v05/venv/bin/python -m unittest discover -s tests -p 'test_bili_*.py' -v` | 365 项，359 通过、6 skip，0 失败/错误；21.486 秒 | `bili-fixes-linux.log` |
| `python -m compileall -q plugin/astrbot_plugin_bili_activity deploy/bili_activity tests` | 通过 | 标准输出为空 |
| `git diff --check` | 通过；仅 Windows LF/CRLF 提示 | 无空白错误 |
| 本轮修复前 `dae5caf` 与当前源码逐段/AST 比较 | Executor 原文、Store.propose/offered/decide 不变；保护文件无差异 | 本地检查输出 |

Windows 跳过：6 项 ffmpeg、4 项 Linux 导出脚本、1 项符号链接；Linux 跳过：6 项 ffmpeg。未安装新测试框架或新生产依赖。中间测试发现的异常类 fixture 身份、工具清单/缩进、WBI 示例参数拼写及配额 fixture 未结束动作问题均已修正并由最终全量覆盖；没有把中间失败记成通过。

实际 B站 Web 参数、WBI 签名受理、SPI 指纹可用性、匿名列表可用性、QQ 真回执、真人公开可见性全部仍为**未验证**。主人手动 smoke 见前述 1–10 项；本地测试与社区文档不替代小号验收。先保持 approval 和低额度，确认问题后再自行部署/推送。

本轮代码提交：`5f9c32a` 恢复与执行状态机 → `fde49d3` Web 参数、回读语义与回归测试；随后单独提交本报告及 README/CHANGELOG。仅本地提交，未 push/PR/Release/部署。
