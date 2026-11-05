# 本机隔离试运行

2026-11-03准备本地目录 `.life-pilot/v01/`；2026-11-04经主人明确授权，已安装本机容器环境、构建镜像并启动独立AstrBot。它是本次未提交工作区的试运行副本，不是发布包或云端已部署版本。QQ、自主活动及真实账号写入仍关闭。

后续已完成[测试模型与LivingMemory安装](PILOT_MEMORY_V01.md)：插件加载、配置与拒绝写入检查通过；模型条目已预填，密钥待主人填写，完整记忆引擎初始化与真实调用未验证。下文保留基础实例启动时的原始记录。

## 本机启动结果（2026-11-04）

- 使用现有 WSL Ubuntu 22.04.5，安装官方签名APT源中的Docker Engine/CLI 29.9.2、Compose 5.7.0、containerd 2.4.2、buildx 0.38.1；未安装Docker Desktop。安装器启用了WSL内Docker/containerd服务，未新增Windows开机任务。
- 基础镜像 `soulter/astrbot:v4.29.0`，拉取摘要 `sha256:d3007632822a128439561a3de02fc663a3ca8e9e5cd309de1c19108317ba26f5`。本地镜像名 `dafeiyu-life-pilot-astrbot`，只挂载此隔离目录的数据与插件副本。
- `docker compose config --quiet`、`docker compose --progress plain build astrbot`、`docker compose up -d --no-build astrbot` 均退出0；默认服务清单仅 `astrbot`。NapCat未启动。
- 插件加载日志显示 `astrbot_plugin_bili_activity (v0.5.0)`，截图上下文保护安装成功；容器 `python -m pip check` 返回 `No broken requirements found.`。
- Windows请求 `http://127.0.0.1:16185/` 返回HTTP 200和AstrBot HTML；浏览器实际显示登录页，页脚为 `WebUI v4.29.0 AstrBot 4.29.0`。尚未登录面板、配置模型或账号。
- 隔离配置开关保持关闭，主人范围为空、`one_shot_verified=false`；隔离账本的runs/proposals/deliveries/memories/goals均为0。
- 00:31（上海时间）复核：容器自最近启动已持续运行285秒；Windows面板仍HTTP 200，模型提供商与平台条目均为0。插件副本与当前工作区逐字节一致，原清单除本轮更新的README外均保持一致，包括初始关闭配置。这是启动稳定性抽查，不是长期生活验收。
- 首次启动曾随WSL空闲退出而中断。已增加隐藏WSL保活进程及本机 `Start-Pilot.ps1` / `Stop-Pilot.ps1`；重复启动复用进程，停止只关闭此Compose项目和自己的保活进程，数据保留。停止后再启动验证通过。不得用 `wsl --shutdown` 代替停止脚本，以免中断其他任务。WSL行为说明见[微软文档](https://devblogs.microsoft.com/commandline/systemd-support-is-now-available-in-wsl/)。

面板仅绑定回环地址。首次登录凭据保存在被忽略的 `.life-pilot/v01/LOCAL_ACCESS.txt`，不写进报告；由主人登录并按面板提示修改。当前运行证据保存在该目录 `RUNTIME_CHECK.json`，与准备时的 `MANIFEST.json` 分开。后者仍为准备时快照，包含旧README哈希和 `runtime_started=false`，不是当前运行状态；新建运行数据和启停脚本不属于原28载荷。

## 已准备的内容

- `compose.yaml`：独立项目名 `dafeiyu-life-pilot`，不使用生产容器名。AstrBot面板仅绑定 `127.0.0.1:16185`，数据只挂载此目录内的 `astrbot/data`；插件副本只读挂载。
- `Dockerfile`：沿用仓库已有的 `soulter/astrbot:v4.29.0`，构建时安装此插件原有依赖。
- NapCat服务仅在显式启用 `qq-manual` profile 时启动，使用独立目录与主机名；面板预留 `127.0.0.1:16099`。没有生产登录状态或自动登录账号。
- `plugin/`：当前插件Python文件、元数据和依赖清单，逐文件与当前工作区核对一致。
- `astrbot/data/plugin_data/astrbot_plugin_bili_activity/control.json`：从既有运维工具默认值生成的新模板；总开关、周期、生活模块和公开写入全部关闭，`once_at=null`、`one_shot_verified=false`，不伪造验收证据。
- 账号范围和DND未填写，读取后端均为 `http://127.0.0.1:1/unconfigured` 占位地址。没有自动沿用指向生产服务的默认地址。
- 预设试运行上限：项目1个、每项目2阶段、24小时后回看；单轮3分钟/6次模型，每日10分钟/12次模型。公开写入额度、分享额度和联系上限为0。上限不等于已经开启功能。
- `tools/bili_activity/`：原运维工具及只读生活观察报告；`reference/`：打包时的验收文档快照；`README.md`：后续启动、现场配置、观察与停止步骤。
- `MANIFEST.json`：记录源HEAD、工作区副本标记及28个载荷文件的SHA-256，总计29个文件。

没有复制Cookie、生产配置、SQLite数据库、QQ登录文件、模型密钥、人格或SSH凭据。LivingMemory插件、模型提供商、嵌入服务、测试主人会话及人格仍需在独立实例中配置；不能把只复制活动插件说成完整真实生活环境已就绪。

`.life-pilot/` 已加入仓库忽略项，避免后续测试凭据和运行数据进入提交。目录为本机准备产物，不随仓库提交分发；如换机器，须重新准备或手动传递不含运行数据的副本。

## 准备时核对结果（2026-11-03）

| 检查 | 结果 |
| --- | --- |
| 29文件清单与28个载荷哈希 | PASS |
| 插件Python源码与工作区逐字节一致、Python语法 | PASS |
| Compose文件可解析、路径仅引用本目录、端口仅绑定回环地址 | PASS（静态检查） |
| QQ profile、禁止自动重启、所有活动关闭、空身份/占位后端、零写入/分享额度 | PASS（静态检查） |
| 不含数据库或Cookie；配置路径被Git忽略 | PASS |
| `git diff --check` | PASS，仅既有换行转换提示 |
| `docker compose config`、镜像构建、真实启动 | UNVERIFIED |
| QQ登录/回执、真实模型、LivingMemory写入与召回、跨日效果 | UNVERIFIED |

本轮没有改动控制器或运行逻辑，因此没有重复执行上一轮574项离线回归。现有 Windows PATH 和 WSL Ubuntu 22.04 中未找到Docker/Podman命令；已检查的 `D:\GitHub\AstrBot`、`D:\AI\AstrBot` 也不存在。这不是对所有磁盘路径的全盘搜索。

## 授权与后续边界

1. 主人已选择“本机新建独立测试实例”，并明确批准“允许，仅启动本机隔离AstrBot”。本次安装与启动在这项授权内完成。
2. 不部署云端、不操作生产实例、不推送的限制继续保留。本机隔离实例启动不代表真实账号、消息发送或自主活动已获准开启。
3. 首次只启动AstrBot；QQ服务、扫码、真实消息及自主活动分别按现场验收授权处理。扫码仍由主人完成，不导入生产QQ会话。
4. 原一次性活动验收要求真实内容核对、图片打开及QQ回执。初始零分享额度不能被解释为已经完成这项验收，不能复制别的实例的 `one_shot_verified` 来跳过。

本机启停使用本地目录README中的脚本；真实生活观察继续按 [LIFE_JOURNEY_V01.md](LIFE_JOURNEY_V01.md) 和 [LIFE_OBSERVATION_V01.md](LIFE_OBSERVATION_V01.md) 执行。不得修改系统或账本日期制造跨日通过结果。安装、启动检查没有修改插件业务代码；没有重复宣称上一轮574项为本轮测试结果。
