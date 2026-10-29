# 生活决策 v0.1：先决定这一轮想做什么

状态：2026-10-29 已部署到云端并开启 planner（代码 `181fa6c` / v2.7.0）；云端 `enabled` 与 `periodic_enabled` 仍为 false，尚未产生真实决策记录。默认配置仍为关闭。
这是自主生活进化清单的第一步：统一现有研究、写作、探索与休息的选择，记录理由并延续进度。
跨 proactive_chat 的联系协调、虚拟房间、作息情绪和真实记忆策略仍为后续工作。

## 本轮会发生什么

沿用现有 `schedule()` 的唤醒、免打扰和周期。满足前台冷却、暂停状态与预算条件后，
新增 `LifePlanner` 从当前主人私聊的目标、作品、自己的兴趣和历史选择中安排这一轮：

| 活动 | 行为 |
|---|---|
| research | 领取一个 ready/paused 目标，重新核对设备，再继续研究它的下一步 |
| writing | 领取已有作品的目标，只使用已保存资料写作；可以在前台占用手机时进行 |
| explore | 依据自己的正向兴趣或一般探索，交给原 Agent 创建一个有结束条件的小目标并读取真实资料 |
| rest | 只记录休息理由；不启动 Agent、不占设备、不预留活动时长、不发 QQ、不写经历或 LivingMemory |

选择是意图，不是已经做过的事。现有原生 AstrBot Agent 仍负责具体研究和写作，可以安静结束。
分享沿用已有独立工具与预算；这一版没有实现跨插件的全局分享额度。

## 选择依据

规则是可解释的工程初始值，不是拟合的人类心理参数，也没有经过真实长期行为评估：

- 未完成目标基础分 60，距更新时间每小时增加 1 分，最多 12 分；匹配自己的正向兴趣增加 8 分。
- 最近三次非休息活动，每次重复选择同一目标扣 12 分。
- 探索基础分 35；有自身兴趣证据增加 8 分；冷却窗口内最近三次都是续做时，探索增加 40 分。
- 默认 180 分钟窗口内，探索最多选择一次；换话题不绕过这一限制。
- 相同目标的内容状态连续两次未变，或上次在相同状态失败、超时、中断、取消，暂不再选。
  有新的内容状态或冷却结束后才重新参与选择。
- 等待审批、blocked、running 和结束状态不参与自动领取；最多 8 个未结束目标的现有约束继续生效。
- 作品功能关闭时不挑选关联作品的目标。作品需要资料时走 research，不假装能离线完成。
- 没有可执行候选时休息。重复休息不会挤掉用于判重的活动历史。

内容状态包含下一步、检查点、来源内容摘要哈希及作品正文哈希/阶段。
事件 ID、截图采集时间、版本号和轮末总结本身不算新进展；相同 JSON 内容仅格式不同也不算变化。
每个目标最多检查最近 200 条有效类别记录、取 8 种不同内容；这不是语义级查重，改写文字仍可能被视作变化。
分数、候选、排除理由及引用的经历 ID 都会保存，后续可根据真实运行记录调整策略。

## 配置与查看

`control.json` 缺少新键时补默认值，不覆盖旧配置。新配置：

| 配置 | 默认 | 含义 |
|---|---|---|
| `life_decision_enabled` | false | 使用新的生活选择器；同时需要 life_enabled |
| `life_repeat_cooldown_minutes` | 180 | 重复/失败与探索冷却窗口，整数 15–1440 |

以下为云端操作示例；2026-10-29 已执行 planner 开关，未改动 `activity`/`periodic`：

```bash
# 需要已核对主人私聊、免打扰、已有一次性活动验收，并已开启 life_enabled。
# 此开关不会自动启动活动、打开周期、开启作品或放行账号写入。
python3 /opt/dafeiyu/tools/bili_activity/ops.py switch life_decision_enabled on
python3 /opt/dafeiyu/tools/bili_activity/ops.py configure --life-repeat-cooldown-minutes 180

# 查询均只读，不迁移数据库、不恢复运行中的目标。
python3 /opt/dafeiyu/tools/bili_activity/ops.py life-decisions --limit 8
python3 /opt/dafeiyu/tools/bili_activity/ops.py status

# 关闭后停止正在使用决策器的活动并保留进度；后续唤醒恢复原有选择方式。
python3 /opt/dafeiyu/tools/bili_activity/ops.py switch life_decision_enabled off
```

主人私聊可让模型调用 `df_life_status` 查看最近选择、理由和对应运行结果。
只有当前主人私聊可查询，不向群聊或其他用户暴露这些记录。
`enabled` 表示功能开关组合，`activity_enabled / periodic_enabled / paused` 分别报告活动总开关、周期和暂停。
`run_status=completed` 只表示该轮 Agent 正常返回，不证明目标完成；目标和作品完成仍按原有证据检查。
休息记录的 `run_status=rested` 不对应一条执行成功的活动。

## 部署记录（2026-10-29）

- 代码：`181fa6c`（v2.7.0）；云端插件日志版本仍显示 `v0.5.0`（本次未改插件版本号）。
- 部署：`release.py deploy --yes`；备份 `/opt/dafeiyu/backups/bili-activity-20261029T135732.428194Z.tar.gz`。
- 开关：`life_decision_enabled=true`、`life_repeat_cooldown_minutes=180`；前置 `life_enabled=true`、`workshop_enabled=true`、`one_shot_verified=true`。
- 未开启：`enabled=false`、`periodic_enabled=false`，所以 planner 暂时不会触发；`life-decisions` 查询为空。
- 插件已加载、适配器已连接；未改人格、cron 或其他配置。

## 实现与恢复

- 新增 `planner.py`，只有 SQLite 读取和选择规则，没有模型、网络、设备操作或新调度器。
- `main.py` 在原 `run_activity()` 内接入选择与目标领取；状态工具和运行中关闭检查增量加入。
- `ops.py` 增加开关、冷却参数和只读选择查询。
- 使用现有 `events` 表：`life_decision` 保存活动选择，`life_rest` 保存休息；运行结果从原 `runs` 表回查。
- 没有新增数据库结构或修改原 proposal、Executor、transport、life、budget、agent_budget、workshop、methods。
- 关闭新功能后恢复旧的写作优先流程；要停止所有自主活动，仍用原有 stop 或暂停命令。
- 不回滚原库来清空历史，不删除审批、幂等账本或发送回执。

## 离线验收

新增 `tests/test_bili_planner.py`，使用现有 unittest 和临时 SQLite、fake 模型、fake B站及 QQ。

| 验收项 | 代表测试 |
|---|---|
| 选择不创造经历，既有目标优先 | empty_life_selects_bounded_exploration / unfinished_goal_beats_generic_exploration |
| 自身兴趣与主人喜好分离 | only_own_positive_interest_can_seed_exploration |
| 待审批/blocked/其他会话不被恢复 | blocked_approval_and_other_scope_are_never_resumed |
| 无进展后休息，新证据可恢复 | two_unchanged_attempts_rest_then_new_evidence_unlocks_goal |
| 重复观察不冒充进展 | rereading_same_video_with_new_ids_and_times / duplicate_read_json_and_failed_legacy_reads |
| 休息零模型/设备/投递/假记忆 | rest_has_no_model_device_send_reservation_or_fake_memory |
| 新旧流程和恢复 | old_config_disables_planner / history_survives_restart / cancel_preserves_progress |
| 前台、免打扰、开关和预算 | offline_writing_keeps_foreground_phone_lease / dnd_pause_and_chat_cooldown / exhausted_budget |
| CLI 不扩大权限、只读查询 | switch_requires_existing_life_acceptance / missing_state_queries_are_readonly |

Windows 基线：374 项，20.023 秒，OK（11 skip）。新增定向：29 项，0.946 秒，OK（0 skip）。
最终全量：403 项，20.838 秒，OK（11 skip）；已有测试文件未改动。
跳过项为 6 项需要 ffmpeg、1 项 Windows 符号链接权限、4 项仅支持 Linux 的安全导出。
`git diff --check` 通过，前述受保护模块、人格及手机启动脚本无改动。命令使用已有测试环境：

```powershell
$env:Path = 'C:\Program Files\Git\bin;' + $env:Path
& 'C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_bili_planner.py' -v
& 'C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_bili_*.py' -v
```

本轮检查 WSL 依赖时，`python3 -c "import mcp, aiohttp, PIL"` 返回 `ModuleNotFoundError: No module named 'mcp'`，
未找到此前的临时测试虚拟环境，因此 WSL 全量为 UNVERIFIED；没有把 Windows 结果当作 Linux 结果。

## 主人后续现场验收

1. 2026-10-29 已保留现有配置并单独开启 planner；`enabled/periodic_enabled` 仍关闭，以下现场验收待在打开活动后执行；不新增其他插件定时任务。
2. 准备一个已有来源的小目标和一个草稿，验证选择理由、目标领取、实际内容及下一步一致。
3. 用临时测试目标验证无进展时换方向或休息；检查休息没有 QQ 投递或新长期记忆。
4. 重启后继续同一目标，前台占用手机时仍能离线写作；暂停或关闭后不再发起新工具调用。
5. 问“你最近为什么想做这件事”，答案须对应选择记录和真实经历。

真实模型是否自然采用这些安排、QQ 表达、跨日长期效果及 LivingMemory 实际召回均 UNVERIFIED。
2026-10-29 已部署 v2.7.0 并开启 `life_decision_enabled`；未开启真实周期，未产生真实决策记录，未调用真实账号写接口或发送 QQ。上述现场验收仍未完成。
