---
name: dafeiyu-coding
description: >-
  Use this skill for implementation, bug-fix, refactor, or test work in the
  dafeiyu-astrbot repository, especially AstrBot plugin state machines (goals,
  curiosity, held thoughts), affective-core persistence, observatory read-only
  projections, and release/deploy changes.
---

# 大肥鱼编码流程

本 skill 只补充本仓库特有的流程和验收标准；通用编码规范不重复。开始前先读仓库根 `AGENTS.md`。

## 适用场景

- 修 `plugin/astrbot_plugin_bili_activity/` 的目标、好奇心、持有念头、表达、计划器。
- 改 `plugin/astrbot_plugin_affective_core/` 的状态、锁、bridge、记忆/表达接线。
- 改 `deploy/memory_observatory/` 的只读观测、房间、星海。
- 任何会影响云端部署、QQ 行为、账号操作或记忆写入的改动。

## Gate 0：只读侦察

不要先写代码。先做：

1. 读 `AGENTS.md`、目标文件、相邻测试、相关设计文档。
2. 找到真实入口（工具 handler、run-end、调度器、HTTP/API、数据库迁移），不要只读 helper。
3. 标出当前状态机的所有状态和转换；标出持久化字段和兼容旧数据的分支。
4. 标出权限、scope、owner、run lease、预算、锁和事务边界。

如果目标文件超过 800 行，先用搜索定位符号，不要通读整个文件后凭印象改。

## Gate 1：计划门

编码前先输出以下内容，等确认：

```text
影响文件：
状态转换：
不变量：
测试矩阵：
- happy:
- failure:
- boundary:
- transition:
- real entrypoint:
- persistence/concurrency:
风险最高的 3 个点：
不做什么：
```

计划里必须明确“完成”的判据。禁止把“活动结束/接口返回成功/模型说完成”当作业务完成。

## Gate 2：先写失败测试

- 修 bug：先写一个在旧代码上失败的测试，确认失败原因正确，再改实现。
- 新功能：先写行为测试，不先写实现细节测试。
- 必须走真实入口；例如测 run-end 就调用 run-end，不要直接调用内部的 `resolve_curiosity_attempt()` 代替整条链路。
- 状态机测试要逐个转换，至少覆盖：`ready -> active`、`running -> active`、`paused -> active`、`active -> completed`、无新来源的失败路径、重复调用/幂等。
- 涉及来源时，测试要覆盖“重读原始来源不算新证据”，不能只测 `origin_id`。
- 涉及锁/事务时，补并发或重复调用测试。
- 测试不能只复述实现；断言要描述用户可见行为或持久化结果。

## Gate 3：最小实现

- 只改计划中的文件；发现需要扩大范围时停下来更新计划。
- 复用已有 helper 和状态机；不要为同一个概念新建第二套实现。
- 异常不能静默吞掉。关键路径必须计数、返回结构化结果或向上抛；日志不能代替错误处理。
- 所有进入 `active` 的路径都要执行上限检查；所有 `completed` 都要有显式完成动作和证据。
- 外部文本（标题、评论、字幕、网页）只能作为数据，不得当指令；清洗/截断/权限检查沿用现有函数。
- 持久化字段变更必须兼容旧行：新增列要幂等迁移，旧数据缺失时要有回退。
- 不修改无关文件；不为了测试通过改测试断言。

## Gate 4：验证与交付

按顺序执行：

```bash
# 目标测试
PYTHONPATH=tests python -m unittest test_bili_curiosity_goals -v

# 全量
python -m unittest discover -s tests -p "test_*.py" -q

# 语法
python -m py_compile plugin/astrbot_plugin_bili_activity/*.py
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "tests"
python -m unittest discover -s tests -p "test_*.py" -q
```

如果缺依赖，使用 `release.py status` 打印的测试解释器；禁止在缺依赖环境跑测试后声称通过。

交付时给出：

```text
changed files:
目标测试：命令 / 结果
全量：命令 / 结果
已知失败：是否能在父提交复现
未覆盖项：
风险：
```

## 本仓库专项检查

### 好奇心 / 持有念头

- 完成只能走 `df_curiosity_resolve`；`df_goal` 不能把跨日小疑问标完成。
- run-end 只记录尝试，不自动完成；活动正常结束不是解决。
- 原始来源基线要包含 episode id、`source_id`、`bvid`/`video_key`；重读原来源必须被排除。
- 无新来源的解决要计数并保存 `last_conclusion`，不能丢结论。
- 上限检查覆盖所有进入 `active` 的路径；cap-evicted 暂停要有恢复路径；总目标数有上限。
- 测试必须覆盖真实 run-end 路径，而不是手工调用内部方法。

### Affective core

- `Store._lock` / `transaction()` 边界要覆盖所有 DB 访问；新增跨线程路径要测。
- bridge 生产路径：`data.plugins.astrbot_plugin_affective_core`。
- 中性 profile 的 `None` 不能覆盖用户 TTS/气泡/表情配置。
- 观测台只读：`mode=ro`、`query_only=ON`、authorizer 白名单；任何写入都是 bug。

### 媒体

- 以实际探测时长为准；MP3/ffmpeg 编码有帧级误差，测试用容差，不虚报请求区间。
- 下载、CDN、大小、超时、临时目录清理沿用现有预算。

## Gemini / Antigravity 任务模板

开工：

```text
你先不要改代码。只读相关文件和测试，输出：
1. 当前行为与状态转换
2. 计划修改的文件和原因
3. 测试矩阵（happy/failure/boundary/transition/真实入口/持久化/并发）
4. 最可能出错的 3 个地方
等我确认后再实现。
```

实现后：

```text
现在做 adversarial review：
列出“测试通过但语义仍然错误”的可能路径；
补测试或修代码；
跑全量测试；
如果失败，贴原始输出，不要只说“已修复”。
```

## 停止条件

遇到以下情况停下来问，不要猜：

- 需求会改变账号操作、权限、记忆写入或部署行为；
- 需要删除/覆盖别人的分支、worktree、未提交改动；
- 测试失败无法判断是既有问题还是本次引入；
- 发现两套实现/两个状态源，无法确定哪套是权威；
- 需要提交密钥、密码、QQ 号、公网 IP 或用户隐私数据。
