"""app.server.api.chat — 对话 API（流式 /api/chat + 斜杠命令 /api/command）。"""
import json
import asyncio
import logging
import uuid as _uuid
from langgraph.types import Command
from langchain_core.messages import AIMessageChunk
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from app.server import store as _store  # 模块引用，保证 _GRAPH/_CONFIG 实时读取
from app.server import config as _srv_cfg
from app.server.store import add_log_entry
from app.server.api.memory import get_threads
from app.config import DB_PATH, CHECKPOINT_DB_PATH
import os as _os
_BASE_DIR = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

router = APIRouter()

# ----- 诊断 logger（受 CHAT_DIAG=1 控制，写到 data/chat_diag.log）-----
_diag_logger = logging.getLogger("chat_diag")
_diag_logger.setLevel(logging.DEBUG)
_diag_logger.propagate = False  # 不让根 logger 重复输出
if not _diag_logger.handlers:
    _diag_log_path = _os.path.join(_os.path.dirname(DB_PATH), "chat_diag.log")
    _fh = logging.FileHandler(_diag_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _diag_logger.addHandler(_fh)

# 内部 LLM 输出过滤（摘要生成等非对话调用）的摘要结构 marker。
# 用"前缀"形式（去掉尾部 **）：token 逐字到达时，一旦拼出"**用户目标"即可判定
# 为内部摘要，无需等完整"**用户目标**"，避免前几个 token 泄漏到前端。
_SUMMARY_MARKERS = ("**用户目标", "**已完成步骤", "**待办事项", "更新后的摘要", "对话摘要")


def _filter_internal_tokens(node: str, run_id, text: str, state: dict) -> str:
    """过滤节点内的"内部 LLM"输出（如 update_summary 的摘要生成）。

    update_summary 等调用发生在 chatbot 节点内部，其 token 会被 messages 流以
    node='chatbot' 捕获并推给前端，导致摘要文本污染聊天框。
    检测策略：按 LLM 调用 run_id 分段；某段一旦出现摘要结构 marker，整段后续 token 丢弃。
    返回过滤后的 text（可能为空字符串）。state 为跨 token 的累积状态（每请求独立）。
    """
    if node != "chatbot":
        return text
    if run_id != state.get("run_id"):
        state.update(run_id=run_id, text="", internal=False)
    if state.get("internal"):
        return ""
    state["text"] = (state.get("text", "") + text)[-60:]
    # 前缀命中：内部摘要段（update_summary 等）以这些结构 marker 开头，token 逐字到达时，
    # 若只累积到 marker 前缀（如 "**用户"）就会因"完整 marker 未出现"被误放行，
    # 导致摘要开头（如 "**用户"）泄漏到聊天框。改为：累积文本是任一 marker 的前缀时，
    # 立即判定为内部摘要并丢弃（含当前 token）。段开头若被误判（如正常回复以 "**" 开头），
    # 前端会用 updates 模式推送的 final 文本兜底，回复内容不受影响。
    if state["text"] and any(mk.startswith(state["text"]) for mk in _SUMMARY_MARKERS):
        state["internal"] = True
        return ""
    if any(mk in state["text"] for mk in _SUMMARY_MARKERS):
        state["internal"] = True
        return ""
    return text

@router.post("/api/chat")
async def chat_endpoint(payload: dict):
    """主对话窗口的发送端点（流式 SSE 推送 token）。
    payload: { "message": "用户输入", "thread_id": "可选" }
    推送事件类型：
      - "node"        节点名（chatbot/planner/executor/...）
      - "token"       LLM 单个 token（text 字段）
      - "tool"        工具调用开始/结束
      - "done"        流程结束
      - "error"       出错
    """
    if _srv_cfg._GRAPH is None:
        return JSONResponse({"error": "graph 未注入，请先启动 main.py"}, status_code=503)

    # resume 模式：从中断点继续（payload 带 resume=true, allow, mode）
    is_resume = bool((payload or {}).get("resume"))
    if is_resume:
        resume_val = {"allow": bool((payload or {}).get("allow", False))}
        mode = (payload or {}).get("mode")
        if mode in ("per_ask", "session_allow", "always_allow"):
            resume_val["mode"] = mode
        inputs = Command(resume=resume_val)
    else:
        message = (payload or {}).get("message", "").strip()
        if not message:
            return JSONResponse({"error": "message 不能为空"}, status_code=400)
        inputs = {"messages": [("user", message)]}

    thread_id = (payload or {}).get("thread_id") or (_srv_cfg._CONFIG or {}).get("configurable", {}).get("thread_id", "default")
    config = {"configurable": {"thread_id": thread_id}}

    queue: asyncio.Queue = asyncio.Queue()

    async def run_graph():
        try:
            # inputs 已在 chat_endpoint 构造（首次=user 消息；resume=Command(resume=...))
            # 同步 API 双通道：updates 拿节点状态 + messages 拿 token chunk。
            # stream 是同步阻塞的，丢到线程跑，经队列回传。
            import asyncio as _aio
            from queue import Queue
            from threading import Thread
            sync_q: Queue = Queue()

            def sync_runner():
                # 内部 LLM 输出过滤：update_summary 等节点内的非对话 LLM 调用（摘要生成）也会
                # 被 messages 流以 node='chatbot' 捕获，必须丢弃，否则摘要文本污染聊天框。
                _tok_state = {}
                try:
                    for mode, payload in _srv_cfg._GRAPH.stream(
                        inputs, config,
                        stream_mode=["updates", "messages"],
                        recursion_limit=40,
                    ):
                        if mode == "messages":
                            # payload = (AIMessageChunk/AIMessage, metadata)
                            chunk, meta = payload
                            node = (meta or {}).get("langgraph_node", "")
                            # 只处理流式 chunk：langgraph 的 messages 流对同一次 LLM 调用会先
                            # emit 流式 chunk（AIMessageChunk，含非流式模型的模拟流式），随后在
                            # on_llm_end 再 emit 一次完整消息（AIMessage）。二者内容相同，若都推给
                            # 前端，回复会被流式显示两遍（表现为回复文本重复）。因此忽略
                            # AIMessage（end 完整消息），完整文本由 updates 模式的 final 事件兜底。
                            if not isinstance(chunk, AIMessageChunk):
                                continue
                            text = ""
                            if hasattr(chunk, "content"):
                                text = chunk.content or ""
                            elif isinstance(chunk, dict):
                                text = chunk.get("content") or ""
                            if text:
                                text = _filter_internal_tokens(node, (meta or {}).get("run_id"), text, _tok_state)
                                if text:
                                    sync_q.put({"step": "token", "text": text, "node": node})
                            # 工具调用参数增量也透传（便于前端展示意图）
                            # 关键：AIMessageChunk.tool_calls（非 _chunks 后缀）是完整字段，
                            # 在第一个非空 chunk 就有 name+id+args；tool_call_chunks 早期可能为 null
                            _diag = _os.environ.get("CHAT_DIAG") == "1"
                            if _diag:
                                _tcs = getattr(chunk, "tool_calls", None)
                                _tccs = getattr(chunk, "tool_call_chunks", None)
                                _diag_logger.info(f"chunk tool_calls={_tcs}  tool_call_chunks={_tccs}")
                            tc_full = getattr(chunk, "tool_calls", None)
                            if tc_full:
                                for tc in tc_full:
                                    tc_name = getattr(tc, "name", None)
                                    tc_args = getattr(tc, "args", {}) or {}
                                    if isinstance(tc_args, dict):
                                        tc_args = json.dumps(tc_args, ensure_ascii=False)
                                    if _diag:
                                        _diag_logger.info(f"tool_chunk name={tc_name} args={str(tc_args)[:80]}")
                                    sync_q.put({"step": "tool_chunk", "name": tc_name, "args": str(tc_args)[:200], "node": node})
                            # 兼容老路径：tool_call_chunks
                            tool_calls = getattr(chunk, "tool_call_chunks", None)
                            if tool_calls:
                                for tc in tool_calls:
                                    tc_name = getattr(tc, "name", None)
                                    tc_args = getattr(tc, "args", "") or ""
                                    if _diag:
                                        _diag_logger.info(f"tool_chunk(old) name={tc_name} args={str(tc_args)[:80]}")
                                    sync_q.put({"step": "tool_chunk", "name": tc_name, "args": str(tc_args)[:200], "node": node})
                        else:
                            # updates 模式：payload 是 {node: update_dict}
                            for node, upd in (payload or {}).items():
                                if node == "__interrupt__":
                                    # 工具审批：upd 本身就是 interrupt tuple（langgraph 1.x）
                                    try:
                                        int_list = upd if isinstance(upd, (list, tuple)) else (upd.get("__interrupt__") or ())
                                        for int_info in int_list:
                                            val = getattr(int_info, "value", int_info) if not isinstance(int_info, dict) else int_info
                                            sync_q.put({"step": "approval", "data": {
                                                "question": val.get("question", "") if isinstance(val, dict) else str(val),
                                                "command": val.get("command", "") if isinstance(val, dict) else "",
                                                "mode": val.get("mode", "per_ask") if isinstance(val, dict) else "per_ask",
                                            }})
                                    except Exception as ex:
                                        sync_q.put({"step": "approval", "data": {"question": str(ex), "command": "", "mode": "per_ask"}})
                                    # interrupt 后 graph 暂停，本流到此结束（用户操作后走 /api/chat resume）
                                    break
                                # node 事件附带具体信息（state 不再含 task_plan 业务对象，事件不携带子任务信息；
                                # 前端从 events 流 + DB 重放重建）
                                sync_q.put({"step": "node", "name": node, "phase": "start", "info": {}})
                                # 提取节点返回的最终 AIMessage（最可靠的"本轮最终回复"，用于兜底）
                                if isinstance(upd, dict) and isinstance(upd.get("messages"), list):
                                    # 先收集 AIMessage 的 tool_calls 映射（tool_call_id -> 工具名）
                                    call_names = {}
                                    for m in upd["messages"]:
                                        t = m.get("type") if isinstance(m, dict) else getattr(m, "type", "")
                                        if t == "ai" and isinstance(m, dict):
                                            for tc in (m.get("tool_calls") or []):
                                                call_names[tc.get("id", "")] = tc.get("name", "")
                                    for m in reversed(upd["messages"]):
                                        is_ai = (isinstance(m, dict) and m.get("type") == "ai") or \
                                                (hasattr(m, "type") and getattr(m, "type") == "ai")
                                        if is_ai:
                                            if isinstance(m, dict):
                                                content = m.get("content") or ""
                                                if isinstance(content, list):
                                                    content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
                                            else:
                                                content = getattr(m, "content", "") or ""
                                                if isinstance(content, list):
                                                    content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
                                            if content:
                                                # 只把 chatbot/summarizer 的回复作为对话最终消息；
                                                # executor/planner 的输出由执行树+事件表达，不污染对话气泡。
                                                if node in ("chatbot", "summarizer"):
                                                    sync_q.put({"step": "final", "node": node, "text": content})
                                                    # 兜底落表：流被 401/异常截断时 chatbot 节点不会 return，
                                                    # 也就不会执行 builder.py:299 的 mm.add_message。
                                                    # 这里在 SSE final 事件发出时同步落 messages 表，
                                                    # 保证"前端看到了 = 表里就有"，切会话时 /api/messages 能回放。
                                                    # add_message 自带 60s 同 role+content 幂等保护，重复触发无副作用。
                                                    try:
                                                        from app.memory import MemoryManager as _MM
                                                        _mm = _MM(db_path=DB_PATH, thread_id=thread_id)
                                                        _mm.add_message(thread_id, "assistant", content)
                                                    except Exception:
                                                        pass
                                            break
                                        # ToolMessage → 工具结果（供前端工具卡片展示）
                                        is_tool = (isinstance(m, dict) and m.get("type") == "tool") or \
                                                  (hasattr(m, "type") and getattr(m, "type") == "tool")
                                        if is_tool:
                                            tcid = m.get("tool_call_id") if isinstance(m, dict) else getattr(m, "tool_call_id", "")
                                            tc_name = call_names.get(tcid, "tool")
                                            tc_content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                                            if isinstance(tc_content, list):
                                                tc_content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in tc_content)
                                            sync_q.put({"step": "tool", "name": tc_name, "phase": "end", "output_preview": str(tc_content)[:300]})
                                sync_q.put({"step": "node", "name": node, "phase": "end"})
                except Exception as e:
                    sync_q.put({"_err": str(e)})
                finally:
                    sync_q.put(None)  # 哨兵

            t = Thread(target=sync_runner, daemon=True)
            t.start()
            while True:
                evt = await _aio.to_thread(sync_q.get)
                if evt is None:
                    break
                if "_err" in evt:
                    raise Exception(evt["_err"])
                await queue.put(evt)
            await queue.put({"step": "done"})
        except Exception as e:
            await queue.put({"step": "error", "message": str(e)})
        finally:
            await queue.put({"step": "__close__"})

    async def event_gen():
        import asyncio as _aio
        task = _aio.create_task(run_graph())
        while True:
            try:
                evt = await _aio.wait_for(queue.get(), timeout=120)
            except _aio.TimeoutError:
                yield "data: " + json.dumps({"step": "heartbeat"}, ensure_ascii=False) + "\n\n"
                continue
            yield "data: " + json.dumps(evt, ensure_ascii=False, default=str) + "\n\n"
            if evt.get("step") in ("__close__", "error"):
                break
        try:
            await task
        except Exception:
            pass

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "Access-Control-Allow-Origin": "*",
    })


@router.post("/api/command")
async def command_endpoint(payload: dict):
    """斜杠命令端点：/new, /resume, /threads, /model, /help, /clear, /save, /system 等。
    返回 JSON 结果，UI 负责呈现。
    """
    cmd = (payload or {}).get("command", "").strip()
    if not cmd.startswith("/"):
        return JSONResponse({"error": "命令必须以 / 开头"}, status_code=400)
    parts = cmd[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    global _CONFIG
    current_tid = (_srv_cfg._CONFIG or {}).get("configurable", {}).get("thread_id", "default")

    if name in ("help", "?"):
        return {"result": "可用命令：\n" + "\n".join([
            "/new          开新对话（生成新 thread_id）",
            "/resume [tid] 继续指定 thread（不填则列候选）",
            "/threads      列出最近 10 个 thread",
            "/model        查看当前模型",
            "/system [m]   切换审批模式 (per_ask | session_allow | always_allow)",
            "/clear        清空当前对话的 logs/events（保留 thread）",
            "/save         导出当前 thread 的消息为 JSON",
            "/delete <tid> 删除指定会话（messages/tasks/checkpoints 全部清除）",
            "/help         显示本帮助",
        ])}
    if name == "new":
        import uuid as _uuid
        new_tid = str(_uuid.uuid4())
        _srv_cfg._CONFIG = {"configurable": {"thread_id": new_tid}}
        # 写入 .thread_id 文件以保持 main.py 行为一致
        try:
            with open(_os.path.join(_BASE_DIR, ".thread_id"), "w", encoding="utf-8") as f:
                f.write(new_tid)
        except Exception:
            pass
        return {"result": f"已开新对话: {new_tid}", "thread_id": new_tid}
    if name == "resume":
        if not arg:
            # 列候选
            r = await get_threads()
            return {"result": "请用 /resume <thread_id> 选择：", "candidates": r["threads"][:10]}
        _srv_cfg._CONFIG = {"configurable": {"thread_id": arg}}
        try:
            with open(_os.path.join(_BASE_DIR, ".thread_id"), "w", encoding="utf-8") as f:
                f.write(arg)
        except Exception:
            pass
        return {"result": f"已切到 thread: {arg}", "thread_id": arg}
    if name == "threads":
        r = await get_threads()
        return {"result": f"共 {len(r['threads'])} 个 thread", "threads": r["threads"][:10]}
    if name == "model":
        from app.llm import create_llm
        return {"result": f"当前 provider: openai_compatible, model: glm-5.1 (实际由 .env OPENAI_* 决定)"}
    if name == "system":
        if not arg:
            return {"result": f"当前审批模式: {(_srv_cfg._CONFIG or {}).get('configurable', {}).get('approval_mode', 'session_allow')}"}
        if arg not in ("per_ask", "session_allow", "always_allow"):
            return {"error": f"未知模式: {arg}"}
        _srv_cfg._CONFIG = _srv_cfg._CONFIG or {"configurable": {}}
        _srv_cfg._CONFIG["configurable"]["approval_mode"] = arg
        return {"result": f"已切到 {arg}（注：CLI 主循环未感知，前端 session 可继续）"}
    if name == "clear":
        _log_entries.clear()
        _events.clear()
        return {"result": "已清空 logs/events"}
    if name == "save":
        r = await get_messages(thread_id=current_tid, limit=500)
        return {"result": f"已导出 {r['total']} 条消息", "messages": r["messages"]}
    if name == "delete":
        if not arg:
            return {"error": "用法: /delete <thread_id>"}
        deleted = await _delete_thread(arg)
        return {"result": f"已删除会话 {arg}（messages={deleted['messages']} tasks={deleted['tasks']} checkpoints={deleted['checkpoints']}）", "deleted": deleted}
    return {"error": f"未知命令: /{name}，输入 /help 查看列表"}


async def _delete_thread(thread_id: str) -> dict:
    """删除某 thread 的所有数据（memory.db + checkpoints.db）。"""
    import sqlite3 as _sqlite
    counts = {"messages": 0, "tasks": 0, "summaries": 0, "commands": 0, "cache": 0, "subtasks": 0, "checkpoints": 0}

    # memory.db
    db = _sqlite.connect(DB_PATH)
    try:
        # 先删子任务（外键）
        task_ids = [r[0] for r in db.execute("SELECT id FROM task_plans WHERE thread_id = ?", (thread_id,))]
        for tid in task_ids:
            cur = db.execute("DELETE FROM subtasks WHERE task_plan_id = ?", (tid,))
            counts["subtasks"] += cur.rowcount
        cur = db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        counts["messages"] += cur.rowcount
        cur = db.execute("DELETE FROM task_plans WHERE thread_id = ?", (thread_id,))
        counts["tasks"] += cur.rowcount
        cur = db.execute("DELETE FROM task_summaries WHERE thread_id = ?", (thread_id,))
        counts["summaries"] += cur.rowcount
        cur = db.execute("DELETE FROM command_history WHERE thread_id = ?", (thread_id,))
        counts["commands"] += cur.rowcount
        db.commit()
    finally:
        db.close()

    # checkpoints.db（LangGraph checkpoint 表）
    try:
        ck = _sqlite.connect(CHECKPOINT_DB_PATH)
        try:
            cur = ck.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
            counts["checkpoints"] += cur.rowcount
            # 部分版本有 writes / checkpoint_writes 表
            for tbl in ("writes", "checkpoint_writes"):
                try:
                    cur = ck.execute(f"DELETE FROM {tbl} WHERE thread_id = ?", (thread_id,))
                    counts["checkpoints"] += cur.rowcount
                except _sqlite.OperationalError:
                    pass
            ck.commit()
        finally:
            ck.close()
    except Exception:
        pass
    return counts


def _safe(obj):
    """把不可序列化的对象转成可序列化形式。"""
    try:
        return json.loads(json.dumps(obj, default=str, ensure_ascii=False))
    except Exception:
        return {"_raw": str(obj)}


# ==================== 定时任务调度器 ====================

