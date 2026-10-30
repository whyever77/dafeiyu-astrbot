# ☁️ 云上安家 · Docker 部署套件

> 把大肥鱼从 Windows 桌面搬进一台 Linux 云服务器，7×24 在线。
> 本目录是这次迁移沉淀下来的可复用套件（已在 2C4G / Ubuntu 24.04 实弹演练）。

## 架构

```
QQ 用户 ⇄ 腾讯服务器 ⇄ NapCat(Docker) ⇄ WebSocket(6199) ⇄ AstrBot(Docker) ⇄ DeepSeek / 百炼 / 硅基流动
```

- 两个容器在同一个 compose 网络里，NapCat 通过 `ws://astrbot:6199/ws` 反向连接 AstrBot
- 四个核心补丁（TTS WebSocket、整段语音/分泡、气泡发送节奏、布局工具）用
  **bind-mount** 接入容器，升级镜像不丢补丁
- 嵌入 provider 从本地 Ollama 切到**硅基流动 bge-m3**（同模型，向量余弦 0.9999+，零重嵌）

## 目录约定

```
/opt/dafeiyu/
├── compose.yaml        # 编排（基于本目录的 compose）
├── Dockerfile          # 官方镜像 + edge-tts / imageio-ffmpeg
├── patches/            # 四个核心补丁（从旧机 core 提取 + 分泡/整段语音）
├── astrbot/data/       # 旧机 core/data（排除 temp / logs / *.bak_*）
├── napcat/config|qq/   # NapCat 配置与登录态
├── napcat/machine-id   # 可选：固定的 machine-id（启用前手动创建）
├── backups/            # 每日滚动备份
└── incoming/           # 迁移时的一次性上传暂存
```

## 迁移步骤（要点）

1. **旧机停服**：先停看门狗，再停 AstrBot / NapCat（避免同 QQ 双登互踢），
   然后打包 `core/data`（排除 `temp` / `logs` / `*.bak_*`，26MB 左右）
2. **服务器**：装 Docker；国内拉镜像需配置 registry mirror（如 `docker.1panel.live`，
   以当时可用为准）
3. **构建镜像**：`docker compose build`（补 edge-tts / imageio-ffmpeg）
4. **起 AstrBot** → 灌数据 → 起 NapCat → 打开 WebUI（6099 端口）扫码登录小号
5. **切嵌入**：运行 `apply_sf_embedding.py`（自动从插件配置读 key，幂等）
6. **安全组**：开放 6185（AstrBot 面板）与 6099（NapCat WebUI）
7. **备份**：`backup.sh` + cron；另可用本地脚本定期把最新备份拉回电脑

## 踩过的坑

- **快速登录变量名**：Docker 入口脚本读的是 `ACCOUNT`（`qq --no-sandbox -q $ACCOUNT`），不是 `NAPCAT_ACCOUNT`；
  再配 `NAPCAT_QUICK_ACCOUNT` 让 Worker 重启后自动尝试快速登录。容器**重建**会丢快速登录记录（设备指纹变），
  改配置优先 `docker restart` 或 WebUI，别频繁 `up -d` 重建。
- **设备名/指纹每次重扫都在变**：QQ 认设备靠「设备名 + 指纹」。容器重建时 hostname 默认变成新容器 ID，
  `/etc/machine-id` 也会重新生成，QQ 会把这次登录当成新设备，风控加码、更容易再掉线。
  compose 已固定 `hostname: dafeiyu-napcat`；想连 machine-id 一起固定（可选，社区经验）：
  1. 先在宿主机执行 `cat /proc/sys/kernel/random/uuid > /opt/dafeiyu/napcat/machine-id`
  2. 取消 compose 里 `machine-id:/etc/machine-id:ro` 那行注释
  3. 重建一次容器并重扫一次码；之后设备名与指纹保持不变

  **注意**：bind 源文件必须先存在，Docker 遇到不存在的源会建成目录，容器会起不来。
- **QQ 登录态不可跨平台迁移**：Linux NapCat 必须重新扫码一次
- **双登互踢**：旧机自启项（AstrBot Launcher / 启动大肥鱼QQ）务必停用
- **升级覆盖补丁**：核心补丁不要改镜像，bind-mount 挂进去
- **嵌入别重跑**：同款 bge-m3 的向量互相兼容，不需要重建索引
  （验证法：取一条旧向量与云端新嵌入算余弦，≈1 即可）
- `data/` 里可能混着旧机的 `site-packages` 残留，可忽略

## 版本与升级

- 本套件钉死已验证组合：**AstrBot `soulter/astrbot:v4.29.0`** +
  **NapCat 镜像 `mlikiowa/napcat-docker:v4.18.37`**（`Dockerfile` 与 `compose.yaml` 两处）。
  升级时：改两处 tag → `docker compose build && docker compose up -d` →
  **重新对照 `patches/` 的补丁在新版是否仍适用**（补丁是核心源码的修改版，版本不匹配行为会变）。
- 只要"基础聊天"（不玩百炼语音 / 不挂 TTS 补丁）：删掉 compose 里两条 `patches/...` 的
  bind-mount 即可，其余照常。补丁文件随仓库提供（`patches/`，附来源与 AGPL-3.0 说明）。

## 安全默认

- 模板默认把 AstrBot 面板（6185）与 NapCat WebUI（6099）都绑在 **127.0.0.1**，远程走 SSH 隧道：
  `ssh -L 6185:127.0.0.1:6185 -L 6099:127.0.0.1:6099 root@<服务器>`，
  浏览器开 `http://127.0.0.1:6185`；扫码登录 NapCat 时同样走隧道打开 `http://127.0.0.1:6099`。
- 确需公网直连：改回 `"6185:6185"` / `"6099:6099"` 并配置安全组，同时务必改强密码。

## 备份与恢复

`backup.sh` 的行为：
- 备份前**短暂停掉 astrbot 容器**（SQLite 在写入中直接打包可能拿到不一致快照），打完立刻拉起；
- 先写临时包、成功后才改正式名；失败写 `backups/backup.log` 且非 0 退出；
- 备份内容：`astrbot/data` + `napcat/config` + `compose.yaml` + `Dockerfile` + `patches/` + `heal_ws.py`（恢复所需全带）；
- **拉起后自动跑 `heal_ws.py`** 自愈 WebSocket（实测 AstrBot 重启后 NapCat 的 WS 不会自己回来）；
  平时若发现"发消息没反应"，手动跑一次 `python3 /opt/dafeiyu/heal_ws.py` 即可（不重启 QQ、不扫码）。
  它只看 AstrBot 容器内 6199 有没有 ESTABLISHED 连接（拿不到才退回日志先后顺序），
  只拨指向 AstrBot 的那条客户端并保留原 `enable`；没修好返回非零。
- **恢复失败也非 0 退出**：拉起失败、容器不是 Running、`heal_ws.py` 报错或 30 秒内没重连，
  都会记 `[ERROR]` 并返回非零；被 Ctrl-C/SIGTERM 打断也走同一条恢复路径。
  备份前容器本来就是停的，则跳过停止/恢复，不会顺手把它拉起来。

恢复：`docker compose stop` → 在 `/opt/dafeiyu/` 解包（`tar -xzf ... -C /opt/dafeiyu`）→
`docker compose up -d`；NapCat 起好后如未登录，按二维码重扫。

## 插件运行参数（可选）

在 `astrbot/data/plugins/astrbot_plugin_face_convert/` 下放 `config.json` 可覆盖默认值：

```json
{"hedge_eraser": true, "face_cooldown_minutes": 10, "max_media_mb": 80,
 "video_max_duration_s": 300, "video_max_frames": 16, "video_audio_chunk_s": 60,
 "video_timeout_s": 300, "vl_timeout_s": 60, "asr_timeout_s": 60,
 "max_concurrent_media": 1, "video_workers": 2, "media_queue_timeout_s": 10}
```

`hedge_eraser=false` 可整体关闭"嘴硬橡皮擦"；含"重发/再说一遍"等请求词的回复本来就永不误删。

旧配置不会自动改写，请合并更新后重载插件或重启 AstrBot。详见 [3～5 分钟视频识别](../docs/视频识别.md)。

## 手机控制（PhoneMCP + 本地模拟器）

`phone_mcp/` 提供“云端 AstrBot 操作本地雷电模拟器”的完整接线：
PhoneMCP 跑在 Windows、ADB 只连本机、SSH 反向隧道回云端，再由
`phone_mcp_proxy.py` 把 Docker 网关桥接到隧道入口。包含源码启动脚本、
UTF-8 补丁和 `mcp_server.json` 示例。详见 [phone_mcp/README.md](phone_mcp/README.md)。
