# 生活节律 v0.1

2026-11-01，本地实现，默认关闭；未部署或修改真实配置。接续[进化清单](../../docs/进化清单.md)第4步。

## 行为

沿用 `schedule → run_activity → LifePlanner`，不新增调度器或模型。判断采用上海时间，与电脑所在时区无关。

| 时段/条件 | 选择行为 |
| --- | --- |
| 起床后2小时 | 既有研究/写作候选加6分，保留原有进展、重复、审批排除规则 |
| 白天 | 原生活决策照常选择研究、写作或探索 |
| 睡前整理时段 | 只允许已有作品的离线写作；没有合适作品则休息，不启动新的资料探索 |
| 睡眠时段 | 只保存休息选择；不启动 Agent、占用手机、预留活动额度、发送 QQ 或创建经历/长期记忆 |
| 近期活动较多 | 在滚动窗口内累积已结束运行的经过时间；不足1分钟余量时休息，时间自然移出窗口后重新判断 |

失败、取消、超时等已结束的运行也占用过时间；重叠区间只计一次。只统计同一会话，忽略未结束、未来、倒置或无效区间，不把预留额度当成已发生的活动。不在午夜清零近期活动，原上海自然日预算依旧独立执行。

这是一条可解释的调度规则，不是生理疲劳、情绪或亲密度测量。休息不生成“睡过一觉”、梦境或其他虚构生活。阈值是工程初始值，尚未经过长期行为评估。

新活动时长取原单轮上限、原预算、节律剩余时长及距睡眠边界的最小值。准备事件或等待设备也消耗该截止时间；耗尽后保留目标进度，不再启动工作。在途外部请求仍按原取消/未知结果语义处理，不承诺服务端请求会在截止瞬间完成。

## 开关和配置

旧 `control.json` 缺键时采用下表默认值，不覆盖自定义字段。开启还需原 `life_enabled`、`life_decision_enabled` 和一次性活动验收；不会顺带开启活动、周期、写操作或 QQ 投递。

| 配置 | 默认 | 范围 |
| --- | --- | --- |
| `life_rhythm_enabled` | false | 布尔值 |
| `life_sleep_hour` | 23 | 0–23，上海整点 |
| `life_wake_hour` | 8 | 0–23，不能与睡眠时间相同 |
| `life_wind_down_minutes` | 60 | 0–180，短于清醒时段；0关闭睡前整理 |
| `life_activity_window_minutes` | 120 | 30–720 |
| `life_activity_limit_minutes` | 20 | 1–120，不大于观察窗口 |

以下是主人以后明确部署、核对配置后的操作示例，本轮没有执行：

```bash
python3 /opt/dafeiyu/tools/bili_activity/ops.py configure --life-sleep-hour 23 --life-wake-hour 8 --life-wind-down-minutes 60 --life-activity-window-minutes 120 --life-activity-limit-minutes 20
python3 /opt/dafeiyu/tools/bili_activity/ops.py switch life_rhythm_enabled on
python3 /opt/dafeiyu/tools/bili_activity/ops.py life-decisions --limit 8
```

不改主人免打扰：原 DND、暂停、前台留白和预算优先，DND 内没有新唤醒就不会额外制造一条“正在睡觉”的记录。此策略仅接入生活规划轮次，不拦前台聊天，也不改回复监听或主动联系协调的规则。

关闭 `life_rhythm_enabled` 后，后续选择恢复原策略；已经开始的一轮仍遵守开始时的保守截止时间。需要立即停止仍使用现有停止/暂停机制。

## 查看与房间

- `df_life_status` 增加当前节律判断，继续仅主人私聊可读。`enabled`、`activity_enabled`、`paused` 分开；节律允许不等于活动被授权。
- 原 `life_decision` / `life_rest` 事件保存选择时的节律、来源运行ID、活动时长、阈值、候选和排除理由；无需新表或数据库迁移。
- 房间总览/休息角只读投影最近一次选择的节律，标明“选择当时”和时间；不根据浏览器时钟猜当前状态，不把旧记录改成实时状态，不随动画触发活动。
- 活动源未部署本版或尚无新记录时，房间显示未开启/尚无记录。LivingMemory 仍是现有只读连接与60秒同步，未改变强化、访问计数或遗忘策略。
- 最多检查257条近期结束记录用于检测超过256条的异常密度；超出时暂不自动开始活动，等待窗口收敛，不把截断数据称作完整统计。

## 变更范围

新增 `plugin/astrbot_plugin_bili_activity/rhythm.py`；增量接入 `planner.py`、`main.py`、`deploy/bili_activity/ops.py`。房间涉及 `activity_reader.py`、`activity.py`、`static/room.js`，附离线和浏览器测试。

未修改 proposal/Executor、transport、life、budget、agent_budget、workshop、methods、proxy、phone_video、media、login、人格及手机启动脚本。之前已完成的角色图集/绘制变更保留，不属于本阶段的新修改。不 push、不部署、不连接真实账号或云端。

## 验证

测试环境：临时 `dafeiyu-rhythm-venv`，Python3.12；安装仓库已有 `deploy/bili_activity/requirements.txt`，未新增项目依赖。Windows PATH 优先 Git Bash。

初次系统 Python 基线因缺少 mcp 和误选 WSL Bash 失败；旧临时环境也缺失 mcp.types。新建隔离环境后的代码基线为435项，23.652秒，OK（11 skip）。保留环境错误记录，不将其计为有效基线。

```powershell
$env:Path='C:\Program Files\Git\bin;' + $env:Path
$testPython='C:\Users\ACER\AppData\Local\Temp\dafeiyu-rhythm-venv\Scripts\python.exe'
& $testPython -B -m unittest discover -s tests -p 'test_bili_rhythm.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_bili_*.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_memory_*.py' -v
$env:MEMORY_PYTHON=$testPython
node tests/memory_observatory_browser.mjs --spawn-demo
```

最终结果：

| 检查 | 实际结果 |
| --- | --- |
| `test_bili_rhythm.py` | 22项，0.589秒，OK，0 skip |
| `test_bili_*.py` | 457项，21.724秒，OK，11 skip |
| `test_memory_*.py` | 85项，10.855秒，OK，0 skip |
| `memory_observatory_browser.mjs --spawn-demo` | 25项 PASS，0失败，0未捕获异常；只用虚构数据 |
| JS语法与 `git diff --check` | 通过 |
| 受保护代码/人格/手机启动脚本 diff | 空 |

跳过项与基线一致：6项需要ffmpeg，1项需要Windows符号链接权限，4项仅支持Linux安全导出。准备耗时边界补测前全量455项通过；补充该测试与边界检查后复跑最终456项通过。

[虚构场景截图](../memory_observatory/assets/room-rhythm-v01.png) · [浏览器结果](../memory_observatory/assets/room-rhythm-v01-results.json)。原始基线和最终回归输出分别保留在本机临时目录 `dafeiyu-rhythm-baseline-clean.log` 与 `dafeiyu-rhythm-final.log`，不含真实账号数据。

关键反例覆盖：夜间零模型/设备/投递/额度/记忆；没有作品时不造睡前写作；真实运行时间重叠去重、跨午夜不清零、重启保留；其他会话和未来/未完成记录不计入；暂停/DND/主人隔离不被节律绕过；准备耗时跨过休息边界时不启动工作；开关关闭恢复原行为；读取状态与房间不改库；无效CLI参数不落盘、元数据保留。

## 尚未验证与现场验收

真实长期作息效果、模型表达、QQ、云端运行以及本阶段Linux/WSL为 UNVERIFIED。窗口内经过时间不等于专注时长，首版不学习个人生物钟或推断情绪。

主人后续可在独立测试状态中核对：白天继续既有目标；睡前只续写已有作品；睡眠时段无模型/设备/消息；积累活动后的留白；暂停/DND仍优先。再在低额度、公开写关闭的条件下明确部署并开启，观察一次跨日/重启后的选择与记录是否一致。

回滚先关闭节律开关，再按需要恢复上一版代码；无需回滚真实数据库或删配置。下一阶段是记忆巩固与渐忘，需单独确认 LivingMemory 实际能力和可逆写入方案。
