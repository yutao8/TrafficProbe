#!/usr/bin/env bash
# macOS / Linux 启动脚本
set -e
cd "$(dirname "$0")"

BIN=./TrafficProbe
[ -x "$BIN" ] || { echo "找不到可执行文件 $BIN，请先构建"; exit 1; }

if pgrep -f "TrafficProbe record" >/dev/null 2>&1; then
  echo "已经在运行了。"
  exit 0
fi

case "$(uname -s)" in
  Darwin) DATA="$HOME/Library/Application Support/TrafficProbe" ;;
  *)      DATA="${XDG_DATA_HOME:-$HOME/.local/share}/TrafficProbe" ;;
esac

mkdir -p "$DATA"
echo "开始后台录制..."
nohup "$BIN" record --tray > "$DATA/trafficprobe.log" 2>&1 &
echo "已启动，PID $!"
echo "数据目录：$DATA"
echo "查看日志：tail -f \"$DATA/trafficprobe.log\""
echo "停止：./stop.sh"
