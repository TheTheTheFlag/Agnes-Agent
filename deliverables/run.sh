#!/bin/sh
# ============================================
#  像素飙车 Pixel Racer - macOS / Linux 启动脚本
#  用法: bash run.sh   或   chmod +x run.sh && ./run.sh
# ============================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GAME="$SCRIPT_DIR/pixel-racer.html"

if [ ! -f "$GAME" ]; then
  echo "[错误] 未找到 pixel-racer.html，请确认与启动脚本放在同一目录。"
  exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$GAME"
elif command -v open >/dev/null 2>&1; then
  open "$GAME"
else
  echo "未找到自动打开命令，请手动用浏览器打开: $GAME"
fi
exit 0
