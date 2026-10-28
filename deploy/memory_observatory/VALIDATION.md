# 记忆星海 v0.1 · 验收记录

验收日期：2026-10-28。基于本地 `021355a`，新分支 `codex/memory-observatory-v01`。

## 结论与边界

本地只读观测台、v1 快照协议和模拟策略已通过下列验证。
没有声称她的在线记忆能力已经改变；LivingMemory 适配器、真实对话召回与写回仍未实现、未验证。

| 项目 | 证据 | 结果 |
|---|---|---|
| 细节先淡、意义更慢 | 精确半衰期断言，时间递增时单调衰减，保留原文和摘要 | PASS |
| 保留与巩固 | 固定保留不衰减；巩固仅乘稳定度；重复请求幂等、重置恢复 | PASS |
| 可信度独立 | 搜索/重复浏览/模拟巩固不增加 confidence；缺失仍为 null | PASS |
| 作用域隔离 | 过滤先于检索与图扩散；跨 scope 关联与实验 ID 被拒绝 | PASS |
| 线索唤回 | 沉睡片段仍可由关键词唤回；无关词返回空；邻居含 via 和 reason | PASS |
| JSON 导入 | 格式/时间/体积/数值校验；失败不替换原数据；推导边往返不增殖 | PASS |
| 本机边界 | 仅回环绑定，Host/Origin 校验，路径白名单，无任意文件读取 API | PASS |
| 安全文本显示 | 合成 `<script>` / `onerror` 文本作为文字显示，未执行且没有生成对应 DOM | PASS |
| 桌面与移动界面 | Chrome 1600×1150、390×844，无横向溢出；截图人工复查 | PASS |
| 原有 B站代码回归 | 371 项，`OK (skipped=11)`；原模块零改动 | PASS |
| 真实记忆与在线行为 | 未连接运行中 LivingMemory、未调用 QQ 或记忆写入工具 | UNVERIFIED |
| 其他浏览器/大规模数据 | 未在 Safari/Firefox/真实移动设备、万级图上测试；本版限制 500 条 | UNVERIFIED |

## 命令与实际结果

Windows 默认 Python：

```text
python -B -m unittest discover -s tests -p test_memory_observatory.py -v
Ran 27 tests in 0.912s
OK
```

WSL：

```text
wsl -e bash -lc 'cd /mnt/d/GitHub/dafeiyu-astrbot && python3 -B -m unittest discover -s tests -p test_memory_observatory.py -v'
Ran 27 tests in 0.941s
OK
```

两次均 0 skip。初次 WSL 调用受到沙箱 `E_ACCESSDENIED` 限制，使用获准的沙箱外调用复跑。

语法检查：

```text
node --check deploy/memory_observatory/static/app.js
node --check deploy/memory_observatory/static/graph.js
```

均 exit 0。浏览器 smoke（先启动本地演示服务）：

```text
node tests/memory_observatory_browser.mjs
```

覆盖页面加载、记忆选择/2D 控件、时间模拟、保留/重置、召回路径、时间线/实验室、群聊隔离、JSON 文件选择、
注入文本显示、移动布局和未捕获 JavaScript 异常。

最终浏览器结果：**10 项 PASS，0 failures，0 未捕获 JavaScript 异常**。
原始结果保存于 [assets/browser-results.json](assets/browser-results.json)，桌面截图见 [assets/preview.png](assets/preview.png)。

原有 B站回归使用已有测试虚拟环境，并仅为测试进程优先选择 Git Bash：

```powershell
$env:Path = 'C:\Program Files\Git\bin;' + $env:Path
& 'C:\Users\ACER\AppData\Local\Temp\dafeiyu-venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_bili_*.py' -v
```

```text
Ran 371 tests in 22.265s
OK (skipped=11)
```

跳过项为 6 项需要 ffmpeg 的媒体测试、5 项仅 Linux 支持的安全导出测试。本次没有复跑原有 B站的 WSL 全量；
WSL 结果仅针对新记忆组件。

完整记录前置失败：第一次原有测试在沙箱内因临时目录权限报 `PermissionError`；获准沙箱外复跑后，
371 项中有 1 项 `test_deploy_preserves_config_and_archives_live_database` 失败，因为默认 `bash` 解析为
WindowsApps 的 WSL 入口，错误为 `/bin/bash: D:GitHub...deploy.sh: No such file or directory`。
没有修改或放宽测试；临时让 PATH 优先使用现有 Git Bash 后，完整重跑得到上方 371 项结果。

## 开发中发现并修复的问题

- 初次协议测试发现：已有推导边再次导入时会补出更多边。归一化现有边计入上限后，同一快照往返不再增殖。
- 截图复查发现：数值 `gist` 可及度覆盖了文字摘要。数值改为 `gist_retention`，并在模型与浏览器测试断言文字摘要保留。
- 星图最下方节点初版会被裁切，已调整投影比例；详情面板可滚动，避免在主画布下方留下过大空白。
- 内置浏览器工具启动失败（Node runtime 路径不存在），实际验收改为现有 Chrome 的独立临时 headless profile。

## 变更隔离

只增加 `deploy/memory_observatory/`、两份新测试和根 README 入口。
不改 B站 proposal / Executor / transport / life / budget / agent_budget / workshop / methods / proxy / phone_video / media / login / persona。
没有读取真实 cookies，没有访问真实 B站写接口，没有修改线上服务、真实数据库或 control.json。

预存在的 `deploy/phone_mcp/start_phone_service.ps1` 保留，不纳入提交。
本轮开始与完成前核对的 SHA256：

```text
A0EDA8C69BD8BC23326142051C2B182DEB782F2B997B6B11228FCF0DE7995BB4
```

只做本地提交，不 push、不部署、不合并 main。

## 后续接入前的必要验证

1. 取得实际安装版本的只读、脱敏导出样本，并确认每条记忆的 scope 和来源语义。
2. 针对该版本编写协议适配器，禁止缺失作用域时默认并入主人私聊。
3. 将真实召回事件与演示线索检索分开显示；观测查询不得触发引擎访问强化。
4. 新策略只在离线副本中对比；引擎与观测台不同时运行生命周期调度。
5. 在授权与备份具备后再做真实写回与恢复测试。当前没有任何写回入口。
