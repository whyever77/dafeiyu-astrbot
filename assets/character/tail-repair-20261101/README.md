# 望窗八帧回填与睡姿尾根修订

2026-11-01，内置 image_gen 编辑，无 CLI/API 密钥调用。

- `window-occlusion-v1.png`：主人已确认的望窗基准。裙子与蕾丝在前，尾根及近身上段被裙子遮住，只在后侧裙摆下方露出。
- `window-eight-frames-v2.png`：根据已确认单帧生成的八帧原稿，1774×887 RGBA，4×2；轻微呼吸、抬眼、眨眼与发梢变化。
- 望窗已回填至 [页面图集](../../../deploy/memory_observatory/static/character/window.png)。仅对新原稿调用既有 `normalize_character_atlas.rebuild` 做整数格打包：1786×894、444px 单元、2px 隔断。未对其他图集重复处理。绘制时补偿新图第423行的足底基线，保持角色落地。
- `rest-tail-base-v1.png`：上一轮睡姿候选，主人反馈尾根应该更多藏在裙子后面。
- `rest-occlusion-v2.png`：延长裙摆遮住尾根的中间稿。主人进一步纠正：尾巴应从裙子里面钻出；此稿不作为后续动画基准。
- `rest-under-hem-v3.png`：主人确认继续制作动画的睡姿基准。恢复短裙，尾根藏在裙内，圆润尾柄从后侧蕾丝裙摆下方伸出，经腿后连续弯向右下方尾鳍。[单帧提示词](PROMPT-rest-v3.json)。
- `rest-eight-frames-v4.png`：以该睡姿生成的八帧轻呼吸原稿，1774×887 RGBA。舍弃尾鳍越过分格边界的候选，仅采用边界检查通过的完整八帧。[内置 image_gen 提示词](PROMPT-rest-eight-v4.json)。
- 睡姿已回填至 [页面图集](../../../deploy/memory_observatory/static/character/rest.png)，调用既有打包器重排为 1786×894 的整数格。房间绘制按每帧可见范围对齐水平中心和坐垫基线，不改画作，避免原稿行间留白差异造成角色跳动；650ms/帧，完整循环5.2秒。暂停或减少动画时保持第0帧。
- [本轮提示词](PROMPTS-v2.json) · [上一轮提示词](PROMPTS.json)。角色参考与许可沿用 [既有说明](../v1-20261030/README.md)。

## 验证与边界

`python -B -m unittest discover -s tests -p "test_memory_room.py" -v`：Windows 23 项通过，6.161 秒，0 skip。

`node tests/memory_observatory_browser.mjs --spawn-demo`：24 项通过、0失败、0未捕获异常。睡姿检查实际收集0–7帧，确认8种画面、四周不裁切、基线和水平中心漂移不超过3个画布像素，并验证暂停后像素保持不变。保留望窗、昼夜、移动端、减少动画及原有星图/同步检查。语法检查和 `git diff --check` 通过。

首次受限运行被临时目录/本机连接权限及浏览器启动限制阻止；授权后用相同命令运行通过，未修改测试断言绕过环境限制。

截图和浏览器结果只用虚构数据：[望窗页面](window-in-room-v2.png) · [睡姿页面](rest-in-room-v4.png) · [最新检查结果](browser-results-v4.json)。

仅更新望窗/睡眠动画、相应绘制落位与浏览器检查；未改变房间布局、记忆星图或来源读取逻辑，未连接真实记忆、B站或云端。图片绘制使用内置 image_gen；现有打包工具只完成动画格排布，不重画尾巴。刷新房间页面即可加载新动画。
