# 核心补丁（随仓库提供）

这些文件是 **AstrBot v4.29.0** 核心源码的修改版（TTS 补丁见
`docs/踩坑合集.md` #15），以修改版文件的形式在 compose 里 bind-mount 进容器，
升级镜像不丢补丁。

- `dashscope_tts.py`：qwen-audio → WebSocket 通道；按模型能力接
  instruction/speech_rate/pitch_rate；超时、音频头校验与文字降级
- `stage.py`：整条回复只合成一次语音；显示气泡照常分；TTS 合成前清理表情标记
- `respond_stage.py`：按语义气泡发送（表情并回句子），首条不等待；被新消息打断时停止剩余气泡
- `chat_layout.py`：上面的分泡 / 朗读文本 / 音频校验纯函数（两个 stage 共用）

⚠️ **升级 AstrBot 版本时**，请在新版对应文件上**重新打补丁**，不要直接沿用本目录文件
（版本不匹配可能引入行为差异）。仓库钉的已验证组合见 `../Dockerfile` 与 `../compose.yaml`。

## tts_emotion_router 插件补丁

`tts_emotion_router_native_speak.diff` 把社区插件 `astrbot_plugin_tts_emotion_router`
的显式语音工具 `tts_speak` 改为调用 **AstrBot 原生 TTS Provider**
（当前为 `dashscope_tts` + `qwen-audio-3.1-tts-plus` + 复刻音色），不再使用插件内置的
SiliconFlow / MiniMax / MiMo 音色。插件的自动语音开关保持关闭，避免和原生 TTS 抢。

升级 `astrbot_plugin_tts_emotion_router` 后需要重打；补丁只改 `tts_speak` 的实现，
不改工具名，人格里仍写 `tts_speak`。

## proactive_chat 插件补丁

`proactive_chat/` 是社区插件 `astrbot_plugin_proactive_chat` v1.2.6 的两个修改版文件，
补上“主动消息也能联网搜索”的工具挂载与工具循环。安装/升级注意事项见
[`proactive_chat/README.md`](proactive_chat/README.md)。

来源：AstrBot（AGPL-3.0）https://github.com/AstrBotDevs/AstrBot
本目录仅包含修改后的文件，按 AGPL-3.0 随本项目一同分发。
