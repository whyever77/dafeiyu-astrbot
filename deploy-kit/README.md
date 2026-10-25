# deploy-kit · 部署辅助包

三个脚本，把「拉取 → 装依赖 → 组装 → 自检」这段重活包了。

| 文件 | 干什么 |
|---|---|
| `preflight.py` | 体检：Python 版本、git/ffmpeg、6199/6185/6099 端口、依赖包、`.env` 密钥。不改动任何东西。 |
| `bootstrap.sh` | 一键：建 venv → 装依赖 → 组装 `payload/` → 跑离线测试 → 体检。仓库外运行时会先克隆。 |
| `bootstrap.ps1` | 同上，Windows PowerShell 版。 |
| `requirements-plugin.txt` | 插件依赖：`dashscope`、`imageio-ffmpeg`、`aiohttp`。 |
| `env.example` | 密钥模板，复制成 `.env` 再填。 |

```bash
python deploy-kit/preflight.py            # 体检
bash deploy-kit/bootstrap.sh              # 一键（Git-Bash / Linux / macOS）
powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1   # 一键（Windows）
python deploy-kit/preflight.py --env .env # 复查密钥
```

## 它们不做的事（也做不到）

- 装 AstrBot / NapCat —— 都是图形化安装程序 + 扫码登录
- 填 API 密钥 —— 密钥不该经过任何脚本
- 改你的 AstrBot 配置 —— 组装好的 `payload/` 要你自己拷进去

## 设计上的三个约定

1. **不 activate 虚拟环境**：直接调 venv 里的 python，避免 `activate` 在 Git-Bash / PowerShell 下行为不一致。
2. **装依赖优先用 `uv pip`**：`uv venv` 建的 venv 不带 pip，这是最常见的坑。
3. **Windows 路径转换**：Git-Bash 的 `/c/...` 传给 `uv` 会找不到文件，脚本里用 `cygpath -w` 转了一道。

## 自检为什么可信

`bootstrap.sh` 最后会跑仓库自带的离线测试（`tests/test_face_rules.py`、
`tests/test_schedule_awareness.py`），它们不回连 QQ、不调用付费模型、不需要密钥。
所以「全部通过」是真的说：这套代码在你机器上能跑，而不是脚本自己说成功。
