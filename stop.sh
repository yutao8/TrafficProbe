#!/usr/bin/env bash
# macOS / Linux 停止脚本
cd "$(dirname "$0")"
if pkill -f "TrafficProbe record"; then
  echo "已停止。"
else
  echo "当前没有在运行。"
fi
