# PhoneMCP → 云端 AstrBot：本地安卓模拟器控制

让大肥鱼（云端 AstrBot）通过 **MCP** 操作本地 Windows 上的雷电模拟器：
看截图、读 UI、点击、启动应用。ADB 和 PhoneMCP **都不暴露公网端口**，
只走 SSH 反向隧道。

## 架构

```text
Windows 本地
  雷电 14 模拟器 (emulator-5554)
        ↑ adb
  PhoneMCP 127.0.0.1:8009/Phone
        │
        │ ssh -R 8009:127.0.0.1:8009
        ▼
云端主机
  sshd 127.0.0.1:8009
        ↑
  phone_mcp_proxy.py  172.18.0.1:8009 → 127.0.0.1:8009
        ↑
  AstrBot 容器（Docker 网络 dafeiyu_default，网关 172.18.0.1）
```

为什么需要代理：Docker 容器里的 `127.0.0.1` 指容器自身，到不了宿主的回环；
SSH `-R` 默认也只绑宿主回环。代理把 Docker 网关地址 `172.18.0.1:8009`
转发到宿主 `127.0.0.1:8009`，容器就能访问。

## 已验证组合（2026-10-20）

- LDPlayer 14.0.31（Android 14），实例 `720×1280 / 320 dpi / 30 fps`，默认 6 vCPU / 6 GB
- PhoneMCP **v0.5.0 源码运行**（Python 3.14 venv，fastmcp 4.1.1）
- AstrBot v4.29.0 Docker，MCP 配置走 SSE
- 实测工具（18 个）：`list_devices` / `get_current_app` / `get_screenshot` /
  `get_ui_elements` / `tap`（坐标点击）/ `tap_element` / `swipe` / `type_text` /
  `press_key` / `launch_app` / `search_apps` / `listen_audio` 等
- 中文输入：**ADBKeyboard** IME 广播（Android 14 上 PhoneMCP 自带的
  ClipHelper `IClipboard` 旧接口已失效，回退方案不可用）
- B 站：`tv.danmaku.bili` v9.15.0，首次启动的性别/年龄/兴趣引导已处理；
  首页在放视频时 `uiautomator dump` 会报 `could not get idle state`，
  用 `get_screenshot` + `tap(x,y)` 操作，搜索结果页和分享面板可正常读元素树
- 模型：DeepSeek（实测可接受图片输入，截图能进上下文）

## 听模拟器里的声音（listen_audio）

`listen_audio(seconds=15)` 录 Windows 默认扬声器的 loopback（模拟器里 B 站正在
播放的声音），经 SSH `-L 8012` POST 到云端 `asr_relay.py`，由百炼
`qwen3-asr-flash` 转写成文字后返回。

- Windows 依赖：`soundcard`、`numpy`（PhoneMCP venv 已装）
- 云端：`/opt/dafeiyu/tools/asr_relay.py` 只监听 `127.0.0.1:8012`，API key
  从 AstrBot 的 `cmd_config.json` 读取，**不落到 Windows**
- 隧道：`start_reverse_tunnel.ps1` 在 8009 隧道上带
  `-L 8012:127.0.0.1:8012`；`start_phone_service.ps1` 会顺带拉起云端 relay
- 用法：先让视频播放，再调用 `listen_audio`；静音返回 `silent: true`，
  人格规则要求她如实说没听到，不补造台词
- 验证：SAPI 中文语音 → loopback 录音 → 云端 ASR，返回
  `大肥鱼，你现在能听见我说话了吗？`

## 1. 本地模拟器

1. 安装雷电 14，启动一个实例。
2. 设置分辨率 `720×1280`、DPI `320`、帧率 `30`（默认性能即可）。
3. 确认 ADB 能看到设备：

```bat
cd /d D:\leidian\LDPlayer14
adb devices
:: emulator-5554    device
```

## 2. 运行 PhoneMCP（源码版）

官方 Windows exe 在中文控制台会在启动横幅的 emoji 上崩溃：

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
```

v0.5.0 和 v0.3.4 都有这个问题。因此从源码运行，并在 `main.py` 入口强制
stdout/stderr 使用 UTF-8：

```powershell
git clone https://github.com/kengerlwl/phoneMcp.git D:\AI\PhoneMCP\src
cd D:\AI\PhoneMCP\src
git apply D:\GitHub\dafeiyu-astrbot\deploy\phone_mcp\patches\phone_mcp_local_20261020.patch

cd D:\AI\PhoneMCP
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r src\requirements.txt
# 固定 fastmcp 4.1.1：2.x 与当前 mcp 2.3 存在导入冲突
.\venv\Scripts\python.exe -m pip install "fastmcp==4.1.1"

# 启动（复用雷电自带的 adb）
.\deploy\phone_mcp\start_phone_mcp.ps1
```

补丁包含：`main.py` 强制 UTF-8（官方 exe 在中文控制台 emoji 崩溃）、
新增 `tap(x,y)` 坐标点击工具、非 ASCII 输入优先走 ADBKeyboard、
`B站`/`哔哩哔哩` 应用别名。旧版单独打 UTF-8 补丁的
`patch_phone_mcp_main.py` 仍保留，但新装建议直接用上面的完整补丁。

`fastmcp 4.x` 的实际 SSE 端点是 **`/Phone`**，不是 PhoneMCP 打印的
`/Phone/sse`；以 `GET /Phone` 返回的 `event: endpoint` 为准。

### 中文输入（ADBKeyboard）

Android 14 上 PhoneMCP 自带的 ClipHelper 调用的 `IClipboard` 旧接口已不存在，
直接报 `NoSuchMethodException`。装一个 ADBKeyboard 作为默认输入法，中文/emoji
走 IME 广播，稳定可用：

```powershell
# APK 来自 https://github.com/senzhk/ADBKeyBoard（开源，约 17 KB）
adb -s emulator-5554 install -r ADBKeyboard.apk
adb -s emulator-5554 shell ime enable com.android.adbkeyboard/.AdbIME
# 本机镜像上 `ime set` 会报 Unknown input method，用 settings put 才生效
adb -s emulator-5554 shell settings put secure default_input_method com.android.adbkeyboard/.AdbIME
```

`start_phone_service.ps1` 每次启动都会检查并重新设一次默认输入法。

## 3. 云端代理

把 `phone_mcp_proxy.py` 放到云端（例如 `/opt/dafeiyu/tools/`），运行：

```bash
nohup python3 /opt/dafeiyu/tools/phone_mcp_proxy.py \
  > /opt/dafeiyu/tools/phone_mcp_proxy.log 2>&1 &
```

默认监听 `172.18.0.1:8009`，转发到 `127.0.0.1:8009`。
如果 compose 网络名不是 `dafeiyu_default`，用
`docker network inspect <网络名> | grep Gateway` 确认网关地址后改脚本顶部常量。

## 4. SSH 反向隧道（Windows 侧）

```powershell
.\deploy\phone_mcp\start_reverse_tunnel.ps1 -Server <server-ip>
```

脚本带 `ServerAliveInterval=15`、`ExitOnForwardFailure` 和断线 3 秒重试循环。
也可以手动执行：

```bash
ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -R 8009:127.0.0.1:8009 <user>@<server-ip>
```

## 5. AstrBot MCP 配置

`astrbot/data/mcp_server.json`：

```json
{
  "mcpServers": {
    "phone-mcp": {
      "url": "http://172.18.0.1:8009/Phone",
      "transport": "sse",
      "active": true
    }
  }
}
```

改完重启 AstrBot，日志应出现：

```text
Connected to MCP server phone-mcp, Tools: ['list_devices', ...]
MCP services initialization completed: 1/1 successful, 0 failed.
```

大肥鱼人格的 `tools` 为空（null）时默认拥有全部工具，无需额外勾选；
若人格设置了工具白名单，需要把 `list_devices`、`get_ui_elements`、
`get_screenshot`、`launch_app` 等加入白名单。

## 6. 日常启停（开机自恢复）

```powershell
# 启动：模拟器 + PhoneMCP + SSH 隧道 + 云端代理（幂等，可重复跑）
.\deploy\phone_mcp\start_phone_service.ps1 -Server <server-ip>

# 开机/重启后刷新云端 AstrBot 的 MCP 会话
.\deploy\phone_mcp\start_phone_service.ps1 -Server <server-ip> -RestartBot

# 停止本地 PhoneMCP + 隧道（默认不关模拟器）
.\deploy\phone_mcp\stop_phone_service.ps1

# 连模拟器一起关
.\deploy\phone_mcp\stop_phone_service.ps1 -StopEmulator

# 看状态
.\deploy\phone_mcp\phone_service_status.ps1
```

开机自启：

```powershell
.\deploy\phone_mcp\install_phone_autostart.ps1 -Server <server-ip>
```

优先注册计划任务 `DafeiyuPhoneService`（登录时运行）；没有管理员权限时自动退回
当前用户的“启动”文件夹，生成 `DafeiyuPhoneService.cmd`，效果相同。自启命令带
`-RestartBot`，会重启云端 AstrBot 并跑 `heal_ws.py`，让 MCP SSE 会话重新连上。

断线处理：`start_reverse_tunnel.ps1` 自带 3 秒重试循环和 `ServerAliveInterval=15`；
AstrBot 侧对 `ClosedResourceError` 有最多 2 次自动重连。若网络长时间中断后手机工具
仍不通，执行一次 `start_phone_service.ps1 -Server <server-ip> -RestartBot` 即可。

`fastmcp` 的 SSE 会话是长连接：PhoneMCP 重启后，AstrBot 必须重连才能拿到新工具列表，
所以脚本提供 `-RestartBot`。

## 7. 验收

从 AstrBot 容器直接验证链路：

```bash
docker exec -i astrbot python3 - <<'PY'
import asyncio
from mcp.client.sse import sse_client
from mcp import ClientSession

async def main():
    async with sse_client("http://172.18.0.1:8009/Phone") as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])
            print(await s.call_tool("list_devices", {}))

asyncio.run(main())
PY
```

QQ 实测话术（基础控制）：

> 肥鱼，看看你的虚拟手机现在是什么页面，然后打开设置，告诉我屏幕上实际显示的两个选项。

三条验收：模拟器画面真的变化、AstrBot 日志出现上述工具调用、回答与截图文字一致。

B 站活动验收话术：

> 肥鱼，用你的虚拟手机去 B 站，自己选个想跟我分享的主题，看看前三个搜索结果，挑一个回来聊聊。告诉我标题、UP 主和你选它的理由，能拿到分享链接就一起发来。按实际看到的内容说就好。

四条验收：她真的打开 B 站并完成一次搜索、回答里的标题和 UP 主与模拟器截图一致、
选的内容确实来自前三个结果、没有编造视频剧情（只读标题/简介/截图时按这些信息聊）。

## 8. 未来任务（每日 20:30 逛 B 站）

AstrBot 的「未来任务」就是 `cron_jobs` 表里的 `active_agent` 任务。2026-10-20 已建：

- 名称：`大肥鱼晚间逛B站`
- Cron：`30 20 * * *`，时区 `Asia/Shanghai`
- 会话：`qq:FriendMessage:<主人QQ>`（主号私聊）
- 提示词要点：结合最近聊天和长期记忆选主题（别每天同一类）→ 手机打开 B 站搜索 →
  看前三个结果 → 挑一条打开看标题/UP/简介/页面数据 → 私聊汇报，只讲实际看到的 →
  能拿分享链接就拿，拿不到直说 → 发完用 `memorize_long_term_memory` 记活动摘要，
  主人的评价等回复后再补 → 过程不逐条直播，最后一条发完。
- 依赖：20:30 时 Windows 电脑、LDPlayer、PhoneMCP、SSH 隧道和云端 AstrBot 都在线；
  手机工具是已注册的 MCP 工具，活动任务可直接调用。

在 WebUI 的「未来任务」页能看到这条；也可以直接查库：

```bash
python3 - <<'PY'
import sqlite3
con = sqlite3.connect('/opt/dafeiyu/astrbot/data/data_v4.db')
for r in con.execute("select name, cron_expression, timezone, enabled, next_run_time from cron_jobs"):
    print(r)
PY
```

`next_run_time` 存的是 UTC；北京时间 20:30 = UTC 12:30。

## 安全注意

- ADB 只在本机使用，不要 `adb tcpip` 或把 5555 端口映射到公网。
- PhoneMCP 只绑 `127.0.0.1`；云端只绑 Docker 网关，不绑 `0.0.0.0`。
- SSH 反向隧道只转发这一个端口；服务器安全组不要放行 8009。
- PhoneMCP 源码和雷电均由用户本机运行，不随本仓库分发。
- ADBKeyboard 是第三方开源输入法（`senzhk/ADBKeyBoard`），只装在模拟器内；
  仓库不分发 APK。模拟器上的 B 站保持未登录状态，避免真实账号被自动化操作。
