# 🐋 大肥鱼 · AstrBot QQ 机器人全家桶

> 一只住在 QQ 里的蓝色鲸鱼娘：会看、会听、会说话、有记忆、会主动找你，
> 还能进群和你的朋友打成一片，也会嘴硬地催你吃饭。基于 **AstrBot + NapCat + DeepSeek + 阿里云百炼** 搭建。

大肥鱼是 DeepSeek 蓝鲸 logo 的**社区二创形象**（非官方），本项目把「让她住进 QQ 里」
的全套实践开源出来：一个高完成度的 AstrBot 插件、一套完整人格、以及踩过所有坑的实录。

本分支新增[B站评论区互动 v0.5](deploy/bili_activity/INTERACT_V05.md)：在现有提议、主人批准、操作账本和回读链上扩展点赞/取消赞、投币、弹幕、互动配额与风控、回复监听和评论区观察。默认仍逐条审批，新增开关默认关闭；`verified` 与公开可见性分别报告。保留[研究方法复用](deploy/bili_activity/LIFE_V04.md)、[来源笔记与自主续写](deploy/bili_activity/LIFE_V03.md)及既有真实 smoke。v0.5 只完成本地代码和离线测试，未部署、未操作真实账号；开启、低配额建议、回滚和主人小号 smoke 见[互动文档](deploy/bili_activity/INTERACT_V05.md)。

## ✨ 能力清单

| 感官 | 能力 | 实现 |
|---|---|---|
| 👁️ 看 | 图片 / 表情包 / 视频（默认覆盖前 5 分钟，最多 16 帧） | qwen3-vl 转述 + ffmpeg 抽帧 |
| 🎧 听 | 语音消息 / 视频音轨（默认前 5 分钟，每 60 秒一段）| NapCat 转码 + qwen3-asr-flash |
| 🎙️ 现场听 | 模拟器里正在播放的视频原声（B 站等） | Windows loopback 录音 + SSH -L 8012 + qwen3-asr-flash |
| 🔊 说 | 语音条（含自定义音色） | 百炼音色复刻 + qwen-audio-3.1-tts-flash |
| 😀 表情 | 真 QQ 表情（326 张脸） | 自研插件把 `[旺柴]` 变成真实表情段 |
| 🧠 记 | 长期记忆 | livingmemory + bge-m3 嵌入（本地 Ollama / 硅基流动托管） |
| 📚 知识 | 功能手册 / 热梗词典（聊天里现查现答） | AstrBot 知识库 + bge-m3 嵌入 |
| 🏠 群聊 | 进群聊天：@ / 引用 / 喊「大肥鱼」都能唤醒 | NapCat 群消息 + 唤醒词 |
| 👥 认人 | 分得清谁在说话：群里看昵称、访客标「朋友」 | 插件注入身份标记（对方不可见） |
| 🔐 隔离 | 每个私聊 / 每个群各一册记忆，互不串账 | livingmemory `session` 作用域 |
| 📲 主动 | 随机 + 沉默触发，可按需联网找话题；每天 20:30 自己逛 B 站，说去逛必须当轮真调工具 | proactive_chat + AnySearch + cron |
| 🧭 日程 | 每轮知道自己名下真实存在的定时任务，不再说“我没有日程” | schedule_awareness 实时注入 cron_jobs（只读、按会话隔离） |
| 📱 手机 | 看截图、读 UI、坐标点击、中文搜索、逛 B 站 | PhoneMCP + ADB + ADBKeyboard |
| 🛠️ 运维 | 一键体检、日志、二维码、受控重启 | dafeiyu-admin MCP（SSH，只读优先） |
| 🌐 网页 | 只读浏览、搜索、提取网页信息（独立 Chrome） | Browser Use + BrowserMCP（SSE 8010 / SSH 反向隧道） |
| 🛡️ 防护 | 白名单 / 防注入 / 草稿箱 | 多层设计，见踩坑合集 |

## 📦 内容

```
plugin/astrbot_plugin_face_convert/   ← 核心插件（AstrBot 插件，直接可装）
plugin/astrbot_plugin_schedule_awareness/ ← 🧭 把她的定时任务实时注入系统提示词
plugin/astrbot_plugin_bili_activity/   ← 自主浏览、持续目标、截图、来源笔记与研究方法
persona/大肥鱼-人格.md                 ← 人格提示词（可直接导入 AstrBot）
persona/课程记忆-模板.md               ← "让她自己悟"的教学记忆模板
docs/部署指南.md                       ← 从零搭建的路线图
docs/导入指南.md                       ← 人格 / 课程记忆 / 音色 手把手导入教程
docs/指令手册.md                       ← 指令速查表（语音开关 / 画图自拍 / 点歌 / 表情包…）
docs/生图玩法.md                       ← 生图与自拍：模型选择 / 参考图 / 调参笔记
docs/知识库笔记.md                     ← 知识库接线：功能手册 + 热梗词典怎么搭
docs/知识库-热梗词典示例.md            ← 现成的热梗词典（含接法），可直接导入
docs/踩坑合集.md                       ← ⭐ 最有价值的部分：所有坑与解法
deploy/                                ← ☁️ 云上安家套件（Docker Compose / 备份 / 嵌入切换）
deploy/phone_mcp/                      ← 📱 模拟器接入（PhoneMCP / 反向隧道 / 启停与开机自启）
deploy/ops_mcp/                        ← 🛠️ 云端运维 MCP（health/logs/qr + 受控 restart/heal_ws）
deploy/browser_use/                    ← 🌐 只读浏览器任务（Browser Use MCP / 反向隧道 / 启停）
deploy/bili_activity/                  ← B站服务、活动开关、升级及验证说明
deploy/incident_20261020/              ← 🧹 旧测试记录隔离脚本与复盘
CHANGELOG.md                           ← 更新日志
deploy/                                ← 云服务器部署套件（Docker Compose + 补丁 + 备份 + 运维笔记）
tests/                                 ← 回归测试（插件文本规则 / 路径解析）
assets/avatars/                        ← 头像素材（18 张，v3-20261025 新立绘）
assets/voice-demo/                     ← 音色样本与复刻链路说明
```

## 🚀 快速开始

1. 按 `docs/部署指南.md` 搭好 AstrBot + NapCat + QQ 小号
2. 把 `plugin/astrbot_plugin_face_convert/` 放进 AstrBot 的 `data/plugins/`
   （插件会自动读取你 AstrBot 配置里的 DashScope key，用于语音/视频理解）
3. 在 AstrBot 里新建人格：把 `persona/大肥鱼-人格.md` 的正文**整段粘贴**（v2 单块提示词，无需拼接；详见导入指南）
4. 想要语音条 / 记忆 / 自定义音色？照 `docs/导入指南.md` 继续
5. 重启，开聊 🎉
6. 想 7×24 挂云端？看 `deploy/README.md`（Docker 一键编排，已实弹演练）

## 🔌 插件做了什么

`astrbot_plugin_face_convert` 是一个「出站整形 + 入站翻译」双通道插件：

**出站（她说什么）**
- `[旺柴]` `[吃瓜]` 等中括号表情名 → **真正的 QQ 表情段**（QQ 不会渲染机器人发的纯文本表情码）
- 草稿箱：双层括号 `（（ ））` 里的内容会被撕掉（给她一个"自己咽回去"的出口）
- 三道轻量橡皮擦：嘴硬小尾巴 / meta 口癖句 / 表情冷却（可逐道关闭，见源码顶部）
- 中文标点归一：顿号后接「也 / 就 / 但 / 所以 / 或者」这类分句连接词时改成逗号，「」统一成 “”

**入站（她看到什么）**
- 纯表情消息的文本是空的，会被上游当"空消息"跳过 → 翻译成 `[旺柴]` 文本
- 图片 / 表情包 → 补上"[对方发来一张图片]"，交给 AstrBot 的图片转述
- 语音 → NapCat 转 mp3 → qwen3-asr-flash 听写 → 文本进上下文
- 视频 → 本地下载 → ffmpeg 抽帧 + 抽音轨 → qwen3-vl 按时间分批看图 + qwen3-asr 分段听声；超时保留已有结果
- 身份标记 → 群聊消息自动带上 `[昵称(QQ号)]`（管理员标「主人」）、访客私聊标「主人的朋友」——
  让她分得清谁在说话；标记只进她的上下文，**对方看不到**；群里只喊名字没内容时，自动补
  「（喊了你一声）」的回应由头

**可调参数（可选）**：插件目录下放 `config.json` 可覆盖 `hedge_eraser`（嘴硬橡皮擦开关）、
`face_cooldown_minutes` 和媒体处理参数。**3～5 分钟视频已作为默认配置**，
旧 `config.json` 会覆盖新默认值，升级时请按 [视频识别说明](docs/视频识别.md) 合并更新。
回归测试：`python tests/test_face_rules.py`、`python tests/test_video_processing.py`。

## 🧩 推荐搭配（可选的"花活"插件）

以下都在 AstrBot 官方插件市场里，与本项目**零冲突**（都是踩过坑验证过的组合）：

| 插件 | 玩法 |
|---|---|
| 链接解析 `parser` | 丢 B站/抖音/小红书链接，她自动解析内容接着聊 |
| 点歌 `music` | "我想听xx" → 她发歌/语音条 |
| 戳一戳 `pokepro` | 被戳触发反应（反戳/回话/表情）※记得关掉它默认的定时戳任务 |
| 表情包偷取 `stealer` | 她"偷"你们聊到的表情包、自动分类，聊天时按情绪发（需配视觉模型） |
| 表情包生成 `memelite` | 现做沙雕表情 |
| 生图 `omnidraw` | `/画 xxx`、`/自拍 场景`（需配生图 API，如硅基流动有免费模型档） |

> 💡 **小贴士·定时服务配方**：AstrBot 内置的「未来任务」能让机器人**定时带着人设自己醒来发消息**。
> 两个实测好用的配方，照抄即可（目标会话=你们的私聊，人格选她）：
>
> | 服务 | 时间 | 任务说明（照抄） |
> |---|---|---|
> | 🌙 催睡觉 | 每晚 23:30 | 现在是晚上 23:30 左右。请用平常聊天的语气催主人早点睡觉：嘴上嫌弃他「又熬夜！」，心里舍不得、想多陪一会儿；一两句话就好，然后用发消息工具发给主人。 |
> | ☀️ 叫起床 | 每天 07:00 | 现在是早上 7 点。请叫他起床：元气满满的早安、嫌弃他赖床，催他别磨蹭；一两句话就好，语气像平时的她，然后用发消息工具发给主人。 |
>
> 💡 装插件时如果卡 GitHub：给安装接口传 `proxy` 前缀加速（见《踩坑合集》#17）。

## 🙏 致敬与出处

- **大肥鱼**：DeepSeek 蓝鲸的社区二创（"你这只吃白饭的蓝色大肥鱼"梗），非官方角色
- 角色设定规范参考社区项目 [YunYueSama/codex-deepseek-pet](https://github.com/YunYueSama/codex-deepseek-pet)
- 依赖生态：[AstrBot](https://github.com/AstrBotDevs/AstrBot) · [NapCat](https://github.com/NapNeko/NapCatQQ) · [livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) · [proactive_chat](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat)

## 📄 License

MIT（见 LICENSE）。人格文本与插件代码可自由使用，转载注明出处即可。

