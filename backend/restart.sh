#!/bin/bash
# 一键重启后端
echo "正在停止旧进程..."
taskkill //F //IM python.exe 2>/dev/null
sleep 1
echo "启动后端..."
cd "$(dirname "$0")"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload 2>&1 | tee "$(dirname "$0")/log/backend.log"
