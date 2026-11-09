# 🍎 macOS 本机部署套件（Docker）

> 在 Mac 上用两个容器跑起大肥鱼：AstrBot 是大脑，NapCat 是 QQ 协议端。
> 本目录按 issue #32 的**方案 A** 独立新增，**完全不动** `deploy/` 里的云上套件，
> 所有宿主路径都是相对路径，时区 / 端口 / QQ 小号走 `.env`。

## 为什么 macOS 上 NapCat 只能进容器

- macOS 26+ / arm64 上，NapCat 原生 Shell 的 native hook 会被系统按代码签名强杀
  （`SIGKILL` / `Code Signature Invalid`，出错页在 `wrapper.node` 的可执行映射上）。
- NapCat 维护者对同类 issue（#2066）的回复是「macos 缺少设备信息，支持的版本不是很全，**建议用 docker**」。
- v4.18.36+ 虽然发了 macOS arm64/x64 原生模块，但官方口径只是「**无登录**运行测试通过」，不等于扫码登录可用。

所以本套件的前提是：**NapCat 跑在容器里**，不追求 macOS 原生 NapCat（那条路受腾讯代码签名限制，不可控）。
`soulter/astrbot` 与 `mlikiowa/napcat-docker` 两个镜像都提供 `linux/arm64`，Apple Silicon 上原生跑。

## 覆盖范围

| 能力 | macOS | 说明 |
|---|---|---|
| 聊天 / 看图 / 语音 / 记忆 / 情感 / B站自主活动 | ✅ | 与 Windows 同一套插件与补丁 |
| NapCat 扫码登录、收发消息 | ✅ | 容器内运行 |
| PhoneMCP（手机控制） | ❌ | 依赖雷电模拟器 + ADB，是 Windows 平台能力 |
| 现场听（loopback 录音） | ❌ | Windows 声卡 loopback，不是移植问题 |
| 合盖不睡（`powercfg`） | ❌ | Windows 专属；macOS 用 `caffeinate` 另说 |
| launchd 开机自启 | ⏳ | P1，暂用「登录项」+ `./start.sh` |

## 前置

- macOS 12+（本套件在 macOS 27 / arm64 上编写；Intel Mac 走 x86_64 镜像，理论可用但未实测）
- Docker Desktop、OrbStack 或 colima 任选其一；给 Docker **4GB 以上内存**
- 磁盘 8GB+（镜像约 2GB + 数据）
- 一个 **QQ 小号**（有风控概率，别用主号）

## 快速开始

```bash
cd deploy-macos
./start.sh
```

`start.sh` 会依次：检查 docker → 建 `data/` `napcat/` → 首次从 `.env.example` 生成 `.env`
→ 构建镜像 → 起容器 → 等面板就绪，最后打印两个地址。

首次启动还要拿两个凭据：**NapCat WebUI 的 token**（见 §1）和 **AstrBot 面板的初始密码**（见 §2）。
两个都是随机生成的，不再有默认密码。

### 1. 登录 QQ

NapCat v4.18.x 起，WebUI token 是随机生成的，**不再打印在启动日志里**，而是落在
`napcat/config/webui.json`（bind-mount 在宿主机上），登录成功后还会私聊发到小号。首次扫码前先读出来：

```bash
cat napcat/config/webui.json     # 看 "token": "..." 字段
```

不想每次翻文件：首次启动前在 `.env` 里设 `WEBUI_TOKEN=你的token`，镜像入口会用它生成 `webui.json`
（只在文件不存在时写入；之后要改就编辑 `webui.json` 再 `docker compose restart napcat`）。

打开 **http://127.0.0.1:6099/webui**，用 token 登录后扫码。

`QQ_ACCOUNT` 只省后续重启：填了小号后 `./start.sh --no-build` 会尝试快速登录，
但首次部署没有登录态，仍然要扫码；扫码成功过一次之后，重启才免扫码。
指向 AstrBot 的反向 WS（`ws://astrbot:6199/ws`）已由 `MODE=astrbot` 自动写好，不用手动加。

### 2. 配提供商

打开 **http://127.0.0.1:6185**（AstrBot 面板）。AstrBot v4.24.4+ 首次启动会生成随机初始密码，
只打印在启动日志里：

```bash
docker compose logs astrbot | grep -i "Initial password"
```

登录后立刻在面板里改密码。忘了密码可以交互式重置：

```bash
docker compose exec astrbot astrbot password     # 交互式重置（v4.24.5+）
```

| 提供商 | 用途 | 关键点 |
|---|---|---|
| DeepSeek (`deepseek-chat`) | 对话主力 | `https://api.deepseek.com/v1` |
| 百炼视觉 (`qwen3-vl-plus`) | 图片/视频画面转述 | 填到「图片转述提供商」 |
| 百炼语音 (`qwen-audio-3.1-tts-flash`) | 语音条 | 先做音色复刻拿 voice_id |
| Ollama 嵌入 (`bge-m3`) | 记忆检索 | 见下节「接宿主 Ollama」 |

平台适配器选 **QQ(aiocqhttp)**，端口 6199。

### 3. 装插件与人格

```bash
# 仓库根目录
bash deploy-kit/bootstrap.sh            # 装依赖 + 干净组装出 payload/
mkdir -p deploy-macos/data/plugins
cp -R payload/plugins/* deploy-macos/data/plugins/
```

人格：把 `persona/大肥鱼-人格.md` 里 `---` 之后的整段正文粘进面板新建的人格，设为默认。
细节见 [导入指南](../docs/导入指南.md)。

## 接宿主 Ollama（记忆嵌入 bge-m3）

```bash
ollama pull bge-m3
```

面板 → 服务提供商 → 新增 **Ollama 嵌入**，API Base 填 `http://host.docker.internal:11434`。

- **Docker Desktop / OrbStack**：`host.docker.internal` 开箱即用，不用改任何配置。
- **colima / lima**：取消 `compose.yaml` 里 `extra_hosts` 两行注释，并让 Ollama 监听所有网卡
  （`launchctl setenv OLLAMA_HOST 0.0.0.0` 后重启 Ollama）。

验证：

```bash
docker compose exec astrbot python -c "import urllib.request as u; print(u.urlopen('http://host.docker.internal:11434/api/tags', timeout=5).status)"
```

返回 `200` 就通了。

## 目录与数据

```
deploy-macos/
├── compose.yaml     # 相对路径编排；镜像构建与补丁复用 ../deploy/
├── .env.example     # 端口 / 时区 / QQ 小号 / WebUI token（首次运行复制成 .env）
├── start.sh         # 环境检查 → 起容器 → 等面板
├── stop.sh          # 停 / 删容器 / 清数据
├── env.sh           # .env 读取（去行尾注释等），start.sh source 它
├── data/            # AstrBot 数据（记忆、插件、密钥；首次启动生成，git 忽略）
└── napcat/          # NapCat 配置与 QQ 登录态（git 忽略）
```

两个端口都只绑 `127.0.0.1`，局域网和公网都访问不到，不需要额外防火墙。

## 语音与分泡补丁

`compose.yaml` 默认把 `../deploy/patches/` 的四个核心补丁 bind-mount 进容器
（TTS WebSocket、整段语音、气泡节奏、布局工具），升级镜像不会丢。

- 补丁是 **AstrBot v4.29.0** 源码的修改版；升级 AstrBot 时要重新打补丁，
  见 [deploy/patches/README.md](../deploy/patches/README.md)。
- 只想要基础聊天（不要百炼语音 / 分泡）：注释掉 compose 里 4 行 `patches/...` 挂载即可。

## 日常运维

```bash
./stop.sh                          # 停容器，数据保留
./start.sh --no-build              # 秒起（日常重启）
./start.sh --pull                  # 重建 AstrBot：先拉 Dockerfile 的 FROM 基础镜像（不拉 NapCat）
docker compose logs -f astrbot     # 跟日志
./stop.sh --down                   # 删容器与网络，数据保留
./stop.sh --purge                  # 连数据一起删（要输 yes）
```

**备份**（SQLite 写入中直接打包可能拿到不一致快照，所以先停）：

```bash
./stop.sh
tar -czf ~/dafeiyu-macos-$(date +%F).tar.gz data napcat compose.yaml .env
./start.sh --no-build
```

**升级**：两套镜像分开升。

- AstrBot：改 `../deploy/Dockerfile` 的 `FROM` tag（当前 `v4.29.0`）→ `./start.sh --pull`。
  `--pull` 只影响这个构建基础镜像，不会拉 NapCat。
- NapCat：改本目录 `compose.yaml` 的 `image` tag（当前 `v4.18.37`）→ `docker compose pull napcat`，
  再 `./start.sh --no-build`。
- 升完都重新对照 `../deploy/patches/` 是否仍适用。

## 掉线与 WS 自愈

NapCat 的 WS 客户端断了不会自己回来，云上靠 `deploy/heal_ws.py` 自愈。
该脚本硬编码了 `/opt/dafeiyu` 与 `/proc`，**在 macOS 上暂时跑不了**（P2 再做）。

发消息没反应时，先确认 AstrBot 日志里 6199 有没有收到连接：

```bash
docker compose logs --tail 50 astrbot | grep -i 6199
```

没有的话：NapCat WebUI → 网络配置 → 把指向 AstrBot 的 `rws` 客户端**关一下再开**
（不重启 QQ、不用重新扫码）。

## 故障排查

| 现象 | 处理 |
|---|---|
| 端口被占用 | 改 `.env` 里的 `ASTRBOT_PORT` / `NAPCAT_PORT` |
| 容器反复重启 | `docker compose logs --tail 100 astrbot`；多半是升级了镜像但补丁版本不匹配 |
| `napcat` 起不来，日志里有 `/app/templates/astrbot.json` | 镜像的 `MODE` 模板对不上：删掉 compose 里 `MODE=astrbot` 一行，启动后在 WebUI 手动加反向 WS `ws://astrbot:6199/ws` |
| 面板打不开 | 首次启动要初始化 `data/`，等 2～3 分钟；再看 `docker compose ps` |
| 扫码后很快掉线 | 别频繁 `docker compose up -d` 重建容器（设备指纹会变）；优先 `docker compose restart napcat` |
| Docker Desktop 吃内存 | 设置里给 4～6GB；同时开太多容器时先 `./stop.sh` |
| 文件归属/权限奇怪 | `start.sh` 已按宿主 `id -u`/`id -g` 对齐 NapCat 的运行用户 |

## 与云上套件的关系

- 方案 A：新增独立目录，`deploy/` 现有文件一行没改（只新增了一个 `deploy/.dockerignore`，
  把 19MB 的构建上下文挡在镜像外——`deploy/Dockerfile` 只有 `FROM` + `pip install`，不 COPY 文件）。
- **同一台 Docker 主机上不要同时起两套**：两边都要 6185 / 6099 端口，会直接撞端口。
  本套件已经去掉 `container_name`，容器名不会撞（云上是 `astrbot` / `napcat`，本套件是
  `dafeiyu-macos-astrbot-1` / `dafeiyu-macos-napcat-1`）；真要同时跑，改 `.env` 里的
  `ASTRBOT_PORT` / `NAPCAT_PORT` 即可。
- **复用**：`../deploy/Dockerfile`（镜像构建）与 `../deploy/patches/`（四个核心补丁），
  不复制第二份，避免两处漂移。因此本目录需要在仓库里运行（`deploy-macos/` 与 `deploy/` 同级）。
- 镜像 tag 分开：本套件是 `dafeiyu-astrbot:macos`，云上是 `dafeiyu-astrbot:latest`，互不覆盖。

## 后续计划

- **P1**：`deploy-kit/preflight.py` 的 macOS 分支（docker/colima 检测、brew ffmpeg 提示、`lsof` 端口检查）、
  `docs/部署指南-macOS.md`、launchd plist 开机自启。
- **P2**：`deploy/memory_observatory/launcher.py` 用 `fcntl.flock` 替换 `CreateMutexW`、
  `heal_ws.py` 去绝对路径（docker exec 探测 + WebUI 配置路径可配）、`browser_use` 的 Chrome 路径自动探测。

## 验证状态

- 静态验证：`bash -n`、compose 变量与 `.env` 对齐、相对路径检查，见 `tests/test_deploy_macos.py`。
- 文档里的凭据位置已按上游源码核对：AstrBot v4.24.4+ 的初始密码打印为 `Initial password`；
  NapCat v4.18.37 起 WebUI token 不再进日志（随机生成后落盘 `webui.json`，登录后私聊发到小号）。
- **未验证**：本 PR 编写环境没有 Docker，没跑过真实 `docker compose up`；扫码登录、真实 QQ 收发
  需要维护者在 Mac 真机上过一遍（README 已按验证顺序写，方便对照）。
