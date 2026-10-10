#!/bin/bash
# agn_health.sh — agnes-media 创作台健康检查（进程/端口/日志 + 重启提示）
# 用法：bash agn_health.sh [端口，默认 8081]   （在远端 god.makeup 执行）
set -u
PORT=${1:-8081}
echo "== 进程 =="
ps aux | grep "[p]ython -m app.service" | awk '{print $2, $11, $12, $13, $14}'
echo "== 端口探测 =="
curl -sS -m 5 -o /dev/null -w "HTTP %{http_code}\n" "http://127.0.0.1:$PORT/" || echo "连接失败"
echo "== 服务日志尾 =="
tail -n 15 /home/mirror/Agnes-Agent/ag-service.log 2>/dev/null || echo "(无 ag-service.log)"
echo "== 重启提示 =="
echo "手工: cd /home/mirror/Agnes-Agent && nohup .venv/bin/python -m app.service --host 0.0.0.0 --port $PORT >> ag-service.log 2>&1 &"
echo "systemd: sudo systemctl restart agnes-agent（见 deploy/agnes-agent.service）"