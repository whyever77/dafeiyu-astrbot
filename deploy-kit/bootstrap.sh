#!/usr/bin/env bash
# 大肥鱼一键引导（Linux / macOS / Windows Git-Bash 通用）
#
#   bash deploy-kit/bootstrap.sh                    # 就地引导：装依赖 + 组装 payload + 离线测试 + 体检
#   bash deploy-kit/bootstrap.sh --with-bili        # 连 B 站自主活动插件（Pillow/mcp）一起装并组装
#   bash deploy-kit/bootstrap.sh --offline          # 跳过联网 pip 安装，只用当前 Python 组装 + 跑标准库测试
#   bash deploy-kit/bootstrap.sh ~/bots/dafeiyu     # 在仓库外跑：先克隆到该目录，再引导
#
# 镜像加速（国内网络默认自动开启，也可手动指定）：
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash deploy-kit/bootstrap.sh
#
# 它不会、也无法替你做的事：装 AstrBot / NapCat、扫码登录 QQ、填 API 密钥。
set -euo pipefail

UPSTREAM="${UPSTREAM:-https://github.com/whyever77/dafeiyu-astrbot.git}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WITH_BILI=0
OFFLINE=0
TARGET_ARG=""
for arg in "$@"; do
  case "$arg" in
    --with-bili) WITH_BILI=1 ;;
    --offline|--no-deps) OFFLINE=1 ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) TARGET_ARG="$arg" ;;
  esac
done

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m错误:\033[0m %s\n' "$*" >&2; exit 1; }
wpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# --- 1. 代码：就地用 vs 克隆 --------------------------------------------------
if [ -z "$TARGET_ARG" ]; then
  [ -d "$ROOT/plugin" ] || die "这里不像项目仓库，请给一个安装目录：bash deploy-kit/bootstrap.sh ~/bots/dafeiyu"
  SRC="$ROOT"
  say "就地引导: $SRC"
else
  BASE="$(basename "$TARGET_ARG")"
  PARENT="$(dirname "$TARGET_ARG")"
  mkdir -p "$PARENT"
  PARENT_ABS="$(cd "$PARENT" && pwd)"
  if [ -d "$PARENT_ABS/$BASE/.git" ]; then
    say "更新已有仓库: $PARENT_ABS/$BASE"
    git -C "$PARENT_ABS/$BASE" pull --ff-only
  else
    say "克隆上游: $UPSTREAM -> $PARENT_ABS/$BASE"
    git clone --depth 1 "$UPSTREAM" "$PARENT_ABS/$BASE"
  fi
  SRC="$PARENT_ABS/$BASE"
  say "工作目录: $SRC"
fi

REQ="$(wpath "$SRC/deploy-kit/requirements-plugin.txt")"
BILI_REQ="$(wpath "$SRC/plugin/astrbot_plugin_bili_activity/requirements.txt")"
PREFLIGHT="$(wpath "$SRC/deploy-kit/preflight.py")"

# --- 2. 虚拟环境 + 依赖（不 activate，直接调 venv 里的 python）-----------------
VPY=""
VENV_W="$(wpath "$SRC/.venv")"
if [ "$OFFLINE" -eq 0 ] && command -v uv >/dev/null 2>&1; then
  say "用 uv 建虚拟环境"
  uv venv --quiet "$VENV_W" ${PYVER:+--python "$PYVER"} || true
fi
if [ -x "$SRC/.venv/bin/python" ]; then
  VPY="$SRC/.venv/bin/python"
elif [ -x "$SRC/.venv/Scripts/python.exe" ]; then
  VPY="$SRC/.venv/Scripts/python.exe"
else
  PY="${PYTHON:-python3}"
  command -v "$PY" >/dev/null 2>&1 || PY="python"
  command -v "$PY" >/dev/null 2>&1 || die "找不到 python3/python，请先装 Python 3.10+"
  "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' \
    || die "Python 版本太旧，需要 3.10+"
  if [ "$OFFLINE" -eq 1 ]; then
    VPY="$(command -v "$PY")"
    say "离线模式：直接使用 $VPY"
  else
    say "用 $PY 建虚拟环境"
    "$PY" -m venv "$VENV_W" || die "venv 创建失败：Debian/Ubuntu 请先 apt install python3-venv"
    if [ -x "$SRC/.venv/bin/python" ]; then
      VPY="$SRC/.venv/bin/python"
    elif [ -x "$SRC/.venv/Scripts/python.exe" ]; then
      VPY="$SRC/.venv/Scripts/python.exe"
    fi
  fi
fi
[ -n "$VPY" ] || die "没能定位可用的 python"

install_req() {
  local req_file="$1"
  if "$VPY" -m pip --version >/dev/null 2>&1; then
    "$VPY" -m pip install -q "$req_file" -r "$req_file" 2>/dev/null \
      || "$VPY" -m pip install -q -r "$req_file" \
      || "$VPY" -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$req_file"
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "$(wpath "$VPY")" -q -r "$req_file" \
      || uv pip install --python "$(wpath "$VPY")" -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$req_file"
  else
    "$VPY" -m ensurepip --upgrade >/dev/null 2>&1 || die "这个 Python 没有 pip，装不上依赖"
    "$VPY" -m pip install -q -r "$req_file"
  fi
}

if [ "$OFFLINE" -eq 0 ]; then
  say "安装核心插件依赖（dashscope / imageio-ffmpeg / aiohttp）"
  install_req "$REQ"
  if [ "$WITH_BILI" -eq 1 ] && [ -f "$SRC/plugin/astrbot_plugin_bili_activity/requirements.txt" ]; then
    say "安装 B 站自主活动插件依赖（Pillow / mcp）"
    install_req "$BILI_REQ"
  fi
else
  say "已跳过联网装包（--offline）"
fi

# --- 3. 组装 payload + 前置检查 -----------------------------------------------
BILI_FLAG=()
[ "$WITH_BILI" -eq 1 ] && BILI_FLAG=(--with-bili)

say "干净组装 payload（排除 __pycache__ / 旧本地配置，生成纯正文人格提示词）"
"$VPY" "$PREFLIGHT" --stage-payload "$(wpath "$SRC/payload")" "${BILI_FLAG[@]}" || true

# --- 4. 离线回归测试 ----------------------------------------------------------
say "跑核心离线回归测试（不花钱、不连 QQ）"
(
  cd "$SRC"
  "$VPY" tests/test_face_rules.py
  "$VPY" tests/test_schedule_awareness.py
  "$VPY" tests/test_heal_ws.py
  "$VPY" tests/test_chat_layout.py
  "$VPY" tests/test_deploy_kit.py
)

cat <<'EOF'

=== 接下来只剩三件手工活 ===
 1. 装 AstrBot（Launcher 版）+ NapCat（Shell.Node 版，双击根目录 napcat.bat），小号扫码
 2. NapCat 加客户端 ws://127.0.0.1:6199/ws；AstrBot 配 DeepSeek / 百炼提供商，并在该环境装好上面的依赖
 3. 把 payload/plugins/* 拷进 AstrBot 的 data/plugins/；把 payload/persona/大肥鱼-system-prompt.txt 整段粘进「人格」

全部启动后可运行：python deploy-kit/preflight.py --stage post --env .env 做最终复查。
详见 QUICKSTART.md 与 README.md。
EOF
