# ⚡ QUICKSTART · 十步从零到聊天

> 目标：QQ 里发一句「在吗」，大肥鱼带着真表情回你。
> 全程约 30～40 分钟，其中大半时间是下载客户端与扫码。
> 详细原理见 [README.md](README.md) 与 [docs/部署指南.md](docs/部署指南.md)，手把手导人设/音色见 [docs/导入指南.md](docs/导入指南.md)，出问题查 [docs/踩坑合集.md](docs/踩坑合集.md)。
> 想 7×24 挂云服务器？直接看 [deploy/README.md](deploy/README.md)（Docker Compose 套件）。

---

## 第 0 步：拿到代码

```bash
git clone https://github.com/whyever77/dafeiyu-astrbot.git
cd dafeiyu-astrbot
```

## 第 1 步：装前体检（1 分钟）

```bash
python deploy-kit/preflight.py
```

检查 Python 版本、git / ffmpeg、关键端口、插件源码与表情表完整性。
红色 `✗`（阻塞项）先解决；黄色 `!`（比如还没装插件依赖）下一步会自动装好。

## 第 2 步：一键装依赖 + 组装 `payload/` + 离线自检（3 分钟）

```bash
# Linux / macOS / Windows Git-Bash
bash deploy-kit/bootstrap.sh
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1
```

它会：建虚拟环境 → 装 `dashscope` / `imageio-ffmpeg` / `aiohttp`（国内网络自动兜底清华镜像）→
把核心聊天插件（`face_convert` + `schedule_awareness`）和剥好纯正文的人格提示词组装到 `payload/` →
连跑 4 套离线回归测试。看到 **`结果：全部通过 OK`** 就说明代码在你这台机器上完好可用。

> 想连 B 站自主活动插件（`astrbot_plugin_bili_activity`）一起准备？加 `--with-bili`（PowerShell 用 `-WithBili`，需 Python 3.12+）。

## 第 3 步：备好密钥与账号（3 分钟）

```bash
cp deploy-kit/env.example .env          # PowerShell: Copy-Item deploy-kit\env.example .env
```

打开 `.env` 填好两把钥匙和两个 QQ 号：

| 变量 | 用途 / 去哪拿 |
|---|---|
| `DEEPSEEK_API_KEY` | 对话主力 → https://platform.deepseek.com |
| `DASHSCOPE_API_KEY` | 看图 / 语音听写 / 语音条 → https://bailian.console.aliyun.com（API-KEY 管理） |
| `QQ_ACCOUNT` | 跑机器人的 **QQ 小号**（强烈建议小号） |
| `OWNER_QQ` | 你自己的 **主人 QQ 号** |

填完跑一次核对（会自动识别没改掉的 `<sk-...>` 模板占位符和填错的 QQ 号）：

```bash
python deploy-kit/preflight.py --env .env
```

## 第 4 步：装 AstrBot（大脑，10 分钟）

1. 下载 **AstrBot Launcher**（图形化，自带隔离环境），装好启动
2. 国内网络：进「高级 → 通用」，打开 **中国大陆一键加速**
3. 打开面板 `http://localhost:6185`，设管理员账号

## 第 5 步：装 NapCat（QQ 协议端，10 分钟）

1. 下载 `NapCat.Shell.Windows.Node.zip`（自带 Node）
2. **Windows 首选自包含模式**：解压后双击**根目录 `napcat.bat`**（自带 `wrapper.node`，绕开新版 QQ 注入崩溃，见踩坑 #22；若走注入模式可用 `launcher-win10-user.bat`）
3. 打开 WebUI `http://127.0.0.1:6099` → **用 QQ 小号扫码登录**

## 第 6 步：把 NapCat 和 AstrBot 接起来（2 分钟）

NapCat WebUI（网络配置 → 新建 WebSocket 客户端）或 `config/onebot11_<QQ号>.json` 里加一条：

```json
{ "name": "astrbot", "url": "ws://127.0.0.1:6199/ws", "enable": true }
```

AstrBot 面板：平台适配器新增 **QQ(aiocqhttp)**，端口 **`6199`**，并在 `admins_id`（管理员列表）里填上你的 **`OWNER_QQ`**（这样不论群聊还是私聊，她都认得出你是主人）。

## 第 7 步：在 AstrBot 面板配提供商（3 分钟）

| 位置 | 填什么 |
|---|---|
| 对话模型 | `deepseek-chat` + DeepSeek Key |
| 图片转述 | `qwen3-vl-plus` + 百炼 Key |
| 语音（TTS，可选） | `qwen-audio-3.1-tts-flash` + 百炼 Key（自定义音色与两个源码补丁见 [docs/导入指南.md](docs/导入指南.md) 与踩坑 #15） |

> 💡 `face_convert` 的语音识别和视频理解优先读插件目录 `config.json` 里的 `"asr_api_key"` 或系统环境变量 `DASHSCOPE_API_KEY`，与 TTS 提供商解耦。

## 第 8 步：拷入插件 + 粘贴人格（3 分钟）

```bash
# 1. 把组装好的干净插件拷进 AstrBot 数据目录
cp -r payload/plugins/* <AstrBot数据目录>/data/plugins/

# 2. 在 AstrBot 所在的 Python 环境里装上插件依赖
<AstrBot的python> -m pip install -r deploy-kit/requirements-plugin.txt
```

- 数据目录位置：Launcher 版在 `…/instances/<实例id>/core/data/`；手动/Docker 版在 `data/`。
- **导入人格**：面板 → 配置 → 人格 → 新建，名称 `大肥鱼`，打开自动生成的 **`payload/persona/大肥鱼-system-prompt.txt`**，**全选复制粘贴**进去，设为默认人格，重启 AstrBot。

## 第 9 步：开访问白名单（1 分钟，别跳过）

面板 → 访问控制 → 白名单模式，先只放行你自己的会话。私聊机器人发 `/sid` 可拿到会话 ID（形如 `qq:FriendMessage:<你的QQ>`）。

## 第 10 步：联调复查与聊天验收（2 分钟）

先跑一次装后复查，确认 6199 / 6185 / 6099 三个端口都处于监听状态：

```bash
python deploy-kit/preflight.py --stage post --env .env
```

然后用你的主人 QQ 私聊小号：

| 发什么 | 应该发生什么 |
|---|---|
| 「在吗」 | 有文字回复、认出你是主人（日志有 `[face_convert] 身份标记: 主人(...)`）、带真 QQ 表情 |
| 一张图片 | 她能看懂画面并自然接话 |
| 一个纯表情 | 她有反应，不是装死（日志有 `收信翻译: [...]`） |
| `/语音状态` | 返回当前语音开关与冷却状态 |

四条都过 = 装完了 🎉 想要语音条、视频理解、长期记忆、主动消息，接着看 [README.md](README.md) 与 [docs/导入指南.md](docs/导入指南.md)。

---

## 卡住了怎么办

| 现象 | 去看 |
|---|---|
| 发消息没反应 | [踩坑合集](docs/踩坑合集.md) #5（NapCat WS 断线自愈） |
| NapCat 启动就崩 | #22（双击根目录 `napcat.bat` 走自包含模式，别走旧注入） |
| 发纯表情她装死 | #1 |
| 私聊不认主人 | 确认 `cmd_config.json` 的 `admins_id` 填了你的 QQ 号，且 `face_convert >= v1.3.3` |
| 语音合成失败 | #15（`qwen-audio` 两个源码补丁） |
| 语音里念出「旺柴」 | #14 |
| 插件市场装不上 | #17（GitHub 被卡，走代理） |
| 升级后功能全没了 | #20 |

**一句话原则**：所有「她好像不太对」，最后都能在日志里找到一句明确的错。先看日志，再怀疑模型。
