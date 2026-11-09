#!/usr/bin/env bash
# 读取 .env 里的简单 KEY=VALUE（start.sh 用；单独放出来便于测试）
#
#   . ./env.sh
#   port="$(env_get ASTRBOT_PORT)"
#
# 规则：忽略注释/空行；去掉 CRLF 与成对引号；未加引号的值去掉行尾注释（" # ..."）。
# ENV_FILE 可覆盖文件路径，默认 ./.env。
env_get() {
  awk -F= -v k="$1" -v q="'" '
    $1 == k {
      sub(/^[^=]*=/, ""); gsub(/\r/, "")
      c = substr($0, 1, 1)
      if (c == "\"" || c == q) {
        # 引号值：只去掉成对引号，引号里的 # 不当注释
        if (substr($0, length($0), 1) == c) $0 = substr($0, 2, length($0) - 2)
      } else {
        # 未加引号：`ASTRBOT_PORT=6186  # 面板` 的注释不能带进端口
        sub(/[ \t]+#.*$/, ""); sub(/[ \t]+$/, "")
      }
      print; exit
    }' "${ENV_FILE:-.env}" 2>/dev/null || true
}
