"""临时复现：真实 graph 的 messages 流，观察工具调用轮的所有事件结构。"""
import sys
sys.path.insert(0, r"C:\Users\17625\Agnes-Agent")
import os
os.chdir(r"C:\Users\17625\Agnes-Agent")

from langgraph.types import Command
from langchain_core.messages import AIMessageChunk

# 与 main.py 一致：先加载模型配置再构建 graph
from app.server import load_model_config, resolve_provider_env
_cfg = load_model_config()
_real_provider = resolve_provider_env(_cfg["provider"])  # 注入 OPENAI_BASE_URL / OPENAI_API_KEY
os.environ["LLM_PROVIDER"] = _real_provider
os.environ["LLM_MODEL"] = _cfg["model"]

# 复用真实 graph（会读 .env 的 key，与线上一致）
from app.server import config as _srv_cfg, set_graph
from app.graph.builder import build_graph
_g = build_graph()
set_graph(_g, {"configurable": {"thread_id": "debug-repro-live"}})

inputs = {"messages": [("user", "我最近的任务有哪些？")]}
config = {"configurable": {"thread_id": "debug-repro-live"}}

for mode, payload in _g.stream(inputs, config, stream_mode=["updates", "messages"], recursion_limit=40):
    if mode == "messages":
        chunk, meta = payload
        print(f"[MSG] type={type(chunk).__name__} node={meta.get('langgraph_node')}")
        print(f"      content={str(getattr(chunk,'content',''))[:80]!r}")
        tcs = getattr(chunk, "tool_calls", None)
        if tcs:
            print(f"      tool_calls={[(c.get('name'), c.get('id')) for c in tcs]}")
            for c in tcs:
                print(f"        -> {c.get('name')} args={str(c.get('args'))[:200]}")
        tcc = getattr(chunk, "tool_call_chunks", None) or []
        if tcc:
            print(f"      tool_call_chunks={len(tcc)}")
            for c in tcc:
                if isinstance(c, dict):
                    print(f"        dict: name={c.get('name')!r} id={c.get('id')!r} args={str(c.get('args'))[:80]!r} index={c.get('index')!r}")
                else:
                    print(f"        name={c.name!r} id={c.id!r} args={str(c.args)[:80]!r} index={c.index!r}")
    else:
        for node, upd in (payload or {}).items():
            msgs = upd.get("messages") if isinstance(upd, dict) else None
            kinds = []
            for m in (msgs or []):
                t = m.get("type") if isinstance(m, dict) else getattr(m, "type", "?")
                kinds.append(t)
            print(f"[UPD] node={node} msg_types={kinds}")
            if msgs:
                for m in msgs:
                    t = m.get("type") if isinstance(m, dict) else getattr(m, "type", "?")
                    if t == "ai":
                        tcs = m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)
                        print(f"      AI tool_calls={[(c.get('name'), c.get('id')) for c in (tcs or [])]}")
print("DONE")
