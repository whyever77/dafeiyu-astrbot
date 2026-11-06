# deploy-kit · 本地一键准备与体检包

> 想 7×24 云端 Docker 部署请看 [`deploy/README.md`](../deploy/README.md)；
> 想在自己电脑上从零把大肥鱼跑起来，用本目录的三个脚本把「体检 → 装依赖 → 干净组装 → 离线自检」这段重活包掉。

| 文件 | 干什么 |
|---|---|
| [`preflight.py`](preflight.py) | 只读体检 + 可选组装：Python 版本、git/ffmpeg、端口状态（区分安装前/后）、插件依赖、源码语法与表情表完整性、`.env` 密钥格式。 |
| [`bootstrap.sh`](bootstrap.sh) | 一键（Linux / macOS / Git-Bash）：建 venv → 装依赖（含清华镜像自动兜底） → 干净组装 `payload/` → 跑 4 套离线回归测试 → 体检。 |
| [`bootstrap.ps1`](bootstrap.ps1) | 同上，Windows PowerShell 版（支持 `uv` / 标准 `venv`）。 |
| [`requirements-plugin.txt`](requirements-plugin.txt) | 核心聊天插件依赖：`dashscope`、`imageio-ffmpeg`、`aiohttp`。 |
| [`env.example`](env.example) | 密钥与账号备忘模板，复制成 `.env` 后可用 `preflight.py --env .env` 校验。 |

## 常用命令

```bash
python deploy-kit/preflight.py                        # 1. 部署前体检（端口空闲视为正常）
bash deploy-kit/bootstrap.sh                          # 2. 一键准备（默认组装 2 个核心聊天插件）
bash deploy-kit/bootstrap.sh --with-bili              #    若还要玩 B 站自主活动插件，加 --with-bili
bash deploy-kit/bootstrap.sh --offline                #    无外网时：跳过 pip，直接组装 + 跑标准库测试
powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1   # Windows PowerShell 版
python deploy-kit/preflight.py --env .env             # 3. 核对 .env 里的密钥与 QQ 号格式
python deploy-kit/preflight.py --stage post --env .env # 4. 全部启动后复查（确认 6199/6185/6099 均在监听）
```

## `payload/` 里有什么

跑完 `bootstrap`（或 `python deploy-kit/preflight.py --stage-payload payload`）会生成：

- `payload/plugins/`：已剔除 `__pycache__`、`*.pyc`、本地 `config.json` 与运行时数据库，只含干净插件源码。
  默认装入 `astrbot_plugin_face_convert` 与 `astrbot_plugin_schedule_awareness`；
  传 `--with-bili` 时额外包含 `astrbot_plugin_bili_activity`（需要 Python 3.12+、Pillow、mcp 与配套服务）。
- `payload/persona/大肥鱼-system-prompt.txt`：已自动剥掉 Markdown 标题与顶部说明，**全选复制就能直接粘进 AstrBot「人格」系统提示词**。

> ⚠️ **注意运行环境**：`bootstrap` 建的 `.venv` 用来在本地跑离线回归测试和体检；
> 真正跑插件的是 **AstrBot 自己的 Python 环境**，记得把 `deploy-kit/requirements-plugin.txt` 装进 AstrBot 所在环境（或让 AstrBot 加载插件时自动安装）。

## 它们不做的事（也做不到）

- 装 AstrBot / NapCat —— 都是图形化安装程序 + 扫码登录
- 替你保管或上传 API 密钥 —— `.env` 只留在你本机，`.gitignore` 已排除
- 直接改写你正在运行的 AstrBot 配置 —— 组装好的 `payload/` 由你拷进 `data/plugins/`

## 设计上的四个约定

1. **不 activate 虚拟环境**：直接调 venv 里的 python，避免 `activate` 在 Git-Bash / PowerShell 下行为不一致。
2. **装依赖优先兼容 `uv pip` 与国内镜像**：`uv venv` 默认不带 `pip`，脚本自动走 `uv pip`；直连 PyPI 失败时自动回退清华镜像。
3. **Windows 路径与终端兼容**：Git-Bash 的 `/c/...` 自动经 `cygpath -w` 转换；Windows 控制台自动开启 ANSI 或降级为 `[OK]/[WARN]/[FAIL]`。
4. **自检真跑仓库测试**：`bootstrap` 会执行 `test_face_rules.py`、`test_schedule_awareness.py`、`test_heal_ws.py`、`test_chat_layout.py` 四套离线测试，不花钱、不连 QQ，全绿才算通过。
