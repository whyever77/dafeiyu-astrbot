# 跨日生活联查 v0.1

2026-11-03，本地离线验收。将已经实现的生活决策、节律、项目、作品、近况回顾、记忆队列和房间项目册串起来验证；没有增加活动种类、定时器、开关、依赖或真实账号权限。

后续已提供[生活观察报告工具](LIFE_OBSERVATION_V01.md)：指定隔离数据目录与项目ID，汇总阶段、运行日期和记忆队列，便于实际跨日观察。工具读取成功不代表本清单已通过。

**离线结论：PASS。真实模型、AstrBot/QQ、LivingMemory 远端写入与召回、数日主动延续：UNVERIFIED。** 时间在测试中推进，没有宣称已实际观察三天。

## 本轮修复

最终 `df_project review` 会把项目状态改为 `completed`。原生 Agent 在工具返回后再次请求模型时，预算适配器先运行 `check_owner`；原门禁将已完成项目拒绝为 `PermissionError`，导致已经保存最终回看的整轮记录成 `failed`。之前项目测试替换了整个 `agent`，没有覆盖工具之后的模型调用边界。

仅修改控制器 `main.py`：

- 仅当选定回看轮次自己成功保存最终回看，才在该轮内部记录 `closed_here`，工具结果增加 `round_complete=true`。标记不来自模型参数、不写入长期配置。
- 下一次工具或模型调用仍被阻止，使用专门的 `ProjectReviewFinished` 表示正常收尾；不消耗另一次模型额度，运行结果保存为 `completed`。进入此收尾路径时记录 `project_review_run_finished`。
- 身份、总开关、DND、生活暂停、项目开关等检查仍在此判断之前。外部完成项目、关闭活动、普通运行异常不能借此报成成功。收尾标记跟随原 `finally` 清理。
- 未改 proposal、Executor、transport、life、budget、agent_budget、workshop、methods、proxy、phone_video、media、login、人格或手机启动脚本。

## 联查怎么做

新增 `tests/test_bili_life_journey.py`。使用真正的 `run_activity → agent → hook → 预算适配器 → 工具 → 收尾` 控制器链，替换 AstrBot 构造依赖、模型 provider、B站后端和 LivingMemory 工具回执。工具调用脚本明确固定，因此本测试证明持久化、权限、调度与接线，不证明模型会自行作出这些选择。

每项测试使用全新的临时配置和 SQLite；不读取生产配置或凭据。模拟后端只放行读取，真实 MCP 构造和 QQ 发送被禁止。房间适配器读取的就是本次控制器写出的同一临时账本。

| 模拟时间（上海） | 实际测试操作与断言 |
| --- | --- |
| 11月3日 10:00 | 原调度选择探索；真实目标、项目、读取、作品修订和完成工具串联，记录来源ID及作品第1版；2次模型调用，1次模拟 LivingMemory 确认 |
| 同日 11:00 | 尚未到24小时回看期限，前台占用手机时准备门禁直接返回；不新增运行、模型调用或记忆 |
| 同日 23:50 | 生活节律选择休息，不调用模型 |
| 关闭并重开临时账本 | 保留原项目、目标、作品、来源和记忆状态，重建预算对象；不模拟整个 AstrBot 进程重启 |
| 11月4日 11:00 | 原调度选择回看；仅开放4个回看工具，不访问后端；保存收获/局限/下一问题，重复 `advance` 返回同一个 ready 目标 |
| 同日 11:10 | 下一轮才领取第二阶段目标并研究，保存第二份第1版作品；该日共4次模型调用，累计2次模拟记忆确认 |
| 11月5日 12:00 | 第二阶段回看完成；即使配置上限调大到6，旧项目仍按冻结的2阶段收尾；该日只调用模型1次，下一次请求被正常终止 |
| 收尾核对 | 恰好2目标、2作品、2回看；项目册的阶段版本与回看文字一致；近况回顾两项完成依据均为 `checked_work_record`，没有虚假失败记录；反复读取不改账本，反复刷新记忆队列不重复派发 |

三日期总计7次模拟模型调用；记忆队列仅有2条已检查作品，未把运行自述再次写为长期记忆。`saved` 仅表示模拟工具明确确认，未做远端可召回验证。房间投影 `recorded` 仅表示来源与版本记录对应，不表示事实正确。

## 反例和回归证据

首个新增测试在修复前失败：

```text
test_final_review_closes_native_round_without_extra_provider_or_failed_run
AssertionError: 'failed' != 'completed'
Ran 1 test in 0.132s
FAILED (failures=1)
```

仅应用控制器修复后，同一测试：`Ran 1 test in 0.118s / OK`。

| 新增测试 | 证明的边界 |
| --- | --- |
| `test_final_review_closes_native_round_without_extra_provider_or_failed_run` | 自己保存的最终回看正常结束；预算适配器停止下一次 provider 调用，运行收尾清理完成 |
| `test_three_dates_keep_sources_versions_memory_and_room_in_sync` | 上表完整链、跨日预算、重开库、原调度、阶段判重、只读展示和队列不重复派发 |
| `test_external_completion_is_still_denied_at_provider_boundary` | 外部完成不设置本轮标记，继续报权限阻断，不伪装成自己的正常收尾 |
| `test_shutdown_after_saved_review_is_not_overridden_by_normal_finish` | 主人关闭活动优先，已保存回看保留，但后续调用仍因关闭被阻止 |
| `test_final_review_blocks_extra_tools_before_io_even_with_one_call_budget` | 仅1次模型额度也能保存最终回看；随后试图读取被拦在后端调用前 |
| `test_unrelated_runner_error_after_review_is_not_reported_completed` | 保存后发生其他运行错误仍记失败，不吞掉异常 |

扩展到6项时出现过1次测试预期错误：前台占用且无离线任务时，原 `life_ready` 在产生决策前直接返回，不会新增 `rest` 决策。修正测试为断言没有新增运行/调用，另用夜间时钟验证真正的休息决策；生产门禁未改。

Windows 在仓库根目录执行：

```powershell
$env:Path='C:\Program Files\Git\bin;' + $env:Path
$testPython='C:\Users\ACER\AppData\Local\Temp\dafeiyu-rhythm-venv\Scripts\python.exe'
& $testPython -B -m unittest discover -s tests -p 'test_bili_life_journey.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_bili_*.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_memory_*.py' -v
wsl.exe --distribution Ubuntu-22.04 --cd /mnt/d/GitHub/dafeiyu-astrbot -- python3 -B -m unittest discover -s tests -p 'test_memory_*.py' -v
git diff --check
```

| 检查 | 实际结果 |
| --- | --- |
| 修改前 B站模块基线 | 552项，32.952秒，OK（11 skip） |
| 新增联查最终定向 | 6项，0.622秒，OK |
| 修改后 B站模块全量 | 558项，29.092秒，OK（11 skip） |
| Windows 房间/记忆 | 98项，14.587秒，OK |
| WSL Ubuntu 22.04 房间/记忆 | 98项，12.870秒，OK（1 skip：Windows命名互斥） |
| WSL 生活模块 | 未运行；Python 3.10.12 中 `mcp`、`aiohttp`、`PIL` 均不存在，没有安装依赖 |
| `git diff --check` | 通过；只有原有换行转换提示，无空白错误 |
| 受保护模块、人格、手机启动脚本 diff | 空；上一轮项目册工作区改动保留 |

B站模块11项跳过与基线一致：6项缺 ffmpeg、1项 Windows 符号链接权限、4项 Linux 限定安全导出。原始日志在本机临时目录：`dafeiyu-journey-baseline.log`、`dafeiyu-journey-regression.log`、`dafeiyu-journey-memory.log`、`dafeiyu-journey-memory-wsl.log`。定向与红绿结果见本轮命令输出。

本轮未改页面样式、动画或同步代码，未重复浏览器验收；项目册浏览器27项通过记录属于上一轮，见 [PROJECT_SHELF_V01.md](../memory_observatory/PROJECT_SHELF_V01.md)。

## 主人真实验收清单（尚未执行）

先使用独立数据目录和隔离主人私聊，保留原一次性活动验收及权限设置。以下是审阅建议，不是已修改的配置：项目数1、每项目2阶段、回看等待24小时、单轮最多3分钟/6次模型、每日10分钟/12次模型。启用生活决策、作品、项目、节律、经历记忆、近况回顾所需前置开关；不要伪造 `one_shot_verified`。模型若用完额度，应保存进度等待下一轮，不提高额度掩盖问题。

公开互动、点赞投币、收藏关注、回复监听和自动公开模式全部保持关闭，互动模式为 `approval`。初期 `daily_shares=0`，联系协调上限也为0；测试读资料与写自己的作品即可。真实 LivingMemory 写入属于本清单的人工验收步骤，不在此次离线测试中发生。

1. **第一天：**让她从实际读到的资料开始一个有结束条件的小主题。保留项目ID、目标ID、来源ID、作品版本和回读截图；核对角色动画不是完成依据。模型是否自然选择/表达，单独记录原对话。
2. **同日与重启：**完成阶段后未满24小时应没有回看；重启测试实例，确认旧目标/作品仍在。暂停、DND、关闭项目时不能继续自动推进。不要手改时间戳制造真实跨日通过证据。
3. **次日：**原调度到合适窗口才可选择回看；记录收获、局限、下一问题。下一阶段只有一个待做目标，重复 `advance` 不增加目标；研究发生在之后的轮次。
4. **最后一阶段：**达到2阶段上限或留下空问题后，项目与该回看轮次均为完成，不再追加模型调用，也不出现无故“失败”近况。
5. **页面与表达：**房间手动同步后核对项目ID、阶段、版本和回看；在主人相关话题中核对近况表达引用真实经历。星海原有60秒同步不等于房间项目册自动刷新，也不证明每条作品已成为记忆原子。
6. **LivingMemory 与 QQ 分别验收：**在隔离会话明确允许后，核对作品入队、工具返回的上游ID，再从正确会话实际召回；重启/重复刷新不得重复派发。初期零联系额度下不应主动发分享；后续如要验 QQ 回执，另将联系额度调为1，用既有联系链记录真实消息ID，不把口述当作送达。
7. **停止/回退：**关闭项目及活动总开关，确认后台结束，保留账本和已生成作品；不通过全库旧备份覆盖新记录。不要把未知的远端结果自动重发。

记录每一步的实际日期、run_id、项目/作品ID和独立回读结果；某环未执行就标 **UNVERIFIED**。后续优先完成这一小范围运行观察，再决定扩展新的生活活动。

本轮保留上一轮未提交的项目册改动；只新增联查测试与本说明、增量修复控制器并更新索引文档。没有 push、部署、修改真实配置、云端或账号数据。
