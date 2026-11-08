#!/usr/bin/env bash
# 大肥鱼 · macOS 一键启动（Docker Desktop / OrbStack / colima）
#
#   ./start.sh              # 检查环境 → 建目录/.env → 构建镜像 → 起容器 → 等面板就绪
#   ./start.sh --no-build   # 不重建镜像，只起容器（日常重启用这个，快）
#   ./start.sh --pull       # 构建前先拉最新基础镜像（升级 AstrBot 时用）
#
# 它不会、也无法替你做的事：填 API 密钥、通过好友验证、选人格。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

BUILD=1
PULL=0
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    --pull) PULL=1 ;;
    -h|--help)
      sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf '\033[31m错误:\033[0m 未知参数 %s（--help 看用法）\n' "$arg" >&2
      exit 2
      ;;
  esac
done

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m错误:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. 环境检查 --------------------------------------------------------------
[ "$(uname -s)" = "Darwin" ] \
  || warn "本套件为 macOS 准备，当前 $(uname -s) 未验证；Linux 服务器请直接用 deploy/ 套件"

command -v docker >/dev/null 2>&1 \
  || die "没找到 docker。装 Docker Desktop（https://www.docker.com/products/docker-desktop/）或 OrbStack，装完重开终端再试"
docker info >/dev/null 2>&1 \
  || die "docker 守护进程没起来。启动 Docker Desktop / OrbStack（或 colima start），等鲸鱼图标稳定后再试"
docker compose version >/dev/null 2>&1 \
  || die "缺少 docker compose v2（Docker Desktop / OrbStack 自带；colima 需另装 docker-compose 并确认 docker compose version 可用）"

say "Docker $(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo '?') · $(uname -m) · compose $(docker compose version --short 2>/dev/null || echo '?')"
case "$(uname -m)" in
  arm64) say "Apple Silicon：两个镜像都有 arm64 版，直接原生跑" ;;
  x86_64) say "Intel Mac：走 x86_64 镜像，能用但慢一些" ;;
esac

# --- 2. 目录与 .env ------------------------------------------------------------
mkdir -p data napcat/config napcat/qq

if [ ! -f .env ]; then
  cp .env.example .env
  say "首次运行：已从 .env.example 生成 .env（要填 QQ 小号就编辑它）"
fi

env_get() {
  # 读取 .env 里的简单 KEY=VALUE：忽略注释/空行，去掉 CRLF 的 \r 和成对引号
  awk -F= -v k="$1" -v q="'" '
    $1==k {
      sub(/^[^=]*=/, ""); gsub(/\r/, "")
      c = substr($0, 1, 1)
      if ((c == "\"" || c == q) && substr($0, length($0), 1) == c) $0 = substr($0, 2, length($0) - 2)
      print; exit
    }' .env 2>/dev/null || true
}
ASTRBOT_PORT="$(env_get ASTRBOT_PORT)"; ASTRBOT_PORT="${ASTRBOT_PORT:-6185}"
NAPCAT_PORT="$(env_get NAPCAT_PORT)";   NAPCAT_PORT="${NAPCAT_PORT:-6099}"
QQ_ACCOUNT="$(env_get QQ_ACCOUNT)"

# 宿主 uid/gid 对齐（compose 里 NAPCAT_UID/GID 的兜底值是 501/20）
export NAPCAT_UID="$(id -u)"
export NAPCAT_GID="$(id -g)"

# --- 3. 依赖文件检查（补丁来自仓库，不复制一份）---------------------------------
missing=""
for f in dashscope_tts.py stage.py respond_stage.py chat_layout.py; do
  [ -f "../deploy/patches/$f" ] || missing="$missing ../deploy/patches/$f"
done
if [ -n "$missing" ]; then
  die "缺少核心补丁：$missing
本套件按仓库布局运行（deploy-macos/ 与 deploy/patches/ 同级）。
只想要基础聊天，可先注释掉 compose.yaml 里 4 行 patches 挂载再重跑。"
fi
[ -f "../deploy/Dockerfile" ] || die "缺少 ../deploy/Dockerfile（镜像构建复用云上套件那一份）"

# --- 4. 端口占用提示 ------------------------------------------------------------
if ! docker compose ps --status running --quiet 2>/dev/null | grep -q .; then
  if command -v lsof >/dev/null 2>&1; then
    for pair in "$ASTRBOT_PORT:AstrBot 面板" "$NAPCAT_PORT:NapCat WebUI"; do
      port="${pair%%:*}"; name="${pair#*:}"
      if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        warn "$name 端口 $port 已被别的进程占用；改 .env 里的 ASTRBOT_PORT / NAPCAT_PORT，或先停掉占用者"
      fi
    done
  fi
fi

# --- 5. 构建并启动 --------------------------------------------------------------
docker compose config -q || die "compose.yaml 解析失败（原始报错见上）"

# 本地还没有镜像时 --no-build 会去 registry 找 dafeiyu-astrbot:macos 并失败，这里兜一下
if [ "$BUILD" -eq 0 ] && ! docker image inspect dafeiyu-astrbot:macos >/dev/null 2>&1; then
  warn "本地还没有 dafeiyu-astrbot:macos 镜像，忽略 --no-build，先构建一次"
  BUILD=1
fi

if [ "$BUILD" -eq 1 ]; then
  if [ "$PULL" -eq 1 ]; then
    say "拉取最新基础镜像并构建（AstrBot v4.29.0 + edge-tts / imageio-ffmpeg）"
    docker compose build --pull
  else
    say "构建 AstrBot 镜像（首次几分钟，之后走缓存）"
    docker compose build
  fi
else
  say "跳过镜像构建（--no-build）"
fi

say "启动容器"
docker compose up -d --remove-orphans

# --- 6. 等面板就绪 --------------------------------------------------------------
say "等待 AstrBot 面板就绪（首次启动要初始化 ./data，最长约 3 分钟）"
ready=0
for _ in $(seq 1 90); do
  if curl -s -o /dev/null "http://127.0.0.1:${ASTRBOT_PORT}/"; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  warn "面板 3 分钟没起来，astrbot 最近日志："
  docker compose logs --tail 40 astrbot || true
  die "启动未完成。./data 已保留，修好后重跑 ./start.sh"
fi

for c in astrbot napcat; do
  running="$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || echo false)"
  if [ "$running" != "true" ]; then
    warn "$c 没在运行，最近日志："
    docker compose logs --tail 40 "$c" || true
    die "$c 启动失败"
  fi
done

# --- 7. 收尾提示 ----------------------------------------------------------------
cat <<EOF

=== 起来了 ===
  AstrBot 面板   http://127.0.0.1:${ASTRBOT_PORT}
  NapCat WebUI   http://127.0.0.1:${NAPCAT_PORT}/webui

下一步：
 1. 登录 QQ
EOF
if [ -n "$QQ_ACCOUNT" ]; then
  cat <<EOF
    已用 .env 里的 QQ_ACCOUNT=${QQ_ACCOUNT} 走快速登录；失败就打开上面的 WebUI 扫码。
EOF
else
  cat <<EOF
    .env 里没填 QQ_ACCOUNT，打开上面的 WebUI 扫码登录小号。
EOF
fi
cat <<EOF
    指向 AstrBot 的反向 WS 已自动写好（ws://astrbot:6199/ws），不用手动加。

 2. 配提供商（面板 → 服务提供商）：DeepSeek / 百炼视觉 / 百炼语音，
    记忆嵌入用宿主 Ollama 时地址填 http://host.docker.internal:11434

 3. 装插件与人格：
      bash ../deploy-kit/bootstrap.sh          # 生成 payload/
      把 payload/plugins/* 拷进 ./data/plugins/，人格正文粘进面板

常用命令：
  ./start.sh --no-build              # 只重启，不重建镜像
  ./stop.sh                          # 停容器（数据保留）
  docker compose logs -f astrbot     # 看日志
EOF
