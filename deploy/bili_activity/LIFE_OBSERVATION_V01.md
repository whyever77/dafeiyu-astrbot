# 生活观察报告 v0.1

2026-11-03，本地工具。将[跨日现场验收清单](LIFE_JOURNEY_V01.md)所需的账本信息汇总成一份可保存、可比较的报告。它不启动活动、不替主人验收，也不修改已有控制器。

可以先看[离线模拟报告示例](examples/life-observation.demo.md)。示例来自上一阶段的三日期固定脚本与临时账本，实际没有等待三天；两阶段、四轮活动、七次模拟模型调用、两条模拟记忆确认均可在联查中核对。

## 使用

在仓库根目录运行；必须显式提供**隔离测试目录**和已有项目ID（从 `df_project list` 查询）。不会默认读取云端目录，也不会创建空数据库。

```powershell
python -B -m deploy.bili_activity.life_observation --data-dir "<隔离测试数据目录>" --project-id "<32位项目ID>"
python -B -m deploy.bili_activity.life_observation --data-dir "<隔离测试数据目录>" --project-id "<32位项目ID>" --format json
```

默认输出 Markdown，可将终端输出保存到自己指定的报告文件；`--format json` 适合比较多日记录。工具本身只向标准输出写报告，不覆盖配置、账本或历史报告。应运行于能访问该目录的同一环境：WSL 可使用 `/mnt/d/...`；不要把 Windows 路径直接当作 Linux 路径。

退出码0仅表示成功读取这个项目，**不表示真实验收通过**。缺配置、旧库、缺表、外会话/不存在的项目、损坏数据库等返回退出码2，并给出 `state`。不显示原始异常、账号、配置、Cookie、作品/记忆正文、哈希、URL或接口响应。

## 报告能回答什么

| 内容 | 证据范围 |
| --- | --- |
| 项目与阶段 | 冻结的阶段上限、目标状态、目标更新时间、当前作品ID和版本、回看作品ID和版本及保存时间 |
| 活动经过 | 从项目阶段的经历与生活选择关联到运行ID，列出开始/结束、账本状态与已登记的模型额度；不将 `running` 解释为当前在线 |
| 首轮探索 | 选择探索时尚未创建目标，使用随后同一轮的目标经历建立关联；不会丢掉这轮的选择类型 |
| 跨日线索 | 上海时间下的运行日期；未来时间和倒置的起止时间标为待核对，不计入日期列表。日期列表本身不能证明时钟未修改或实际观察了几天 |
| 作品记忆队列 | 只列项目目标的 `work_checked` 经历对应的队列状态；`saved` 是工具确认，`sending/uncertain/failed` 原样保留，不重试；不混入其他会话或兴趣巩固队列 |
| 数据覆盖 | 最多6阶段、最近500条本会话选择、50轮关联运行，每阶段最多50个经历运行关联和24条作品记忆；截断、缺表、不能解析的选择分别标明 |

只读连接使用 `mode=ro`、`query_only` 和一个读取事务，不导入可恢复状态/迁移的 `Store`，不调用调度、模型、网络、SSH、ADB、QQ 或 LivingMemory。使用当前配置中匹配的主人私聊范围，不能通过命令指定其他会话。

报告列出历史版本，不重新运行作品来源与结构检查，也不比较正文正确性。记录发生变更后仍须用原项目/作品工具核对，不能把表格中的“相同版本号”当作来源依然有效。`model_reservations` 是原额度账本记录，不是服务商成功计费或完整模型响应的证明。

`live_verified` 固定为 `false`；自然主动性、LivingMemory实际召回、QQ实收、真实经过的天数固定为 `UNVERIFIED`。不增加“确认通过”开关，不改 `one_shot_verified`，不以可编辑文件替代原一次性活动验收。

## 本轮验证

新增 `tests/test_bili_life_observation.py` 16项：缺目录不初始化、配置/ID校验、旧库/未来表版本、损坏数据脱敏、会话隔离、首次探索关联、读取不恢复运行/重试队列、缺表提示、范围截断、异常时间、阶段预留/缺失目标、数值字段不输出任意文本、两种CLI格式与退出码。

原跨日联查增加报告断言：同一临时账本输出三日期、四轮正确的选择和完成记录、七次额度记录、两条记忆确认；报告读取前后账本一致。示例报告从这条实际执行过的离线链生成。

```powershell
$env:Path='C:\Program Files\Git\bin;' + $env:Path
$testPython='C:\Users\ACER\AppData\Local\Temp\dafeiyu-rhythm-venv\Scripts\python.exe'
& $testPython -B -m unittest discover -s tests -p 'test_bili_life_*.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_bili_*.py' -v
wsl.exe --distribution Ubuntu-22.04 --cd /mnt/d/GitHub/dafeiyu-astrbot -- python3 -B -m unittest discover -s tests -p 'test_bili_life_observation.py' -v
python -B -m deploy.bili_activity.life_observation --help
git diff --check
```

| 检查 | 本轮实际结果 |
| --- | --- |
| Windows生活定向（补首次探索回归之前） | 71项，5.140秒，OK |
| 最终Windows B站模块全量 | 574项，28.919秒，OK（11 skip） |
| 最终WSL观察报告定向 | 16项，2.174秒，OK（0 skip） |
| 三日期模拟报告生成 | 2阶段、4轮，选择为探索→回看→研究→回看；`live_verified=false` |
| 模块命令入口与帮助 | Windows成功，无状态读取或修改 |

第一次定向的测试夹具漏传 `evidence`，后续只读连接监测包装又与 SQLite 的 `uri` 关键字重名；均已修正测试夹具。首份示例发现首次探索无法通过创建前为空的目标ID关联，已修复为同时使用该轮后续经历关联，并补回归断言。上表为对应最终通过结果。

11个全量跳过项与原基线一致，原始输出位于本机临时目录 `dafeiyu-observation-targeted.log`、`dafeiyu-observation-regression.log`、`dafeiyu-observation-wsl.log`。WSL仅验收纯本地报告模块；完整生活控制器仍缺依赖，未把这16项写成Linux全量通过。页面和动画未改，未重跑浏览器。

## 下一步现场观察

仍按[现场清单](LIFE_JOURNEY_V01.md)在独立测试实例低额度运行。每天对同一项目保存一份本报告，另记录真实开始/结束日期、原对话和远端召回/QQ证据；不要通过改账本日期完成跨日验收。

本工具不替你创建或部署隔离 AstrBot 实例。本轮尚未指定现场数据目录与项目，也未启动真实试运行；报告工具就绪后应进入该步骤，再根据观察决定新增哪种生活活动。

本轮只新增报告模块、测试、模拟示例及文档，扩充既有联查断言。保留前序未提交工作；未改运行控制器、审批、传输、预算、人格、手机启动脚本或真实配置，没有 push 或部署。
