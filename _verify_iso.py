# -*- coding: utf-8 -*-
"""临时验证：测试跑完后生产库无 test-tid 污染（跑完即删）。"""
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
for t in ("messages", "app_events"):
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE thread_id LIKE 'test-tid%'").fetchone()[0]
        print(f"{t}: test-tid 残留 {n} 行")
    except Exception as e:
        print(f"{t}: 查询失败 {e}")
conn.close()
