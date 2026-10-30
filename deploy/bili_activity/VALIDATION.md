# 本次验证与部署记录

## 2026-10-25 v0.3.0 上线与真实 smoke

代码已合并到 main 并部署到华为云 `/opt/dafeiyu`：

- PR #2 `32f8349`：v0.3.0 首次进入 main
- PR #4 `8781ac8`：工具 JSON 参数兼容
- PR #5 `6ddacd6`：多词 recall
- PR #6 `f088fdd`：无空格中文二字窗口
- 部署备份：`/opt/dafeiyu/backups/bili-activity-20261025T130419.236083Z.tar.gz`

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| Windows `test_bili_*.py` | **215 项 OK，11 跳过** | mcp 1.26；Linux-only export 测试按设计跳过 |
| Linux `test_bili_*.py` | **215 项 OK，6 跳过** | 云主机临时目录；export/符号链接测试实际执行；ffmpeg 缺失跳过 |
| AstrBot 插件加载 | 通过 | v4.29.0，`astrbot_plugin_bili_activity v0.3.0`，12 个 `df_*` 工具 |
| SQLite 迁移 | 通过 | `user_version=1`，goals/episodes/activity_budget_* 存在 |
| QQ / WS | 通过 | 小号 `<小号QQ>` 恢复；`aiocqhttp` 已连接 |

真实 smoke 证据：

- 失败 read `invalid_test`：只写 `read_failed`（`operation=invalid_test`），`read` episode 为 0；
- 群聊 `<测试群>`：无 `list_devices`，`df_screen` 被“仅主人私聊”拒绝；
- life run `122ca7125a7a4635be2b3fbaac4555fa`：completed；目标 `def033f00f824c4dabf67c3d650a8330`；2 条视频 + 弹幕/评论；截图回执 `message_id=1693155839`、`verified=true`、`image_readback=true`、`recipient_confirmed=false`；预算约 65 秒/12 次模型/1 分享/1 截图；episodes 10（read 2）；`video_verified=false`，未冒充手机正在播放；
- workshop：work `f99232fccadb4d64998a3542822c31ff`，revision 1，4 个来源，三节结构，`passed=true`；
- export：`works/f992.../r1.md`，19,117 字节，sha256 `6e055c193d626a234ca928db74b7fa57c5bbb89af88e3ec5fd2c934eee742806`；
- share：`message_id=1161581486`，`status=sent`，`qq_file_sent=false`（只发摘录）；
- 手机离线写作 run `36e9340ed6b34b5c974e7bebcdbba9dd`：completed；读回 r1，`df_recall` 空时调用 `need_sources` 退回 research，未硬凑新版本；无 PhoneMCP 调用；
- recall 修复后 owner 私聊 `query=比格自助三国杀` 已返回多条 episode（含势赵云视频笔记/read）。

当前开关：`enabled=true`、`periodic_enabled=true`、`life_enabled=true`、`workshop_enabled=true`、`one_shot_verified=true`。

尚未验收：跨日预算、真实账号写入/提议、LivingMemory 实际保存/召回、`browser-use` MCP；`df_recall` 仍是词面检索（已支持多词/无空格中文），不宣称语义召回。其他插件日志中的知识库 400 错误不计入本插件验收。

## 2026-10-25 v0.4 合并到当前 main 修复基线（本地）

在 `codex/v04-rebased`（`main-current=6da07f3`）上，以共同祖先 `f7065fe` 三方合并 `v04-import=4ff19b6`。常规 `git merge --no-commit --no-ff v04-import` 因 `.git/ORIG_HEAD.lock` 写入被拒绝而未建立合并状态；随后仅完成工作树文件合并，没有绕过 `.git` 权限、创建提交、push 或部署。

本地 `6da07f3` 尚未包含文档提交 `0506d8f`（`codex/docs-20261025`），因此一并纳入该提交的真实 smoke 和 215 项测试记录。文档冲突保留完整 smoke 段落，再追加 methods 说明；旧 202/243 项及未部署说明只保留为原始分支历史。

- `main.py` 在当前实现上追加 methods 工具、开关、恢复和结果核验；保留 owner/raw Bili 工具隔离、失败 read、目标状态默认值、作品创建 key、share 配文前置校验、JSON 参数兼容和 `runs` 插入的 try 范围。
- `life.py`、`workshop.py` 及原有 14 个 `test_bili_*.py` 与当前 main 内容一致，包含失败来源拒绝、目标完成转换、多词/CJK recall、检查事务和 Windows export 跳过逻辑。
- 新增 `methods.py`、`LIFE_V04.md`、三份 methods 测试及 ops 查询/开关。`df_method.steps` 使用 `parse_json_argument(steps, "steps", list)`；额外两项回归验证列表创建/修订、字符串与列表幂等，以及非数组输入不落库。

使用指定解释器 `C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe`，mcp **1.26.0**：

```powershell
python.exe -m unittest discover -s tests -p "test_bili_*.py"
```

| 检查 | 本次实际结果 | 范围与限制 |
| --- | --- | --- |
| 全量测试 | **258 项：246 通过、11 跳过、1 失败，0 error** | 215 项基线 + 41 项 methods + 2 项参数回归；13.625 秒 |
| 唯一失败 | `test_deploy_preserves_config_and_archives_live_database` | Git Bash 启动即报 `CreateFileMapping … Win32 error 5`，未进入部署脚本；不属于业务断言不符 |
| 跳过 | 6 项 ffmpeg、4 项 Linux export、1 项 Windows symlink | 保持现有跳过条件，没有为本次失败添加 skip |
| 源码保全与语法 | 通过 | `life.py`、`workshop.py`、原有 14 份测试内容比对；新增/修改 Python 的 AST 解析；`git diff --check` |

首次直接运行受 Python 3.12.15 的 Windows `tempfile` 私有目录 ACL 与沙箱令牌不兼容影响，SQLite 无法打开临时库。上表复跑将 `TEMP`/`TMP` 指向工作区 `.test-tmp`，通过本地 `sitecustomize.py` 仅让 `tempfile` 在该目录内创建的子目录继承工作区 ACL；没有修改应用 `os.mkdir`、既有 ACL、测试断言或 skip 条件。测试日志为工作区 `v04-merge-tests.log`。首次失败产生的空临时目录因权限限制无法清理，保留在 `.test-tmp`；本地探针 `.probe-private` 同样为空且无法清理。

结论：methods 与已有修复回归通过，但本次全量不是全绿，需在可正常启动 Git Bash 的环境重跑部署测试。v0.4 methods 尚未执行生产 Agent/QQ/LivingMemory 或跨日验收；上方 v0.3.0 的真实 smoke 和 215 项历史结论保持有效，不将它们冒充本次新测试结果。

## 2026-10-25 研究方法复用 v0.4 原始代码交付（导入前历史）

以下为 v0.4 导入树原有的开发记录，基于旧 v0.3 提交 `f7065fe61e6a631c33c10a38fe4604caaa78aada`，开发分支 `codex/research-methods-v04-20261025`。其中 202/243 项仅代表原始分支的历史测试，不能代替上方当前主线的 215 项基线或本次合并后的回归结果；“未升级生产”仅指这次 methods 开发，不否定 v0.3.0 已上线的真实 smoke。

新增方法库及 `df_method`：从完成且来源可核对的作品保存研究步骤；在不同目标绑定方法版本与尝试；对尝试后的新作品及不同内容来源回查，保存成功、失败或放弃结果。沿用原生 Agent、目标与作品、模型/时间/分享预算和会话隔离的记忆队列，默认 `methods_enabled=false`。

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| 恢复后重跑 v0.3 的 `test_bili*.py` | 202 项通过 | 验证恢复后的原基线 |
| 完整 `python -m unittest discover -s tests -p 'test_bili*.py'` | **243 项通过** | 原 202 项 + 本次 41 项：方法存储 19、插件集成 13、运维 9 |
| `python -m pip check` | 无损坏依赖 | 复用既有 Python 3.12 venv；缓存目录不可写提示不影响检查 |
| Python `compileall`、`git diff --check` | 通过 | 插件、运维脚本、新增测试和差异格式 |

完整离线流程使用真实控制器和 SQLite：完成来源目标与有引用作品 → 提炼待复用方法 → 新目标选用方法 → 记录不同内容来源 → 保存本次新作品 → 完成目标时自动核验结果 → 方法有效复用次数变为 1 → 写入一次方法结果经历及记忆队列 → 重复结果调用不增加记录。另一个两轮用例在手机锁被前台持有时续写，中间重开数据库，恢复同一尝试和方法版本，最后完成并计数一次。

还验证了：新方法版本独立计数、旧版本恢复不被替换、来源修订后当前有效次数失效、同目标不能切方法刷次数、仅复制旧内容或更换经历 ID 不足以计数、模型自报成功被拒绝、方法退役和关闭开关阻止继续调用、失败记录不算成功、作用域隔离、既有模型额度、只读 CLI 不迁移或恢复目标、新增结构前一致性备份。

模型、QQ 和设备业务响应仍为测试替身；记忆队列落盘不等于 LivingMemory 已保存并召回。方法步骤是文本资料，不是代码执行接口。计数证明有合格的新产物记录，不证明研究方法的因果有效性或作品结论正确。原 MCP SDK 的非致命弃用和类型解析提示仍存在。真实 Agent 自选方法、生产重启跨日运行、QQ 实收、记忆保存/召回均未在本次验收；现场步骤和开关见 [LIFE_V04.md](LIFE_V04.md)。

## 2026-10-24 学习与创作 v0.3 代码交付（历史）

基于已上传的 v0.2 提交 `89d9cca`，开发分支 `codex/creative-life-v03-20261024`。本次在隔离 Linux 工作区实现第二种持续活动：把同一私聊中真实读取的经历整理为文字笔记，保存版本、检验引用、导出 Markdown，并通过原 QQ 接口分享文字摘录。`workshop_enabled=false` 默认关闭，未更改实际生产配置或其他暂停任务。

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| `python -m unittest discover -s tests -p 'test_bili*.py'` | **202 项通过** | v0.2 的 157 项 + 本次 45 项：作品存储 23、插件集成 13、运维 9 |
| `python tests/test_phone_video.py` | 13 项通过 | 原 ADB 导航与视频身份规则 |
| `python tests/test_video_processing.py` | 13 项通过 | 真实本地 ffmpeg/HTTP、模型替身和失败恢复 |
| Python `compileall`、`git diff --check` | 通过 | 插件、部署工具、新增测试和修改格式 |

本轮离线完整流程使用真实插件方法及 SQLite：创建目标与模拟读取经历 → 创建作品 → 写入第 1 版 → 持有前台手机锁时启动后台续写 → 保存第 2 版并检查 → 重开数据库 → 同一目标续写第 3 版 → 最新版核验通过后完成目标 → 作品进展进入记忆队列。模型和 QQ 响应为测试替身；没有把它记作真实自主模型决策、真实 B 站来源或 LivingMemory 已经召回。

另外实际验证了 Markdown 文件落盘及 SHA-256、私聊摘录发送账本与回执处理、相同版本重复发送抑制、未知发送结果不重试、额度拦截后可在允许时首次发送、来源修订后阻止导出/分享/完成、旧有效版本不能替最新无效草稿完成目标、会话隔离、修订冲突、运行中关闭开关、前台暂停保留草稿、数据库新增结构前一致性备份。手机不可用的续写测试确认没有调用 Bili/PhoneMCP，也没有抢占已持有的手机锁。

检查通过只代表笔记结构与来源有效，不代表内容推断得到事实证明。分享发送的是标题和文字摘录，导出路径不是 QQ 文件发送回执。不同作品正文相同时，内容去重可能保守消耗一次分享预算，但不会实际重复发送；同一版本重试不重复扣额。

原有 MCP 测试仍有非致命 SDK 弃用和类型解析提示。新模块按云端 Linux AstrBot 运行设计，未增加 Windows 服务依赖或部署步骤。未执行生产插件重载、真实 Agent 工具循环、QQ 实收、LivingMemory 实际保存/召回、跨日运行；也未实现游戏操作、软件安装或可执行技能自我修改。部署启停和现场验收见 [LIFE_V03.md](LIFE_V03.md)。

## 2026-10-24 自主生活 v0.2 代码交付

本次基于 `main@7025063` 整合 B 站分支 `e70a686`，开发分支为 `codex/autonomous-life-v02-20261023`。在隔离 Linux 工作区完成实现和下列验证；**没有执行生产升级，没有新增真实 QQ/B 站写入或跨日运行验收记录**。10 月 21 日接手 Agent 的部分实机结果保留在下文，不能当成本次新版本的验收。

实现：持久化目标及检查点、按会话追加的历次经历、来源可查的自身兴趣与主人明确偏好、每日活动/模型调用/分享预算、前台手机抢占、暂停恢复和审批状态衔接。新增 `life_enabled=false`，升级不自动启用功能；SQL 迁移前一致性备份旧库，保留原防重和投递账本。

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| `python -m unittest discover -s tests -p 'test_bili*.py'` | 157 项通过 | 原有 89 项 + 新增 68 项；目标/经历 17、预算 18、原生 Provider 计数适配 9、CLI 10、插件流程 14 |
| `python tests/test_phone_video.py` | 13 项通过 | ADB 身份与导航替身 |
| `python tests/test_video_processing.py` | 13 项通过 | 真实本地 ffmpeg/HTTP 与模型替身 |
| `python tests/test_heal_ws.py` | 23 项检查通过 | 主线自愈逻辑回归 |
| `python tests/test_face_rules.py` | 22 项检查通过 | 原有表情与文本规则 |
| `python tests/test_schedule_awareness.py` | 10 项检查通过；生产 DB 检查跳过 | 当前工作区没有生产日程数据库 |
| Python `compileall`、`git diff --check` | 通过 | 新旧插件和部署 Python 文件、差异格式 |

插件流程测试通过真实的方法和临时 SQLite，替换外部模型、QQ、B 站和设备响应。覆盖：创建目标 → 读取视频及字幕 → 保存位置/感想 → 兴趣证据 → 提议 → 主人答复 → 一次执行及核实 → 重开数据库 → 选择同一目标继续。还覆盖静默结束、没有内容证据拒绝完成、前台暂停、操作开关关闭、暂停失败不领取写入、提议过期、重复答复、私聊/群聊隔离及预算耗尽保留部分内容。

对照 AstrBot v4.29 原生接口完成 Provider 包装；只影响后台本轮 Provider，不修改共享前台 Provider。计数范围为 Provider 调用与显式视觉/ASR，不等同于供应商内部 HTTP 请求、LivingMemory 内部调用或精确费用。独立 proactive_chat 暂未共享该预算。更多配置、边界和现场验收步骤见 [LIFE_V02.md](LIFE_V02.md)。

原有测试过程中出现 MCP SDK 的非致命弃用提示，测试仍通过。现场仍需确认 AstrBot 版本、插件加载及新增 3 个工具注册、当前模型真实工具循环和图片回流，再按原一次性验收方式测试新版本。

日期：2026-10-21。执行位置：Codex Linux `/workspace/dafeiyu-astrbot`。源码基线：`46638f9822ec8233090959e419176b640d658da7`。

交付新分支时已同步远程 `main` 的 `e5298bbee0a5de1da260a55a1eb8b91a1c600fc9`，保留其中新增的浏览器服务、手机音频链路、人格与标点修正；本次提交只新增本功能文件。原有源码审查记录仍以各表列出的版本为准。

**结论：新增实现已通过下述本地检查；真实部署、B 站账号联调、真实模型视觉回流和 QQ 图片验收尚未完成。** 本报告不将测试替身算作真实运行。

## 2026-10-21 接手 Agent 实机部署进展

部署位置：华为云 `<云主机>`（AstrBot 4.29.0 容器）+ 本机 Windows（雷电 emulator-5554），分支 `codex/bili-activity-20261021`。

已完成并验证：
- 云端插件已落盘并加载成功：日志出现 `Loading plugin astrbot_plugin_bili_activity`、`Plugin ... (v0.1.0)`，8 个 `df_*` 工具注册；PhoneMCP 重连后 18 个工具可用。
- 云 → Windows Bili MCP 真实往返成功：容器内 `streamablehttp_client(http://172.18.0.1:8011/mcp)` 列到 5 个工具，`bili_phone status` 返回 `online:true`，`current` 返回 `bilibili_not_focused`。
- 本地 `status_bili_service.ps1`：MCP 握手、必需工具、emulator-5554 全部 OK。
- PhoneMCP 已备份并应用 `phone_screenshot.patch`；真实截图返回 1 张 JPEG（720x1280，114 KB，非全黑）。
- 云端 `deploy.sh` 已执行，备份 `/opt/dafeiyu/backups/bili-activity-20261021T124200.554744Z.tar.gz`；`control.json` 已按实际值配置：`owner_scope=qq:FriendMessage:<主人QQ>`、`dnd_ranges=[["23:00","08:00"]]`（取自 proactive_chat 的 23-8），活动默认关闭。

本轮修正（均由实机部署暴露）：
- 端口冲突：云端 `172.18.0.1:8010` 已被 browser-use MCP 占用，Bili 服务全链路改为 **8011**。
- `start_bili_service.ps1` 在 StrictMode 下空数组 `.Count` 会抛 `PropertyNotFoundStrict`，改为 `@(Find-Owned ...)`。
- Windows 缺 `tzdata`，`ZoneInfo("Asia/Shanghai")` 报错；已加入 `deploy/bili_activity/requirements.txt`。
- B 站二维码接口现在返回 `account.bilibili.com`，`login.py` 原白名单只允许 `passport.bilibili.com`；已改为严格双域名白名单。
- Git 检出 CRLF 会让 `deploy.sh` 的 `set -o pipefail` 失效；新增 `.gitattributes` 固定 `*.sh` 为 LF。
- Windows 单测最初有 8 项平台差异失败；已修复 `Executor` 长期持有 SQLite 句柄、deploy 脚本 `PYTHON` 覆盖、`chmod` 与符号链接的平台断言。2026-10-21 复跑 `test_bili_*.py` 89 项通过、1 项跳过，`test_phone_video.py` 13 项通过。

本轮代码优化（2026-10-21 晚）：
- 新增 `preflight.py`：启动前检查 Python、Asia/Shanghai 时区、ffmpeg、ADB 在线与 8011 端口；已接入 `start_bili_service.ps1`
- 周期活动改为随机间隔 `check_minutes ± check_jitter_minutes`（默认 120±60 分钟），`next_check_at` 持久化，开启时不会立即触发
- 插件初始化检查 AstrBot 版本（仅 4.29 验证），原生 Agent 导入失败时给出明确错误
- 新增 `install_bili_autostart.ps1`（用户启动目录，无需管理员）
- `Executor` 改为每次操作独立 SQLite 连接，Windows 句柄可释放

仍待用户参与/联调：
- QQ 小号 `<小号QQ>` 于 2026-10-21 21:52 扫码恢复登录，当天群消息收发正常。
- B 站账号于 2026-10-21 22:00 扫码登录成功：UID `<B站UID>`（`<昵称>`），`bili_read status` 返回 `logged_in:true`，关注列表可读。
- 真实视频链路：`bili_prepare_video` 对 `BV1Jonu6mEYG`（cid 42839704376）取 0–8 秒，下载 21.8 MB，返回 2 帧 + 1 段音频，`covered_range` 与请求一致。
- 仍待完成：被动“截图看看”QQ 收图、一次性自主活动、真实评论/收藏提议与回读、LivingMemory 保存、周期启用。

## 最终本地结果

| 检查 | 最终结果 | 证据与范围 |
| --- | --- | --- |
| `python -m unittest discover -s tests -p 'test_bili_*.py' -v` | 84 项通过，0 失败 | 活动、审批、去重、媒体、MCP、QQ 接口处理与部署运维；约 4.1 秒 |
| `python tests/test_phone_video.py` | 13 项通过，0 失败 | ADB 输出替身下的身份、离线与导航规则 |
| `python tests/test_face_rules.py` | 22 条断言通过 | 同步远程最新标点修正后的规则回归；最初基线为 10 条 |
| `python tests/test_video_processing.py` | 13 项通过，0 失败 | 原有真实 ffmpeg / 本地 HTTP 媒体处理回归 |
| 候选项目 `npm run build`、`npm test` | 构建成功，5 个测试文件、63 项通过 | 固定审查提交，未接入真实账号 |
| `pip check` | 无依赖冲突 | 当前开发 venv，不代表生产 AstrBot 容器依赖已核对 |
| Python `compileall` | 通过 | 新插件与部署 Python 文件 |
| `bash -n` | 通过 | `deploy.sh`、`cloud_service.sh` |
| PhoneMCP `git apply --check` | 通过 | 针对审查的 v0.5.0 提交；没有应用到用户 Windows |
| qrcode / Pillow | 本地 PNG 生成及解码通过 | 离线测试地址，未生成真实登录二维码或登录 |
| Windows PowerShell 脚本 | 未执行 | 当前环境没有 PowerShell、Windows 或 ADB 设备 |
| AstrBot 宿主加载与真实 Agent | 未执行 | 本地插件测试使用 SDK 替身；仅对照了 v4.29.0 原生源码 |

新增测试共 **97 项**。既有测试与候选项目测试分别列出，避免混算实际能力覆盖。

最终日志和当时源文件 SHA-256 清单保存在开发环境：

```text
/workspace/dafeiyu-work/validation/dafeiyu-bili-final-tests.log
/workspace/dafeiyu-work/validation/dafeiyu-phone-final-tests.log
/workspace/dafeiyu-work/validation/dafeiyu-face-final-tests.log
/workspace/dafeiyu-work/validation/dafeiyu-video-final-tests.log
/workspace/dafeiyu-work/validation/bilibili-build.log
/workspace/dafeiyu-work/validation/bilibili-test.log
/workspace/dafeiyu-work/validation/source-sha256.json
```

测试中的 MCP SSE / Streamable HTTP 初始化、列工具和调用使用真实本地服务；媒体测试使用真实 ffmpeg。QQ、B 站、模型、ADB 业务响应采用测试替身。当前 SDK 有非致命弃用/类型解析提示，不能据此省略真实宿主依赖验证。

## 本轮复核修正

- 移除自主 Agent 结束时按旧快照写回聊天历史的行为，避免覆盖活动期间的新聊天；活动通过独立账本及记忆摘要保存。
- 已分析位置需要画面、字幕或转写时间证据；仅有请求区间不能算已看过。
- 内部执行 MCP 工具不直接留给聊天模型；提议目标核对失败时不生成待批准写入。
- 补齐二维码依赖并验证离线生成链路。
- 增加部署文档、各能力的未验边界与运维命令。

期间有一次回归失败：旧测试要求聊天历史写回函数被调用。确认该断言与并发聊天保护冲突后，改为检查不得覆盖旧会话快照，并添加位置证据回归。随后最终 84 项全部通过；没有跳过失败项。

## 修改与保留

新增文件位于 `plugin/astrbot_plugin_bili_activity/`、`deploy/bili_activity/`，以及 `tests/test_bili_*.py`、`tests/test_phone_video.py`。原有受版本控制文件未改动；按用户后续要求，通过独立分支交付给接手 Agent，不修改 `main`，不进行生产部署。初始状态及差异记录位于开发环境 `/workspace/dafeiyu-backups/pre-bilibili.*`；这些开发环境日志和备份路径不随 GitHub ZIP 导出。

现场部署脚本已在临时部署树测试备份、保留已有配置与 SQLite 在线备份；**尚未对生产配置做备份或安装**。默认运行目录和一键启动、停止、日志、各功能开关见 [README.md](README.md)。

沿用 `cloud-environment-onboarding:setup`，已保存开发环境的 `install_script` 和 `start_skill` 草稿，包含新增依赖、分进程测试命令与生产联调入口说明。草稿保存不等于执行、生产部署或环境发布；需在环境设置中审阅、保存并发布后，才能声明新的环境快照可供恢复。当前实例的上述检查已经实际执行，未来新实例恢复尚未验证。

## 真实环境阻塞

复核结果：本机不存在 `/opt/dafeiyu`；没有 `/home/agent/.ssh`；`docker ps` 无运行中容器；没有可调用的 Windows 执行入口。现有 PhoneMCP 的“已接入云端 AstrBot”不等于本 Codex 会话能够访问该云端或 Windows。实际人格、免打扰、QQ 接收人和登录态仍需现场读取。

此前对 B 站导航 API 的只读 HTTPS 请求被本环境代理返回 `403 ClientHttpProxyError`。这只能说明当前开发环境的请求被阻断，不能作为用户本地网络结论。

继续部署所缺的是**可由本会话执行的现有云端/Windows 连接入口**。可恢复原远程执行环境，或提供现有 SSH 主机、端口、用户及已安全配置的密钥路径/执行工具；不要在聊天中发送密钥正文、密码或 Cookie。取得入口后按既有授权继续安装、修复与验证，只有扫码及具体写入提议需要用户参与。

## 要求的完整运行记录

以下各项目前均无真实生产证据，不能补写成成功：

| 阶段 | 状态 | 应保留的真实证据 |
| --- | --- | --- |
| 一次性触发 | 未创建 | `run_id`、Asia/Shanghai 时间、触发来源 |
| 打开并选择视频 | 未执行 | 当前手机 UI 来源、BV/aid/cid/page、身份核对结果 |
| 内容理解 | 未执行 | 画面采样时间、字幕或转写范围、弹幕时间、评论分页 |
| 自主截图分享 | 未执行 | 截图时间/指纹、同轮标识、QQ `message_id`、图片读回、主人可打开确认 |
| 主动具体提议 | 未发送 | `proposal_id`、目标、完整正文/变更内容、发送回执 |
| 主人答复 | 未收到 | 同私聊、同主人、真实 QQ 消息 ID 与时间 |
| 实际写入及核实 | 未执行 | 同操作 ID 的本地账本、B 站返回、重新读取核实 |
| 记忆保存 | 未执行 | 活动摘要、归属标记、LivingMemory 成功返回及实际检索 |
| 周期启用 | 未执行 | 一次性验收证据与开关记录；其他暂停任务状态保全 |

被动“截图看看”同样待 QQ 真实收图验收。发送接口成功或返回图片给模型都不能替代主人收到并能打开图片。
