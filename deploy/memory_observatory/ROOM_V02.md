# 她的小小房间 v0.2 · 大肥鱼与记忆分区

2026-10-30，基于 `0b3a3e3`，本地实现，未部署。

## 实现

- 使用主人确认的 [尾巴修订三视图](../../assets/character/v1-20261030/dafeiyu-turnaround-tail-v3.png)。奶白、浅蓝、蓝紫与暖金贯穿房间和记忆星海。
- `/room` 默认总览。大肥鱼在地毯空位待机、眨眼；切换书桌/书架/窗边/休息角时，镜头连续平移与缩放，角色走过去后播放写字、阅读、望窗、睡觉。快速切换以最后一次选择为准。
- 六组透明精灵图，每组 4 × 2、8 格。图集重排为 444px 整数格 + 2px 透明隔断（1786×894，工具 `tools/normalize_character_atlas.py`），Canvas 源矩形均为整数，避免相邻帧采样渗色。Canvas 按格播放，SVG 绘制房间。桌椅与角色的写字动作一起显示；移动时显示独立空桌。不是 Live2D，也没有完整起身/落座骨骼动画。
- 窗景每 30 秒核对 Asia/Shanghai 时间，分晨光、白天、晚霞、夜间；支持暂停动画、系统减少动态效果、窄屏。
- 记忆按既有类型和形状分区，位置不代表语义距离。选中的记忆与直接相关连线、节点优先显示，其他关系减淡；筛选和会话隔离沿用原逻辑。
- 遗忘模拟的亮度连续插值。每 60 秒自动同步沿用原计时器：可关闭、隐藏页暂停、单飞。同步后只按当下作用域和实验参数重新投影，避免先闪回旧作用域或快照当日。
- 活动动画是浏览场景的表演；右侧文字才是账本证据。没有从点击动作生成真实作品、安排或经历。

## 数据接入

```powershell
python -B -m deploy.memory_observatory.server --port 8767 --livingmemory-ssh --life-ssh
```

访问 `/room` 和 `/`。已运行的旧进程需要先正常停止，再以新版本启动；不会替换或终止其他进程。
现有桌面入口默认带 `--life-ssh`，双击即同时连接记忆与活动账本；已有真实服务在运行时直接复用。
若旧进程没有活动源，先正常停止一次再双击。

真实 LivingMemory 保持固定 SSH 读取器、只读 SQLite SELECT 和本机内存快照；未变更访问计数、强化、真实遗忘或 QQ 行为。
活动账本有独立的「同步近况」按钮；缓存超过三分钟会显示旧快照，不冒充实时状态。它不跟随记忆的 60 秒刷新。
启动时不指定真实来源则展示明确标记的虚构样例；真实来源失败不会变成演示。

## 实测证据

| 验证 | 结果 |
| --- | --- |
| Windows：`C:\Users\ACER\miniconda3\python.exe -B -m unittest discover -s tests -p "test_memory_*.py" -v` | 81 项，11.9 秒，OK，0 skip |
| WSL：`wsl.exe -e bash -lc 'cd /mnt/d/GitHub/dafeiyu-astrbot && python3 -B -m unittest discover -s tests -p "test_memory_*.py" -v'` | 81 项，11.5 秒，OK，1 skip（Windows 命名互斥锁） |
| `node tests/memory_observatory_browser.mjs --spawn-demo` | 18 项 PASS；只用虚构数据，测试进程结束即关闭 |
| `node tests/memory_observatory_browser.mjs --live-readonly --live-activity` | 7 项 PASS；实际连接 LivingMemory 和活动账本，不保存正文或截图 |
| JS 语法检查、`git diff --check` | 通过 |

新增/扩展检查覆盖：6 个素材的明确静态白名单、路径越界拒绝、类型布局、选中关联、遗忘过程中画布连续变化、镜头中间位置、快速导航、动作换帧、昼夜、移动端、减少动画，以及 60 秒回调的状态保持/隐藏暂停/关闭暂停/慢同步期间切换作用域。
自动同步的边界由模拟来源与调用原计时器回调验证，没有把模拟结果说成真实时间流逝验收。

过程中修复了画布新增 `data-zone` 后被导航选择器误选的问题；首轮浏览器失败及修正后的全部通过均保留在临时测试输出。
最初受限环境的本机套接字/临时 SQLite 访问失败，允许正常本地执行后离线测试通过。
真实活动首次浏览器断言遇到三分钟旧快照；改为先显式只读刷新，再断言连接就绪，7 项通过，未放宽来源断言。

截图仅使用虚构数据。真实联调结果 JSON 只记录本机地址、检查名称与通过结果，没有保存记忆、活动正文、主机地址或凭据。

[房间总览](assets/room-v02-overview.png) · [书桌特写](assets/room-v02-desk.png) · [记忆分区](assets/room-v02-atlas.png)

[18 项演示验收结果](assets/room-v02-browser-results.json) · [7 项真实只读验收结果](assets/room-v02-live-results.json)

## 审查修复（2026-10-30）

- 活动快照的“超过三分钟即旧”改按本机单调时钟的加载时长计算（服务端 `age_seconds` + 页面接收后的本地时长），不再拿远端 `as_of` 对浏览器时钟。
- 六组精灵图由 1774×887 的 443.5px 网格重排为 1786×894（444px 整数格 + 2px 透明隔断），Canvas 源矩形全为整数。
- `graph.js` 的数字三元改为显式 `cond ? a : b`，Node 24 可直接解析。
- 被拒绝的 POST 先排空请求体，消除 Windows 下偶发 `ConnectionAbortedError` 的测试 flake。
- 本目录 README 顶部介绍对齐 v0.2；新增远端时钟漂移与图集尺寸的回归测试。

## 素材与边界

角色参考作者 **YunYueSama**：[上游仓库](https://github.com/YunYueSama/codex-deepseek-pet)。
保留 [许可](static/character/LICENSE.txt)、[署名与修改说明](static/character/credits.txt)、[生成提示词](../../assets/character/v1-20261030/ANIMATION-PROMPTS.json)。
六组动作基于已确认形象重新生成；不是上游作者的新交付，不代表 DeepSeek 官方认可。

仍未验证：Safari/Firefox、长时运行和低端设备的帧率、逐格美术一致性之外的连续骨骼运动、实际生活策略或 QQ 行为。
真实数据只读接通不等于拟人记忆策略已经上线。原有 B站、审批、Executor、transport、预算、人格和手机脚本均未修改。
回滚只需停止本机服务并恢复页面/素材文件；无需回滚真实库或云端配置。没有 push 或部署。
