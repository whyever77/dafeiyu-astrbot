# 隔离记忆联查：验收准备

2026-11-06。主人选择“暂不填写，先完成验收准备”。本轮只增加只读配置检查器和真实联查计划：没有索要或填写密钥，没有启用模型、调用模型或嵌入接口，没有扩大白名单、写入/回读/召回记忆，也没有启动QQ、自主生活或B站写操作；未推送、未部署、未改云端。

> **验收准备完成 ≠ 真实链路通过。** 目前没有运行过完整的真实写入—回读—召回探针。

## 只读检查器

在仓库根目录运行：

```text
python -B -m deploy.bili_activity.pilot_memory_check --pilot-dir .life-pilot/v01
```

源码见 [pilot_memory_check.py](pilot_memory_check.py)。只读取所选目录中的 `MEMORY_INSTALL.json`、`livingmemory/` 源码副本（按安装记录逐个哈希）、`astrbot/data/cmd_config.json`、LivingMemory配置和活动 `control.json`。不打开数据库、不联网、不执行子进程、不导入AstrBot或插件、不写任何文件。输出只含固定状态、布尔值和计数；密钥只报告“是否存在”，不输出内容、路径或地址。

| 状态 | 含义 | 退出码 | 下一步提示 |
| --- | --- | --- | --- |
| `invalid_input` | 文件缺失、JSON无效/重复字段、超限、路径越界、源码目录为链接或重解析点、固定版本/模型ID不符 | 3 | 修复所选目录输入 |
| `blocked_config` | 静态检查有失败项，或检查期间配置被改动（`configuration_changed_during_check`） | 3 | 人工复核，不自动修改 |
| `missing_keys` | 检查通过，但任一密钥为空或只有空白 | 2 | 本机面板填写测试密钥，保持模型禁用 |
| `models_disabled` | 两处密钥都存在，三个模型开关全关 | 2 | 经授权后做模型连通性检查 |
| `models_partly_enabled` | 只启用了部分提供商/模型 | 2 | 复核部分启用 |
| `config_ready` | 静态配置全部符合预期且三个开关全开 | 0 | 验证运行中的插件，再做限定会话的虚构记忆 |

判定顺序为：输入无效 → 配置阻塞 → 缺密钥 → 全部禁用 → 部分启用 → 就绪。任何状态都固定 `live_verified=false`，`runtime` 中 container、model、embedding、engine、memory_write、readback、recall、QQ 均为 `UNVERIFIED`。**`config_ready` 只证明静态配置，不证明容器、密钥有效、模型可用或记忆引擎正常。**

静态检查项：源码与安装记录哈希一致；活动 `control.json` 与安装记录哈希一致且16个活动/公开写入开关均为 `false`；QQ平台数为0；聊天/嵌入提供商地址、类型、模型、1024维固定值一致；LivingMemory指向两个固定ID；空白名单、会话/人格隔离保持，群聊全量捕获、记忆工具、召回、重排、整合、衰减和自动清理关闭。安全开关要求严格类型：`0`、`"false"`、`1` 不能冒充布尔值。

输入防护：单个JSON不超过2 MiB，单个源文件8 MiB、源码合计64 MiB、清单1–1000项；拒绝重复JSON字段、绝对路径、`..`、反斜杠和盘符；源码目录不得为符号链接或Windows重解析点（含junction）；解析后的文件必须仍在所属目录内；符号链接环按 `invalid_input` 处理。结束前重读四个JSON，期间有变化则拒绝报告就绪。`invalid_input` 时清空已算出的部分检查结果和密钥状态，避免看起来像可信结论。CLI参数错误只提示 `--pilot-dir`，不回显误填的参数。

## 本轮实际结果

| 项目 | 实际结果 |
| --- | --- |
| 对 `.life-pilot/v01` 运行检查器（14:01，上海时间） | `missing_keys`，退出码2；8项静态检查均为true；235个源文件，0个不一致；两处密钥均不存在；三个模型开关均为false。仅代表当时的静态文件 |
| 定向测试 Windows（MSYS2 UCRT64 Python 3.14.5） | 32项 OK，5 skip：4项需创建符号链接、1项创建junction被宿主拒绝（PermissionError） |
| 定向测试 WSL Ubuntu-22.04（Python 3.10.12） | 32项 OK，1 skip（junction测试仅Windows适用） |
| Windows全量 `test_bili_*.py`（Python 3.14.7，非此前测试venv） | **UNVERIFIED**：运行206项，19个测试模块因缺少 `mcp` 依赖无法导入，1项 `test_bili_ops` 部署测试因宿主bash无法解析Windows路径失败，14 skip。此前记录使用的 `dafeiyu-rhythm-venv` 已不存在，本轮未联网安装依赖；这些错误与本轮文件无关（失败模块均不导入检查器），但不能当作全量通过 |
| 容器运行状态、面板HTTP | 本轮未检查，UNVERIFIED（上次恢复启动后的HTTP 200只是当时快照） |

Windows的junction/重解析点分支因宿主拒绝创建junction仍未经实际测试，WSL下的符号链接分支（文件、目录、配置、链接环）已实际通过。

## 已验证与未验证

| 已验证（静态/离线） | 未验证（UNVERIFIED） |
| --- | --- |
| 检查器状态、退出码、脱敏输出与输入防护（临时假配置和临时目录） | DeepSeek短回执；硅基流动1024维嵌入 |
| 隔离目录当前静态配置：缺密钥、模型全关、QQ为0、活动全关、空白名单与隔离保留 | LivingMemory原生引擎初始化 |
| 235个源文件、活动配置与安装记录哈希一致 | 记忆写入、独立回读、召回 |
| — | 跨会话、跨人格数据隔离；QQ；自主活动；跨日效果 |

## 现有脚本的适用范围

- `.life-pilot/support/verify-memory.py` **仅适用于填钥前**：它会调用docker并重写 `MEMORY_CHECK.json`，结论只能是等待/缺少提供商。填钥或启用模型后不要再拿它作为通过证据。
- `.life-pilot/support/prepare-memory.py` 是一次性安装准备，**不要重跑**：它会重新复制源码、改写 `cmd_config.json`、LivingMemory配置、编排文件和安装记录，可能覆盖主人之后在面板保存的密钥与配置。
- 本检查器可随时重复运行，不改变任何文件。

## 未来真实联查（本轮只写计划，未执行）

前提：主人自行在本机面板填写密钥（不贴到聊天、不进Git），并另行明确授权“调用测试模型”和“在唯一隔离会话写入一条虚构记忆”。每一步失败即停止，不跳步，不以后续成功掩盖前一步失败。报告只保存状态、计数、维度、记录ID的哈希，不保存密钥、原始响应或日志原文。

| 步骤 | 做法 | 明确通过条件 |
| --- | --- | --- |
| 0. 静态门 | 填钥后保持禁用运行检查器；授权启用后再运行 | 先 `models_disabled`（2），启用后 `config_ready`（0）；否则停止 |
| 1. 容器 | 仅在实例停止时用 `Start-Pilot.ps1` 启动；不无故重启 | 仅为前置条件，HTTP 200不计入记忆验收 |
| 2. 模型短回执 | 对 `pilot_deepseek/deepseek-flash` 发一条固定短提示 | 无错误且回执非空；只记状态与字符数 |
| 3. 嵌入 | 对 `pilot_bge_m3` 嵌入一条固定文本 | 向量长度恰为1024，数值有限 |
| 4. 原生初始化 | 在插件页重载LivingMemory（或重启这个隔离实例） | 运行实例内引擎初始化成功；插件加载、数据库文件存在均不算 |
| 5. 唯一会话 | 生成唯一测试会话ID和测试人格；经授权仅把该ID临时加入白名单 | 测试窗口内检查器报 `blocked_config` 属预期，需记录起止时间 |
| 6. 写入 | 在运行实例内经 `MemoryMemorizeTool.call` 写一条明确虚构、带唯一标记的记忆 | 返回 `memorized=true` 与记录ID；结果未知时**不重发**，先按标记查找 |
| 7. 独立回读 | `memory_engine.get_memory_record(ID)` | 内容含标记，会话与人格与写入一致 |
| 8. 召回 | 同会话同人格经 `MemorySearchTool.call` 语义查询 | 结果包含该ID |
| 9. 隔离反例 | 同人格换会话查询；同会话换人格查询 | 两者都不返回该ID |
| 10. 收尾 | 恢复白名单为空、工具关闭；如需清理只按ID删这一条 | 检查器回到 `config_ready`；禁止清空整库 |

整体通过条件：步骤2–9全部PASS并保存证据。任一步失败或UNVERIFIED，都不能写“真实记忆接通”。

关键约束：

- LivingMemory 2.7.0-beta.2 中 `MemoryMemorizeTool.call` 与 `MemorySearchTool.call` 会经过白名单、scope和人格解析，适合做写入与召回；`memory_engine.get_memory_record` 用于独立回读。
- 面板 Page API 的 recall/test 固定 `persona_id=None`，不能用来证明人格隔离。白名单拒绝也不能替代真正的跨会话/跨人格数据隔离反例。
- 探针必须在运行实例的进程内执行；不要另开进程对同一FAISS/SQLite目录重新初始化。具体触发方式在获得授权、联查开始前确认。
- 插件加载、数据库文件存在、HTTP 200或 `config_ready` 都不是真实记忆验收通过。

相关记录：[模型与记忆安装](PILOT_MEMORY_V01.md)、[基础隔离实例](PILOT_V01.md)。
