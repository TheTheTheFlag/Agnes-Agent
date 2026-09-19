#!/usr/bin/env python3
"""视频参考图上传日志监听器"""

import json
import os
import time
from datetime import datetime

LOG_FILE = "/tmp/video_ref_upload.log"

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    print(line, end="", flush=True)

if __name__ == "__main__":
    log("=" * 60)
    log("视频参考图上传日志监听器启动")
    log(f"日志文件: {LOG_FILE}")
    log("=" * 60)

    # 清空旧日志
    with open(LOG_FILE, "w") as f:
        f.write("")

    last_size = os.path.getsize(LOG_FILE)
    log("开始监听...")

    while True:
        try:
            time.sleep(0.1)
            if os.path.exists(LOG_FILE):
                current_size = os.path.getsize(LOG_FILE)
                if current_size > last_size:
                    with open(LOG_FILE, "r") as f:
                        f.seek(last_size)
                        new_content = f.read()
                        last_size = current_size
                    print(new_content, end="", flush=True)
        except KeyboardInterrupt:
            log("监听停止")
            break
