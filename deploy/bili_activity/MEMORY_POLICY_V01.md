# 经历记忆 v0.1：巩固与渐忘

本地实现、默认关闭、未部署。此阶段改变大肥鱼生活经历的默认检索与长期记忆入队策略；不是全库删除器，也不是人的神经系统。真实 LivingMemory 写入、召回效果与长期运行尚未验证。

## 已有能力核对

| 已有代码 | 已有能力 | 本阶段补充 |
| --- | --- | --- |
| `life.py` episodes / interests | 来源、修订链、会话隔离；兴趣已有独立来源判重与60天半衰减 | 不改原表与兴趣算法；在查询外增加细节/概要/淡出层 |
| `main.py` flush_memory + `core.py` memories | 使用真实目标事件调用 `memorize_long_term_memory`，pending/sending/saved/failed/uncertain | 沿用队列；新增巩固快照、派发前核对来源及开关、条件领取避免并发重发 |
| LivingMemory 原生引擎 | 重要度、访问强化、每日衰减、原生整合与归档 | 不再叠加全库衰减，不直接改其 SQLite/向量/图索引 |
| 记忆星海 | 只读同步、来源关系、遗忘曲线模拟 | 保留现有60秒同步；图上曲线仍是模拟，不当作本策略已执行的证据 |

核对来源：仓库已有的 LivingMemory **2.7.0-beta.2** 源码检查副本（`core_managers_memory_engine_crud.py`、`core_page_api_modules_consolidation_handler.py`，非真实记忆数据库），以及上游公开的 [MemoryMemorizeTool](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/blob/master/core/tools/memory_memorize_tool.py)（此次读取 blob `0169bbde10b7db348dc6db8dacd26e8cd93373c5`）。公开 master 与实际安装版本可能不同，现场工具兼容性仍须验收。

工具接受 `memory/topics/reason`，从传入事件解析会话与人格，成功返回 `memorized: true` 和记忆 ID。`reason` 不是上游幂等承诺；本地不靠它自动重试。原生 `run_consolidation(force=True)` 会绕过冷却，本阶段不调用；内容更新可能改变 ID 并同步多种索引，本阶段不直接更新上游记录。

## 行为

默认 `life_memory_enabled=false`，旧配置缺键可加载。开启且 `life_enabled=true` 后：

1. 普通经历前 **7天**显示细节；第7天起显示概要与来源；第 **60天**起淡出无关键词的默认回忆。这是分阶段检索规则，不是生物遗忘曲线。
2. 主人明确表达的偏好、未完成目标的证据、已检查作品及方法尝试证据保留。检查仍不证明事实正确；原始记录和现有兴趣置信描述不被改写。
3. `df_recall(query=...)` 可找到淡出记录的概要；`df_recall(episode_id=...)` 按会话查完整证据，包括原记录被谁更正。查询/状态查看不增加强化次数，不写表。
4. 生活轮次与主对话的近期经历提示统一使用上述检索，旧视频缓存/运行总结不再绕过它注入细节。未完成目标、作品正文及现有 LivingMemory 的独立召回仍按各自规则工作。
5. 开启时，普通视频笔记、单次兴趣、模型运行总结只保存在本地经历账本，不逐条导出为长期记忆。主人明确偏好、已检查作品、方法尝试仍沿原链入队。旧的 pending 记录不被偷偷删除，可能继续按原链处理。

概要为确定性模板：来源类型/ID/视频标识，以及明确标注的“当时主观感想”。不新调用模型概括，不把评论、模型想法或重复查询升级成事实。

## 巩固与 LivingMemory 桥

- 本版自动巩固只处理**有实际视频读取支持的自身兴趣**。先核验 interest 所指的 video/video_note 存在于同一会话、有画面观察或字幕、标识一致、未被修订；模型运行总结不算来源。
- 同一主题按视频 bvid 判重；同视频不同分P、重述、反复查看都不是独立证据。同一视频采用最近立场；不同视频证据有冲突时不生成稳定立场快照。
- 累积 **2 / 4 / 8 个不同视频**时各可形成一份有日期和来源的快照。每个会话/主题/立场/档位只有一个稳定队列键，每会话至少间隔24小时才能新建下一份。档位封顶8，避免无限堆叠。
- 只在已有 `flush_memory` 入口尝试入队，工具缺失/停用不创建新条目；不新增定时器、模型、外部请求或QQ消息。`enabled=false` 不生成/派发新的巩固快照。
- 使用真正的主人目标事件调用 LivingMemory，身份/会话不匹配不派发。发送前再检查开关和来源；失效 pending 标为 `stale`；条件领取确保并发调用只派发一次。
- 只有 `memorized is True` 记 `saved`；这是**工具确认，不是独立回读或可召回证明**。异常/取消记 `uncertain`，否定结果记 `failed`；均不自动重试。重启仍沿用 Store 的 sending→uncertain 恢复。
- `memory_consolidation_queued` 事件仅记本地ID/来源ID；`memory_consolidation_result` 记状态及符合字符白名单的上游ID。事件不记录正文、完整响应、URL或异常文本。

更正会阻止尚未派发的旧快照；已经写进 LivingMemory 的历史快照不会自动撤回。相同档位失败/不确定/失效后也不改键重发，需人工核对。新增证据达到更高档位可形成新的带来源快照，但不改变之前的未知状态。

## 配置、查看、回滚

| 配置 | 默认 | 范围 |
| --- | --- | --- |
| `life_memory_enabled` | false | 布尔值；需先开启生活功能且完成既有一次性活动验收 |
| `life_memory_detail_days` | 7 | 1..90 整数 |
| `life_memory_dormant_days` | 60 | 1..365 整数，严格大于细节保留期 |

以下仅为日后明确部署后的操作示例，本次未执行。先备份并核对目标数据目录，保持公开写开关关闭。`ops.py` 沿用原配置备份机制：

```bash
python3 ops.py --data-dir <测试数据目录> configure --life-memory-detail-days 7 --life-memory-dormant-days 60
python3 ops.py --data-dir <测试数据目录> switch life_memory_enabled on
python3 ops.py --data-dir <测试数据目录> switch life_memory_enabled off
```

`df_life_status.memory_policy` 返回启用状态、配置、最近最多500条未被修订经历的阶段计数、最近5份巩固状态。计数是有界窗口，非全库统计；关闭时仍可看规则下的预估分布。`ops.py logs` 可查写入结果事件。

关闭即恢复原 `df_recall` 与近期记录注入方式、普通笔记导出方式，并停止新增巩固快照的派发；未发送快照保留，重新开启时再校验。不删除证据、旧配置或上游已保存内容；无需回滚数据库模式。开关不能撤回已经开始的上游调用。

## 验证

Windows 临时测试环境沿用仓库现有依赖，无新增运行时依赖。所有账户/模型/LivingMemory 均用 fake/mock；数据库只建在临时目录。

```powershell
$env:Path='C:\Program Files\Git\bin;' + $env:Path
$testPython='C:\Users\ACER\AppData\Local\Temp\dafeiyu-rhythm-venv\Scripts\python.exe'
& $testPython -B -m unittest discover -s tests -p 'test_bili_memory_policy.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_bili_*.py' -v
```

| 检查 | 实际结果 |
| --- | --- |
| 修改前基线 `test_bili_*.py` | 457项，23.989秒，OK（11 skip） |
| 新增 `test_bili_memory_policy.py` | 28项，0.844秒，OK（0 skip） |
| 修改后 `test_bili_*.py` | 485项，25.251秒，OK（11 skip） |
| `git diff --check` | 通过 |
| 原 proposal/Executor/core/life/transport/budget/agent_budget/workshop/methods/proxy/phone_video/media/login/persona/手机启动脚本 | 无改动 |

11个跳过与基线相同：6项缺ffmpeg、1项Windows符号链接权限、4项Linux限定安全导出。原始输出在本机临时目录 `dafeiyu-memory-policy-baseline.log`、`dafeiyu-memory-policy-final.log`。WSL Ubuntu22.04 可启动，但 Python 导入依赖失败：`ModuleNotFoundError: No module named 'mcp'`；未补装环境，Linux测试 **UNVERIFIED**。本次未改前端，未重跑浏览器或重新验证星海连接。

关键断言：7/60天边界、概要不含原响应、ID查询保留细节且不写库、保护目标/主人偏好、修订链、单视频多次/分P不巩固、空观察/跨会话/混合立场不巩固、间隔与档位上限、重启未知不重试、真实事件透传、并发只调用一次、取消与否定回执、派发等待期间关闭、旧配置与关闭回退、前台/后台提示均不漏出旧细节。

## 现场待验收与限制

1. 在隔离测试会话/人格开启本策略，确认实际 `memorize_long_term_memory` 接口兼容；只读核对工具回执ID是否出现在同一会话/人格的 LivingMemory 记录中。
2. 用两段不同内容留下实际读取与自身兴趣；同一视频重复读取不新增独立证据。检查快照来源，确认不含对主人偏好的猜测。
3. 用测试库的历史时间验证细节→概要→淡出；按ID能取原始证据，未完成任务仍能找到来源。不要修改真实经历时间来做测试。
4. 关闭开关，确认无新巩固派发且原查询恢复；不确定结果需人工核账，不补发。保持QQ分享和公开账号写入关闭即可完成记忆验收。

真实上游写入/检索、QQ表达、长期效果、Linux均 **UNVERIFIED**。本地策略不限制 LivingMemory 独立召回已存内容；也不把星海模拟、图上大小或角色动画当成真实巩固。候选/状态最多扫描500条，默认查询从原查询最近50条筛选；大量记录时不承诺覆盖全部历史。更广泛的经历主题归纳、上游历史快照修订和图上真实生命周期展示留待后续。

改动仅 `memory_policy.py`、`main.py`、`ops.py`、新增测试及说明文档；无数据库迁移、无新依赖、不 push、不部署、不改真实配置。
