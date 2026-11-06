# 房间项目书架 v0.1

2026-11-02，本地页面接入；未部署或开启机器人配置。承接[跨日自主项目](../bili_activity/PROJECTS_V01.md)，将已有项目、阶段作品与完成后的回看放到房间书架。

## 入口与呈现

进入 `/room`，仍先显示总览。点击「书架」，右侧手记出现项目索引，房间下方展开「她的项目册」；点击索引可直接走到对应项目册。保留奶白、浅蓝、纸页和淡金装饰，角色阅读、镜头和八帧动画沿用原实现。

- 每本项目册显示主题、记录状态、已建阶段数/上限；翻开后看到当时的理由与阶段时间线。
- 阶段可用鼠标或键盘展开：下一步、作品标题与版本、正文摘页、来源可用情况、回看的收获/局限/新问题。
- 收尾与待做分开呈现；项目完成后留下的问题不代表会自动继续。阶段完成但尚无回看时明确提示。
- 同步近况、切换房间角落或项目时保留所选项目及章节开合；快速切换会在重新渲染前捕获原生 details 状态，避免等待 toggle 事件造成丢失。
- 390px 窄屏使用双册封面和单列正文；减少动画时不增加卡片移动。原单件作品列表保留。

所有正文通过 `textContent` 展示，不执行HTML、Markdown内嵌指令或链接动作。页面只有查看、翻页与原有只读同步，无创建目标、推进阶段、审批、发布或发送按钮。

[桌面虚构示例](assets/room-projects-v01.png) · [窄屏虚构示例](assets/room-projects-mobile-v01.png)

## 数据和边界

`activity_reader.py` 在原只读事务内增加可选 `projects` 投影，不导入 Store/Projects/Workshop，不迁移、恢复、校验写缓存或更改运行状态。SQLite 继续使用 `mode=ro`、`query_only`、authorizer 与执行时限；本地及固定 SSH 读取器共用同一实现。

| 情况 | 页面行为 |
| --- | --- |
| 原库尚无项目表 | 空项目册，保留已有目标、作品和近况；不补演示数据 |
| 表不完整、未来版本、列不兼容 | 项目暂不可读，已有房间数据仍可查看 |
| 项目功能关闭但有历史 | 显示关闭说明，保留历史，不恢复活动 |
| 未落实的下一阶段预留 | 显示待恢复，不冒充完成或生成作品 |
| 回看版本/哈希/来源列表与当前保存记录不一致 | 显示历史回看需重新核对 |
| 来源缺失、跨会话或已被修订 | 标注来源不可用、回看需核对 |
| 回看错误地关联到另一件作品 | 隐藏该回看文字，不沿错误关联取正文 |
| 同步失败或超过三分钟 | 沿用旧快照提示；项目区也明确只供回看 |
| 真正的演示模式 | 两个显式虚构项目，仅供界面演示，不写入任何账本 |

仅从已配置主人私聊读取；项目→阶段→目标→作品→版本按同一 scope 关联。输出使用字段白名单，不附带配置、来源键、正文哈希、原始响应、会话标识、QQ回执或数据库路径。作品正文属于该会话已有内容，不是从模型或其他会话补写。

每次最多4个项目（活动/暂停优先），每个最多6阶段。作品正文前600字、收获前360字、局限前240字，长文本标明节选；不加载来源正文。报告项目总数与截断，最坏长度 fixture 在原SSH 256 KiB上限内。

`sources_available` 只核对同会话的 read/video/video_note 来源行仍存在且未被替代。`recorded` 表示回看对应当前保存的版本/哈希/来源列表且来源仍可找到；**不是重跑 Workshop 内容检查，不是事实验证，不证明已发布或QQ送达**。页面对此有明确文字说明。

`project_review` 生活选择现在能显示为「项目回看」、归于书架；运行结束仍不等同于目标或项目完成。

继续使用原 `/api/room` 与 `/api/room/refresh`，没有新的数据读取端点。LivingMemory 星海原有60秒同步保持不变；房间新增可关闭的60秒只读自动同步（默认开启，页面隐藏时暂停），复用 `/api/room/refresh` 读取活动账本，手动「同步近况」仍保留。后续同步收尾及34项浏览器结果见 [ROOM_SYNC_V01.md](ROOM_SYNC_V01.md)；下表27项是书架首版的历史验收，不包含后续新增同步用例。

## 验证

基线：分支 `codex/life-decision-v01`，HEAD `0246e98`，工作区干净。本轮没有改机器人插件、人格、账号配置、动画素材、星图布局或既有记忆策略；没有真实B站、QQ、模型或SSH源请求。

```powershell
$testPython='C:\Users\ACER\AppData\Local\Temp\dafeiyu-rhythm-venv\Scripts\python.exe'
& $testPython -B -m unittest discover -s tests -p 'test_memory_projects.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_memory_*.py' -v
& $testPython -B -m unittest discover -s tests -p 'test_memory_room.py' -v
wsl.exe --distribution Ubuntu-22.04 --cd /mnt/d/GitHub/dafeiyu-astrbot -- python3 -B -m unittest discover -s tests -p 'test_memory_*.py' -v
$env:MEMORY_PYTHON=$testPython
node tests/memory_observatory_browser.mjs --spawn-demo
git diff --check
```

| 检查 | 实际结果 |
| --- | --- |
| Windows 修改前基线 | 85项，11.131秒，OK，无skip |
| 新增只读项目测试 | 13项，1.608秒，OK |
| Windows观测台全量 | 98项，12.327秒，OK，无skip |
| 补充静态资源/旧快照断言后，房间定向 | 25项，6.246秒，OK |
| WSL Ubuntu 22.04观测台全量 | 98项，11.792秒，OK，1 skip（Windows命名互斥锁） |
| Chrome演示浏览器验收 | 27项PASS，0失败、0脚本异常；含原星海、镜头、望窗/睡姿八帧、60秒同步测试 |
| 内嵌浏览器人工检查 | 翻册、回车收起章节与视觉排版通过；只用虚构数据 |
| 差异检查 | `git diff --check`通过；插件、人格、手机启动脚本及动画文件零改动 |

首轮浏览器验收有4项失败：新增键盘用例未产生完整回车文本事件，以及新增重载用例没有恢复原动画测试的暂停初始状态（影响后续3项）。第二轮动画通过、回车用例仍失败；用实际页面确认回车可操作后补齐自动测试事件，第三轮27项全通过。另在检查中补上重绘前保存 details 状态，防止快速操作落在异步 toggle 回调之前。没有放宽原动画断言。

最终[浏览器结果](assets/room-projects-v01-results.json)及上方截图均为虚构数据。原始单测日志位于本机临时目录 `dafeiyu-portfolio-baseline.log`、`dafeiyu-portfolio-final.log`、`dafeiyu-portfolio-wsl.log`。本阶段未改B站执行代码，没有重复运行 `test_bili_*.py`；不将前阶段结果当作本轮执行结果。

## 使用、回退与仍未验证

文件升级后需要重启本机观测服务；原数据源配置不变。真实库没有项目时显示空册。不要为填充页面开启真实自主项目或导入虚构记录。本轮没有重启用户原服务、没有改桌面快捷方式，也没有部署或push。

手动验收时，主人可在已授权的真实只读观测台里点击「同步近况」→「书架」，核对项目标题、阶段和版本与机器人 `df_project read` 一致；关闭项目功能仍应看得到历史。来源修订和陌生记录建议继续用隔离测试库验证，不改真实库造数据。

真实项目库/SSH/LivingMemory联动、长时间真实运行、Safari/Firefox及低端设备性能仍 **UNVERIFIED**。本轮的接口契约使用真实生产者结构构造的临时库验证，未宣称线上已有项目。

回退只需停止本机观测服务，恢复本轮页面和只读适配器文件后重新启动；没有数据库迁移或真实数据需要回滚。
