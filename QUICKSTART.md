# ⚡ QUICKSTART · 十条命令从零到聊天

> 目标：QQ 里发一句「在吗」，有人回你。
> 全程约 40 分钟，其中 25 分钟是下载和扫码。
> 详细原理见 [README.md](README.md) 与 [docs/部署指南.md](docs/部署指南.md)，出问题看 [docs/踩坑合集.md](docs/踩坑合集.md)。

---

## 第 0 步：拿到代码

```bash
git clone https://github.com/whyever77/dafeiyu-astrbot.git
cd dafeiyu-astrbot
```

## 第 1 步：体检（1 分钟）

```bash
python deploy-kit/preflight.py
```

检查 Python 版本、git / ffmpeg、关键端口、依赖包、密钥。
红色 `✗`（阻塞项）先解决，黄色 `!`（提醒）可以先忽略。

## 第 2 步：一键装依赖 + 组装 + 自检（5 分钟）

```bash
bash deploy-kit/bootstrap.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1
```

它会：建虚拟环境 → 装 `dashscope` / `imageio-ffmpeg` / `aiohttp` →
把插件和人格组装到 `payload/` → 跑离线回归测试。
看到 **`结果：全部通过 OK`** 就说明这套代码在你这台机器上是好的。

## 第 3 步：填密钥（3 分钟）

```bash
cp deploy-kit/env.example .env          # PowerShell: Copy-Item deploy-kit\env.example .env
```

至少填两个：

| 变量 | 去哪拿 |
|---|---|
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com |
| `DASHSCOPE_API_KEY` | https://bailian.console.aliyun.com → API-KEY 管理 |

复查：`python deploy-kit/preflight.py --env .env`

## 第 4 步：装 AstrBot（大脑，10 分钟）

1. 下载 **AstrBot Launcher**（图形化，自带隔离环境），装好启动
2. 国内网络：进「高级 → 通用」，打开 **中国大陆一键加速**
3. 面板 `http://localhost:6185` 打开，设管理员账号

## 第 5 步：装 NapCat（协议端，10 分钟）

1. 下载 `NapCat.Shell.Windows.Node.zip`（自带 Node）
2. 用 `launcher-win10-user.bat` 启动（免管理员）
3. WebUI `http://127.0.0.1:6099` → **用 QQ 小号扫码登录**

## 第 6 步：接起来（2 分钟）

NapCat WebUI 或 `config/onebot11_<QQ号>.json` 里加一个 websocket 客户端：

```json
{ "name": "astrbot", "url": "ws://127.0.0.1:6199/ws", "enable": true }
```

AstrBot 侧：平台适配器选 **QQ(aiocqhttp)**，端口 **6199**。

## 第 7 步：配提供商（3 分钟）

| 位置 | 填什么 |
|---|---|
| 对话模型 | `deepseek-chat` + DeepSeek Key |
| 图片转述 | `qwen3-vl-plus` + 百炼 Key |
| 语音（TTS） | `qwen-audio-3.1-tts-flash` + 百炼 Key（自定义音色见 README「导入音色」） |

## 第 8 步：装插件 + 人格（3 分钟）

```bash
# payload/ 是上一步组装好的，直接拷进 AstrBot 数据目录
cp -r payload/plugins/* <AstrBot数据目录>/data/plugins/
```

数据目录：Launcher 版在 `…/instances/<实例id>/core/data/`；手动部署版在 AstrBot 根目录 `data/`。

人格：面板 → 配置 → 人格 → 新建，名称 `大肥鱼`，
把 `persona/大肥鱼-人格.md` 里 `---` 之后的正文整段粘进去，设为默认。重启。

## 第 9 步：白名单（1 分钟，别跳过）

面板 → 访问控制 → 只放行你自己的会话。私聊发 `/sid` 拿 UMO，形如 `qq:FriendMessage:<你的QQ>`。

## 第 10 步：验收（2 分钟）

| 发什么 | 应该发生什么 |
|---|---|
| 「在吗」 | 有文字回复，带真表情 |
| 一张图 | 她描述得出内容 |
| 一个纯表情 | 她有反应，不是装死 |
| `/help` | 返回指令列表 |

四条都过 = 装完了。想要语音条、视频理解、长期记忆、主动消息，接着看 README。

---

## 卡住了怎么办

| 现象 | 去看 |
|---|---|
| 发消息没反应 | 踩坑合集 #5（NapCat WS 断了，不会自愈） |
| 发纯表情她装死 | #1 |
| 语音合成失败 | #15（qwen-audio 两个源码补丁） |
| 语音里念出「旺柴」 | #14 |
| 插件市场装不上 | #17（GitHub 被卡，走代理） |
| 升级后功能全没了 | #20 |

**一句话原则**：所有「她好像不太对」，最后都能在日志里找到一句明确的错。先看日志，再怀疑模型。
