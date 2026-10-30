# dafeiyu-admin MCP

给 Claude Code 用的**只读优先**运维 MCP。底层是 Windows OpenSSH，stdio 传输，纯标准库，无第三方依赖。

```text
Claude Code ──stdio──> server.py ──ssh -i ~/.ssh/ecs_dafeiyu──> root@<你的云端主机>
```

## 配置主机地址

仓库里**不写真实地址**。二选一，写在本地（别提交）：

- 环境变量 `DAFEIYU_SSH_HOST=root@1.2.3.4`
- 或文件 `~/.dafeiyu-admin/host`，一行 `root@1.2.3.4`

`register.ps1` 会把解析到的地址以 `-e DAFEIYU_SSH_HOST=...` 写进 Claude Code 的 MCP 注册项。
两个 QQ 号也不写进仓库：可选设置 `DAFEIYU_OWNER_QQ` / `DAFEIYU_BOT_QQ`，
只用于 `webui_info` 展示；不设置时返回空字符串。

## 工具

| 工具 | 读写 | 作用 |
|---|---|---|
| `health` | 只读 | 容器状态、NapCat 登录日志、AstrBot WS 日志、端口、磁盘/内存 |
| `logs` | 只读 | `docker logs`，支持 `since` / `grep` / `tail` |
| `qr` | 只读 | 拉最新 `qrcode.png` 到 `~/.dafeiyu-admin/qr.png`，内联返回图片 |
| `plugins` | 只读 | 列出 AstrBot 插件 |
| `cron_jobs` | 只读 | 只读 SQLite 查 `cron_jobs`（名称/表达式/时区/下次运行） |
| `webui_info` | 只读 | NapCat WebUI 基础 URL、AstrBot 面板、两个 QQ 号（若配置了环境变量）；**不返回 token** |
| `restart` | 写 | `docker restart <container>`，`confirm` 必须是 `RESTART` |
| `heal_ws` | 写 | 跑 `/opt/dafeiyu/heal_ws.py`，`confirm` 必须是 `HEAL` |
| `compose_up` | 写 | `docker compose up -d`，`confirm` 必须是 `UP` |

## 安全边界

- 不是通用远程 shell：没有任意 `exec` 工具，容器名只允许 `astrbot` / `napcat`。
- `logs` 的 `since` 只接受 `30m` / `2h` / `1d` 这类格式，`grep` 拒绝 shell 元字符。
- `qr` 只读取 NapCat 的二维码文件。
- `cron_jobs` 用 SQLite `mode=ro`，只执行固定 SELECT。
- 写操作必须传字面量确认串；不会因为“看起来该重启”就自动执行。
- 所有调用写入 `~/.dafeiyu-admin/audit.jsonl`（不记录 token / 消息正文）。
- `logs` 输出会把 `txz.qq.com/p?k=...` 的一次性登录 URL 和 `token=...` 脱敏。
- **不返回 WebUI token**；需要时在服务器上自行读取：
  `cat /opt/dafeiyu/napcat/config/webui.json`

## 注册

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\register.ps1
```

脚本会先 `claude mcp remove dafeiyu-admin`（忽略不存在），再以 user scope 注册：

```powershell
claude mcp add --scope user dafeiyu-admin -- `
  "C:\Users\ACER\AppData\Local\Python\pythoncore-3.14-64\python.exe" `
  "D:\GitHub\dafeiyu-astrbot\deploy\ops_mcp\server.py"
```

注册后新开一个 Claude Code 会话才能看到工具；`claude mcp list` 应显示 `dafeiyu-admin ... Connected`。

## 自检

```powershell
& "C:\Users\ACER\AppData\Local\Python\pythoncore-3.14-64\python.exe" -I .\server.py --selftest
```

只跑只读工具（`health` / `plugins` / `cron_jobs` / `webui_info`），不会重启任何东西。

## 常见判断

- `health.napcat_login_lines` 里出现 `Login Error, ErrType: 1 ErrCode: 3` + `请扫描下面的二维码` → **没登录**。用 `qr` 拉码，用户本人用手机 QQ 扫。
- AstrBot 日志里 `aiocqhttp(OneBot v11) 适配器已连接` **不等于** QQ 协议端在线；只说明本地 WS。唯一判据仍是 NapCat 登录日志。
- 重启后等约 4 分钟再判断，别连着重启。

## 卸载

```powershell
claude mcp remove dafeiyu-admin
```
