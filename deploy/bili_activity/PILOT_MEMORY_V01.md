# 隔离实例的模型与记忆安装

2026-11-04。主人选择“沿用DeepSeek + 硅基流动bge-m3，在本机面板填写密钥”。本轮只推进 `.life-pilot/v01/`；不使用生产凭据、记忆、账号或云端配置。

## 实际完成

- 从LivingMemory官方仓库下载固定标签 `2.7.0-beta.2`，提交 `28698d31250073280ff23cf4d00a1ea17041b48c`；235个文件复制到隔离目录 `livingmemory/` 并记录哈希。只读挂载到AstrBot插件目录，没有改上游源码。此版本号与原检查副本一致；原检查副本没有完整提交证据，不能宣称它们逐文件一致。
- 在原AstrBot 4.29.0镜像内按上游requirements安装依赖；新增networkx 3.7和pytz 2026.6，其余依赖已有。成功构建并启动，镜像摘要 `sha256:8c7f5b65fda9052253367b379f60085e20eb4aafc20a1026bd38e7d578855e27`。
- 聊天提供商 `pilot_deepseek`、模型 `pilot_deepseek/deepseek-flash`；嵌入模型 `pilot_bge_m3` 对应 `BAAI/bge-m3` / 1024维。地址分别为 `https://api.deepseek.com` 和 `https://api.siliconflow.cn/v1`，密钥留空、模型禁用。聊天预设非思考模式、输出上限2048；尚未发送请求验证这些参数。
- LivingMemory明确关联两个ID，启用空白名单、会话与人格隔离；群聊全量捕获、自动召回、主动记忆工具、整合与自动清理关闭。原活动control.json哈希仍为 `a245d8ed2f441ae4e6fe4fb98c1e1131d7d3cf677dfa8def2caa1e94940bf922`。
- 安装前配置与编排备份保存在被Git忽略的 `backups/before-livingmemory-20261104/`，旧镜像保留 `before-memory-20261104` 标签。未删除运行数据。

## 验证证据

命令在 `D:\GitHub\dafeiyu-astrbot` 运行，WSL命令均指定 `Ubuntu-22.04`、root与隔离项目目录。

| 检查 | 命令/证据 | 结果 |
| --- | --- | --- |
| 编排 | `docker compose config --quiet` | 退出0 |
| 构建 | `docker compose --progress plain build astrbot` | 退出0，镜像构建成功 |
| 启动 | `.life-pilot/v01/Start-Pilot.ps1` | 退出0，仅AstrBot容器运行 |
| 依赖 | 容器内 `python -m pip check` | `No broken requirements found.` |
| 实际加载 | 当前容器启动日志 `Plugin astrbot_plugin_livingmemory (2.7.0-beta.2)`；原活动插件亦加载 | PASS |
| 安装检查 | `python -B .life-pilot/support/verify-memory.py` | 退出0；见本机 `MEMORY_CHECK.json` |
| 配置验证 | 调用安装版本的 `normalize_config` / `validate_config` / `ConfigManager` | PASS，无修正，不降级为默认配置 |
| 空白名单 | 实际 `is_event_memory_allowed` 拒绝；实际 `MemoryMemorizeTool.call` 返回 `memorized=false, error=memory access is not allowed` | PASS，在处理器调用前拒绝，未写记忆 |
| 会话隔离 | 实际 `resolve_memory_scope` 返回输入测试会话；人格过滤保持true | PASS |
| 源码及关闭状态 | 235个源文件哈希一致；活动配置原哈希不变；平台数0，活动账本五类记录均0 | PASS |
| Windows面板 | 本机HTTP 200，AstrBot HTML | PASS；本轮未登录面板，不宣称新增模型条目已做浏览器操作验收 |
| 真实模型、嵌入、完整引擎初始化、记忆写入/回读/召回 | 无密钥；未执行 | UNVERIFIED |

插件加载与引擎就绪分开报告。缺少启用的模型时，原生初始化器等待60次后记录初始化失败；当前状态为 `BLOCKED_MISSING_PROVIDERS_RELOAD_AFTER_KEYS`。没有创建 `livingmemory.db`，不能称为真实记忆已接通。填好并启用两个模型后，需要重载LivingMemory。

本轮没有修改业务Python代码，未重复全量离线回归；以上是本轮实际安装与配置检查。QQ、自主生活、真实B站写入以及跨日行为未验证。

## 下一步

本机 `.life-pilot/v01/MODEL_MEMORY_SETUP.md` 已给出两处密钥填写位置。主人只填写并保存密钥，暂时保持模型禁用；之后再验证连通性、初始化及指定测试会话的一条测试记忆。不要把密钥写进聊天或提交到Git。

2026-11-06主人选择“暂不填写，先完成验收准备”。已增加只读检查器 `python -B -m deploy.bili_activity.pilot_memory_check --pilot-dir .life-pilot/v01`：状态 `invalid_input` / `blocked_config`（退出3）、`missing_keys` / `models_disabled` / `models_partly_enabled`（退出2）、`config_ready`（退出0），始终 `live_verified=false`，运行时八项均UNVERIFIED。当次对本目录的实际结果为 `missing_keys`（退出2），8项静态检查通过、235个源文件0不一致。上表的 `verify-memory.py` 仅适用于填钥前；`prepare-memory.py` 是一次性准备，不要重跑。用法、已验证/未验证边界和未来真实联查的步骤与通过条件见[验收准备](PILOT_MEMORY_ACCEPTANCE_V01.md)；真实写入—回读—召回仍未执行。

原[基础实例记录](PILOT_V01.md)及其 `MANIFEST.json` / `RUNTIME_CHECK.json` 是前一阶段快照。本轮记录为 `MEMORY_INSTALL.json` / `MEMORY_CHECK.json`；它们与启停脚本、凭据、运行目录均被Git忽略。

上游依据：[固定版本](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.2)、[DeepSeek接口](https://api-docs.deepseek.com/quick_start/agent_integrations/)、[硅基流动嵌入接口](https://siliconflow.readme.io/reference/createembedding)、[LivingMemory嵌入配置](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/guide/embedding-model)。
