# LivingMemory 只读接入 · v0.2 验收

日期：2026-10-29。分支 `codex/memory-livingmemory-v02`，基于 `fd22011`。

## 结论

**PASS：真实只读连接、手动同步、来源展示与本地隔离。**
实际读取线上安装的 LivingMemory `2.7.0-beta.2`，本地页面已完成真实来源浏览器联调。
这不代表新的遗忘策略、真实向量召回或 QQ 行为已经上线；这些功能仍未接入。

首次成功同步时的统计（不是固定配置，也不是模型生成的演示数据）：

| 项目 | 实测 |
|---|---:|
| 原库 documents | 128 |
| 原库 memory_atoms | 147 |
| 载入活跃文档 | 128 |
| 载入有效原子 | 109 |
| 排除非活跃/到期原子 | 38 |
| 会话与人格组合的作用域 | 2 |
| 当前作用域展示条数 | 232（两个作用域总计 237） |
| 缺失归属、父记录/归属不符、无效、截断、超量 | 均为 0 |
| 原库 graph_edges 总数 | 1208（不是画布连线数） |

没有在提交中保存私人记忆正文、源消息、账号标识、真实主机地址或真实库文件。
浏览器结果只保存检查名称，见 [assets/livingmemory-browser-results.json](assets/livingmemory-browser-results.json)。

## 为什么使用固定只读读取器

审计了实际安装版本的 `metadata.yaml`、`core/page_api.py`、`core/page_api_modules/`、
`core/managers/memory_engine_crud.py` 及数据库列结构。

该版本的页面导出路径会读取源聊天消息；测试召回路径会进入真实 `search_memories`，
可能更新访问与强化信息。因此观测台不调用这些接口。通过现有 SSH 连接，以标准输入执行固定读取器，
只对 livingmemory.db 执行明确列的 SELECT；不读 conversations.db，不加载插件或向量引擎。
没有部署文件、修改云端配置、重启 AstrBot 或创建云端服务。

## 证据矩阵

| 项目 | 实现与测试证据 | 结果 |
|---|---|---|
| 实际版本契约 | `livingmemory_reader.read_snapshot` 严格匹配 2.7.0-beta.2；`test_unknown_version_schema_and_missing_file_fail_closed` 拒绝新版本、缺表和缺文件 | PASS |
| 防写与读一致性 | `mode=ro` + `query_only` + SQL authorizer + 事务；`test_sqlite_readonly_mode_and_authorizer_reject_mutations` 拒绝 UPDATE/ATTACH/可写 schema；`test_repeated_reads_do_not_mutate_db_or_access_counts` 校验合成库哈希和计数不变 | PASS |
| WAL | `test_live_wal_records_are_visible_without_immutable_shortcut` 从仍打开的 WAL 写连接读取已提交内容；没有使用 immutable 跳过 WAL | PASS |
| 生命周期过滤 | `test_active_documents_and_valid_atoms_only` 覆盖非活跃文档、expired/forgotten/已到期/未来时间原子和父记录排除 | PASS |
| 会话和人格隔离 | `test_session_and_persona_are_both_isolation_keys`、`test_missing_persona_is_quarantined_but_explicit_null_stays_separate`；先按归属筛选再检索和关联 | PASS |
| 来源含义 | 计划/未知/文档独立类型测试；`test_native_ttl_is_evidence_not_simulation_half_life`；原图关系与父文档归属测试 | PASS |
| 元数据最小化 | `test_metadata_allowlist_drops_secrets_and_transcripts`；不输出任意 metadata；错误脱敏、命令注入拒绝测试 | PASS |
| 不因观测强化 | `test_reads_and_projections_never_refresh_or_write_upstream` 验证普通页面 API 不调用来源；原有投影/搜索不改变可信度和记忆测试继续通过 | PASS |
| 同步生命周期 | single-flight、失败保留旧快照并标记 stale、恢复同步、启动失败不切演示、真实模式拒绝导入/重置 | PASS |
| 真实连接 | 固定 SSH 读取成功；真实页面点击手动同步，时间戳更新且 source state=ready | PASS |
| 界面 | 演示 10 项 + 真实只读 6 项 Chrome 检查；1600×1150、390×844 无横向溢出；无未捕获 JS 异常 | PASS |

真实引擎可能同时写库，所以没有将线上库的前后哈希/计数差异用作「完全没变化」的证据。
防写保证来自读取器的 SQL/打开模式审查及合成数据库的独立测试。

## 实际测试命令与输出

Windows：

```text
python -B -m unittest discover -s tests -p 'test_memory_*.py' -v
Ran 48 tests in 3.947s
OK
```

WSL：

```text
wsl -e bash -lc 'cd /mnt/d/GitHub/dafeiyu-astrbot && python3 -B -m unittest discover -s tests -p "test_memory_*.py" -v'
Ran 48 tests in 3.780s
OK
```

两者均 0 skip，全部使用合成数据和临时库。原有记忆观测台 27 项保留，新增 21 项。

```text
node --check deploy/memory_observatory/static/app.js
node --check deploy/memory_observatory/static/graph.js
node --check tests/memory_observatory_browser.mjs
```

均 exit 0。演示服务和真实只读服务分开启动：

```powershell
python -B -m deploy.memory_observatory.server --port 8768
$env:MEMORY_TEST_URL = 'http://127.0.0.1:8768'
node tests/memory_observatory_browser.mjs
# 10 checks, 0 failures, 0 errors

python -B -m deploy.memory_observatory.server --port 8767 --livingmemory-ssh
$env:MEMORY_TEST_URL = 'http://127.0.0.1:8767'
node tests/memory_observatory_browser.mjs --live-readonly
# 6 checks, 0 failures, 0 errors
```

Chrome 使用独立临时 profile。真实模式不截图、不导出正文、不调用导入/重置接口；
普通演示测试会替换内存快照，因此必须对单独的演示服务运行。

开发中先后发现并修复：Windows SQLite URI 缺少 `uri=True`；测试连接未关闭造成临时文件清理失败；
新增 UPDATE 拒绝探针自身开启隐式事务，需要回滚探针后再运行真实读取器。
沙箱内的临时目录/回环端口限制通过获准的沙箱外测试调用处理。最终结果为上方完整重跑输出。
收尾浏览器复查曾因旧预览进程已经退出而在页面加载阶段超时；确认两个端口均不可用后，
重启本机预览，再次完整取得真实只读 6 项和演示 10 项 PASS，未修改断言或放宽等待条件。

本轮未复跑 B站全量，不能引用历史结果当作本轮结果；B站代码、测试和配置均无改动。

## 使用与未接入部分

打开 <http://127.0.0.1:8767>，确认「LivingMemory · 真实只读」及快照时间；选取作用域，
点击文档或原子查看来源与原库观测值。模拟时间/巩固/保留只影响视图，点击「同步真实记忆」重新读取。
若显示「旧快照」，当前页面不能证明上游最新状态；恢复 SSH 后手动重试即可。

- 支持版本仅 2.7.0-beta.2；升级引擎后需重新核对表结构。其他版本 **UNVERIFIED**。
- 只展示活跃记忆，不提供过期/遗忘记录管理；最多 500 条、文档优先，大规模性能 **UNVERIFIED**。
- 每篇文档最多展示 5 条原实体关系；没有把 1208 条原图谱关系全部画出。
- 真实召回事件流、向量召回、新遗忘策略、写回/删除和 QQ 行为变化 **未实现**。
- Safari/Firefox、真实手机、持续运行稳定性和自动启动 **UNVERIFIED**。
- 本地只读服务供本机主人使用，作用域选择不是多用户认证。无需修改云端；停止本地服务即停止观测。

本轮只改记忆观测台、对应测试、文档和忽略目录；不改原有审批、执行器、传输、B站模块、人格或部署配置。
没有 push、PR、发布、部署或云端写入。
