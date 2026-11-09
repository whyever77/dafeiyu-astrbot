#!/usr/bin/env bash
# 大肥鱼 · macOS 停止 / 清理
#
#   ./stop.sh            # 停容器，数据保留；./start.sh --no-build 秒起
#   ./stop.sh --down     # 删除容器与网络，./data 与 ./napcat 保留
#   ./stop.sh --purge    # 连数据一起删（AstrBot 记忆/插件/密钥 + QQ 登录态），需输入 yes 确认
#   ./stop.sh --purge -y # 同上，跳过确认（脚本里用）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

MODE=stop
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --down) MODE=down ;;
    --purge) MODE=purge ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf '\033[31m错误:\033[0m 未知参数 %s（--help 看用法）\n' "$arg" >&2
      exit 2
      ;;
  esac
done

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m错误:\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "没找到 docker"
[ -f compose.yaml ] || die "不在 deploy-macos 目录（找不到 compose.yaml）"

case "$MODE" in
  stop)
    say "停止容器（数据保留）"
    docker compose stop
    say "已停止。重启用 ./start.sh --no-build"
    ;;
  down)
    say "删除容器与网络（./data、./napcat 保留）"
    docker compose down --remove-orphans
    say "已清理容器。重启用 ./start.sh"
    ;;
  purge)
    if [ "$ASSUME_YES" -ne 1 ]; then
      printf '\033[31m这会删除：\033[0m\n'
      printf '  ./data    AstrBot 的记忆 / 插件 / 提供商密钥\n'
      printf '  ./napcat  QQ 登录态 / NapCat 配置\n'
      printf '.env 会保留。确认输入 yes：'
      answer=""
      read -r answer || true
      [ "$answer" = "yes" ] || { say "已取消，什么都没删"; exit 0; }
    fi
    say "删除容器与网络"
    docker compose down --remove-orphans || true
    say "删除数据目录"
    rm -rf -- ./data ./napcat
    say "已清空。下次 ./start.sh 会重新初始化并要重新扫码登录"
    ;;
esac
