#!/usr/bin/env bash
# 大肥鱼一键引导（Linux / macOS / Windows Git-Bash 通用）
#
#   bash deploy-kit/bootstrap.sh                  # 就在本仓库里跑：装依赖 + 组装 payload + 自检
#   bash deploy-kit/bootstrap.sh ~/bots/dafeiyu   # 在仓库外跑：先克隆到该目录，再引导
#
# 它只做四件可验证的事：
#   1. 准备好代码（本仓库就地用；仓库外则克隆上游）
#   2. 建虚拟环境、装插件依赖
#   3. 把插件/人格组装到 payload/（等拷进 AstrBot 数据目录）
#   4. 跑离线回归测试 + 前置检查
#
# 它不会、也无法替你做的事：装 AstrBot / NapCat、扫码登录 QQ、填 API 密钥。
set -euo pipefail

UPSTREAM="${UPSTREAM:-https://github.com/whyever77/dafeiyu-astrbot.git}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m错误:\033[0m %s\n' "$*" >&2; exit 1; }
# Windows 原生程序（uv 等）看不懂 /c/... 这种 MSYS 路径，转一下
wpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
REQ="$(wpath "$ROOT/deploy-kit/requirements-plugin.txt")"

# --- 1. 代码：就地用 vs 克隆 --------------------------------------------------
if [ -z "${1:-}" ]; then
  # 不给参数 = 就地引导（最常见：你已经在仓库里）
  [ -d "$ROOT/plugin" ] || die "这里不像项目仓库，请给一个安装目录：bash deploy-kit/bootstrap.sh ~/bots/dafeiyu"
  SRC="$ROOT"
  say "就地引导: $SRC"
else
  # 给了目录 = 先克隆到那儿，再引导
  TARGET_ARG="$1"
  BASE="$(basename "$TARGET_ARG")"
  mkdir -p "$(dirname "$TARGET_ARG")"
  cd "$(dirname "$TARGET_ARG")"
  if [ -d "$BASE/.git" ]; then
    say "更新已有仓库: $BASE"
    git -C "$BASE" pull --ff-only
  else
    say "克隆上游: $UPSTREAM"
    git clone --depth 1 "$UPSTREAM" "$BASE"
  fi
  SRC="$(pwd)/$BASE"
  say "工作目录: $SRC"
fi

# --- 2. 虚拟环境 + 依赖（不 activate，直接调 venv 里的 python）-----------------
VPY=""
VENV_W="$(wpath "$SRC/.venv")"
if command -v uv >/dev/null 2>&1; then
  say "用 uv 建虚拟环境"
  uv venv --quiet "$VENV_W" ${PYVER:+--python "$PYVER"} || true
fi
if [ -x "$SRC/.venv/bin/python" ]; then
  VPY="$SRC/.venv/bin/python"
elif [ -x "$SRC/.venv/Scripts/python.exe" ]; then
  VPY="$SRC/.venv/Scripts/python.exe"
else
  PY="${PYTHON:-python3}"
  command -v "$PY" >/dev/null 2>&1 || die "找不到 $PY，请先装 Python 3.10+"
  "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' \
    || die "Python 版本太旧，需要 3.10+"
  say "用 $PY 建虚拟环境"
  "$PY" -m venv "$VENV_W" || die "venv 创建失败：Debian/Ubuntu 请先 apt install python3-venv"
  if [ -x "$SRC/.venv/bin/python" ]; then
    VPY="$SRC/.venv/bin/python"
  elif [ -x "$SRC/.venv/Scripts/python.exe" ]; then
    VPY="$SRC/.venv/Scripts/python.exe"
  fi
fi
[ -n "$VPY" ] || die "没能定位虚拟环境里的 python"

say "安装插件依赖"
if "$VPY" -m pip --version >/dev/null 2>&1; then
  "$VPY" -m pip install -q --upgrade pip
  "$VPY" -m pip install -q -r "$REQ"
elif command -v uv >/dev/null 2>&1; then
  # uv 建的 venv 默认不带 pip —— 直接用 uv 装
  uv pip install --python "$(wpath "$VPY")" -q -r "$REQ"
else
  "$VPY" -m ensurepip --upgrade >/dev/null 2>&1 || die "这个 Python 没有 pip，装不上依赖"
  "$VPY" -m pip install -q -r "$REQ"
fi

# --- 3. 组装 payload ---------------------------------------------------------
rm -rf "$SRC/payload"
mkdir -p "$SRC/payload/plugins" "$SRC/payload/persona"
cp -r "$SRC"/plugin/* "$SRC/payload/plugins/"
cp "$SRC"/persona/*.md "$SRC/payload/persona/"
say "已组装: $SRC/payload"
say "  plugins/ → 拷进 AstrBot 数据目录的 data/plugins/"
say "  persona/ → 内容粘进 AstrBot 面板「人格」"

# --- 4. 自检 -----------------------------------------------------------------
say "跑离线回归测试（不花钱、不连 QQ）"
( cd "$SRC" && "$VPY" tests/test_face_rules.py && "$VPY" tests/test_schedule_awareness.py )
say "第三个测试需要 ffmpeg，可选：$VPY tests/test_video_processing.py"

say "跑前置检查"
"$VPY" "$(wpath "$ROOT/deploy-kit/preflight.py")" || true

cat <<'EOF'

=== 接下来只剩三件手工活 ===
 1. 装 AstrBot（Launcher 版）+ NapCat（Shell.Node 版），NapCat 用小号扫码
 2. AstrBot 里加 ws://127.0.0.1:6199/ws，配 DeepSeek / 百炼 提供商
 3. 把 payload/plugins/* 拷进 AstrBot 的 data/plugins/，重启

详见 QUICKSTART.md 与 README.md。
EOF
