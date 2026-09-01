"""临时验证：用新代码在独立端口起服务，验证 tool_chunk 带 name + tool end 事件。"""
import os, sys, threading
os.chdir(r"C:\Users\17625\Agnes-Agent")
sys.path.insert(0, r"C:\Users\17625\Agnes-Agent")

from app.server import load_model_config, resolve_provider_env, set_graph
_cfg = load_model_config()
_real = resolve_provider_env(_cfg["provider"])
os.environ["LLM_PROVIDER"] = _real
os.environ["LLM_MODEL"] = _cfg["model"]

from app.graph.builder import build_graph
_g = build_graph()
set_graph(_g, {"configurable": {"thread_id": "verify-live-status"}})

import uvicorn
from app.server import app as fastapi_app

port = 8010
t = threading.Thread(target=lambda: uvicorn.run(fastapi_app, host="127.0.0.1", port=port, log_level="warning"), daemon=True)
t.start()
print(f"VERIFY_SERVER_READY port={port}")
import time
time.sleep(2.5)
while True:
    time.sleep(10)
