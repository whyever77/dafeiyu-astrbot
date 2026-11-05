# 房间近况自动同步 v0.1

2026-11-06，进化清单第3、7项的收尾。本地代码与离线浏览器验收完成；未连接真实账本、SSH、模型或QQ，未部署。

## 页面行为

房间的安排、作品、足迹和项目册共用原来的只读活动快照。连接活动账本后，默认每60秒尝试同步一次；右上方「自动同步近况」可以关闭，偏好保存在本浏览器中。手动「同步近况」仍可使用，且与自动同步共用一个在途请求。

- 页面隐藏时取消下一次自动读取。返回页面后，若已到期只读一次；短时间反复切标签不会重复读取。
- 首屏只取已有缓存。演示和未配置活动来源的页面不轮询；服务端明确报告已配置但暂不可用时，允许自动恢复。首个页面请求本身失败、尚不能确认来源时，需要手动重试。
- 同步失败保留原记录与原快照时间，标明「旧快照 · 当前状态未知」。不会以空白或虚构经历替代失败，也不会每分钟弹出错误遮住阅读。
- 更新不重置房间区域、镜头和动画；保留项目选择、章节开合和键盘焦点。相同内容只更新快照时间，保留原文字节点及选区。
- 桌面手记列表按记录ID保留正在读的条目和偏移；手机长列表随整页滚动时也保留条目位置。原条目消失时保留可用滚动位置，不重新生成记录。

房间与记忆星海的自动同步开关各自独立。LivingMemory已有读取器、星图布局、动画素材、机器人调度/审批/预算和记忆写入策略没有改动。只使用 `GET /api/room` 和 `POST /api/room/refresh {}`；后者在服务端只执行原活动来源的读取，并非机器人写操作。

## 本次修正

恢复工作时，分支为 `codex/life-decision-v01`，HEAD `2e5dcee`，工作区干净；上轮同步代码与31项浏览器检查已经包含在该提交中。本次在此基础上收尾，不回退已有提交。

| 漏项 | 复现 | 修正 |
| --- | --- | --- |
| 手记内层读位回到顶部 | 窗边长列表向下滚动，新记录到来后重绘 | 为已有记录附上ID，保存可视条目及偏移；桌面内层列表和手机整页分别恢复 |
| 定时器轻微迟到后漏掉一轮 | 前次在100000ms读取，下次回调在159997ms；固定interval跳过后等到下一分钟 | 按上次尝试时间安排单个timeout；提前回调只补剩余3ms，不丢掉整轮 |

## 验证记录

所有数据均为虚构快照或临时数据库。浏览器通过CDP拦截活动接口，不请求真实源；受控时钟用来覆盖60秒边界，未声称完成数日在线观察。

```powershell
python -B -m unittest discover -s tests -p 'test_memory_*.py' -v
node tests/memory_observatory_browser.mjs --spawn-demo
node --check deploy/memory_observatory/static/room.js
node --check tests/memory_observatory_browser.mjs
git diff --check
```

| 检查 | 实际结果 |
| --- | --- |
| Windows Python 3.14.7 观测台回归 | 98项，12.194秒，OK，0 skip |
| WSL | UNVERIFIED：发行版查询受权限限制，测试未启动，无项数/skip结果 |
| 原31项浏览器检查 | 31 PASS，0失败、0脚本异常 |
| 补充复现、修复前 | 31 PASS，2 FAIL；分别命中列表读位和剩余3ms断言 |
| 最终 Chrome / Node 24.21.0 | 34 PASS，0失败、0脚本异常；包括手机读位、键盘焦点、隐藏页/手动模式、失败恢复、原星图及角色八帧 |
| JS语法与差异空白检查 | 通过 |

红阶段失败断言：`New records must not displace the journal entry being read`；`An early tick must reschedule the remaining 3ms, not skip a whole minute`（`undefined !== 3`）。修复后同一断言均通过，未放宽既有断言。

Windows首轮在沙箱内因 `WinError 10013` 的本机套接字限制和临时目录权限失败；获准在沙箱外复跑后取得上表结果。测试使用系统标准库，无新增依赖。

WSL实际尝试 `wsl.exe --list --quiet` 返回 `Wsl/E_ACCESSDENIED`；升级查询的审批等待被中断，没有取得发行版列表或启动测试。没有安装、启动或更改WSL实例；不引用项目书架阶段的WSL成绩作为本轮结果。

最终[浏览器结果](assets/room-sync-v01-results.json)。本机原始输出：`%TEMP%\dafeiyu-room-sync-windows-20261105.log`；红阶段 `%TEMP%\memory-observatory-browser-aKNLCE\results.json`，最终 `%TEMP%\memory-observatory-browser-v9FiEO\results.json`，同目录包含桌面/手机截图。

## 使用与下一项

刷新本机房间页面即可加载新脚本；服务端接口没有改变。可先在演示模式检查界面，再在主人已授权的只读活动来源中查看真实近况。关闭「自动同步近况」即可恢复手动读取；无需回滚或迁移数据库。

本轮WSL、真实SSH/活动账本连续同步、真实项目更新、LivingMemory联动、真实模型与QQ、Safari/Firefox及长时间运行仍为 **UNVERIFIED**。未改真实配置、未启动机器人活动、未push或部署。

下一项依照[隔离模型与记忆计划](../bili_activity/PILOT_MEMORY_V01.md)：在独立实例中确认模型与嵌入配置就绪，再验证指定隔离会话的记忆写入、召回和跨日项目。房间界面通过不代表这些真实链路已通过。
