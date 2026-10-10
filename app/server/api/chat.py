"""app.server.api.chat — 对话 API（流式 /api/chat + 斜杠命令 /api/command）。"""
import json
import asyncio
import uuid as _uuid
from langgraph.types import Command
from langchain_core.messages import AIMessageChunk
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.server import store as _store  # 模块引用，保证 _GRAPH/_CONFIG 实时读取
from app.server import config as _srv_cfg
from app.server.store import add_log_entry
from app.server.api.memory import get_threads
from app.config import DB_PATH, CHECKPOINT_DB_PATH
from app.userctx import (current_user, set_current_user, reset_current_user,
                         set_current_llm, reset_current_llm, get_user_llm)
import os as _os
_BASE_DIR = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

router = APIRouter()

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


def _supplement_sse_response(thread_id: str, message: str, answer: str = None, note: str = None):
    """DAG 执行期插话的轻量 SSE：不启动第二个 graph run。

    两种形态（由参数区分）：
    - answer=None：普通插话入队，回一句"已加入进行中任务"，然后立即 done，
      让前端本轮结算、解锁发送器，等正在执行的 executor 消费补充。
    - answer=<文本>：查询类插话的只读回答，直接把 answer 作为最终回复推给前端
      （不建 run、不入队、不调 LLM），同样立即 done。
    与 chatbot 入队文案保持一致（builder.py 的 _ 分支），用户感知统一。
    """
    from app.graph.builder import _looks_like_query
    if answer is not None and answer.strip():
        _is_q = _looks_like_query(message)
        _note = (note or "[执行期间插话·只读回答]") if _is_q else (note or "（执行期间插话）")
        _text = (
            f"{answer}\n\n{_note}"
        )
    else:
        _pui = message.strip()[:2000]
        _text = (
            f"收到，已将你的消息加入进行中的任务，执行器会在处理下一节点时结合它继续推进"
            f"（计划完成前我不会另起炉灶）：\n\n> {_pui}"
        )
    try:
        add_log_entry("info" if answer is None else "info",
                      f"DAG 执行中{'补充消息入队' if answer is None else '进度查询只读回答'}: {message[:80]}")
    except Exception:
        pass
    # 与 chatbot 分流路径保持一致的落库：用户消息 + 回复都写进 messages 表，
    # 让 /api/messages 回放与前端展示一致（切会话/刷新后能看到这条插话）。
    try:
        from app.memory import MemoryManager as _MM
        _mm = _MM(db_path=DB_PATH, thread_id=thread_id)
        _mm.add_message(thread_id, "user", message)
        _mm.add_message(thread_id, "assistant", _text)
    except Exception:
        pass

    async def _gen():
        yield "data: " + json.dumps({"step": "final", "node": "chatbot", "text": _text},
                                    ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps({"step": "done"}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "Access-Control-Allow-Origin": "*",
    })


def _ask_ack_sse_response(thread_id: str, answer: str):
    """ask 选项卡答复已被受理的轻量确认 SSE。

    答复本身不是「插话」也不是「新任务」：executor 正挂起在进程内注册表里等这个
    答案，submit_answer 已经唤醒它。这里只回一句确认让前端解锁发送器并结束本轮，
    后续 executor 的进展由它自己已建立的 SSE 继续推。

    注意前端 respondAsk() 发的是空 message（答案在 answer 字段），所以这里不往
    messages 表写空的用户消息，只落一条 assistant 确认。
    """
    _text = f"已收到你的选择，执行继续：\n\n> {str(answer or '')[:500]}"
    try:
        add_log_entry("info", f"[ask] 用户答复已提交: {str(answer or '')[:80]}")
    except Exception:
        pass
    try:
        from app.memory import MemoryManager as _MM
        _mm = _MM(db_path=DB_PATH, thread_id=thread_id)
        _mm.add_message(thread_id, "assistant", _text)
    except Exception:
        pass

    async def _gen():
        yield "data: " + json.dumps({"step": "final", "node": "chatbot", "text": _text},
                                    ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps({"step": "done"}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "Access-Control-Allow-Origin": "*",
    })


@router.get("/api/chat/pending-ask")
async def pending_ask_endpoint(request: Request = None, thread_id: str = None):
    """查询该 thread 当前是否有待答复的 ask_user 问题。

    刷新页面后 ask 卡片会随历史回放成只读气泡，但进程内的注册表还挂着问题、
    executor 仍在阻塞等答案——这里把待答问题原样吐给前端，让它重新渲染成可交互卡片。
    """
    try:
        _u = getattr(getattr(request, "state", None), "username", None) or current_user()
    except Exception:
        _u = None
    try:
        from app.userctx import current_tid as _user_tid
        _tid = _user_tid(_u)
    except Exception:
        _tid = "default"
    _tid = thread_id or _tid
    from app.graph import ask_registry
    return {"pending": ask_registry.get_pending(_tid)}


@router.post("/api/chat")
async def chat_endpoint(payload: dict, request: Request = None):
    """主对话窗口的发送端点（流式 SSE 推送 token）。
    payload: { "message": "用户输入", "thread_id": "可选" }
    推送事件类型：
      - "node"        节点名（chatbot/planner/executor/...）
      - "token"       LLM 单个 token（text 字段）
      - "tool"        工具调用开始/结束
      - "llm_call"    模型调用完整输入/输出
      - "retrieval"   L6 知识检索结果（命中实体/关系/知识片段）
      - "done"        流程结束
      - "error"       出错
    """
    if _srv_cfg._GRAPH is None and not _srv_cfg._GRAPHS:
        return JSONResponse({"error": "graph 未注入，请先启动 main.py"}, status_code=503)

    # 多用户：按登录用户选 graph（每用户 LLM / DB / RAG 隔离）
    try:
        _user = getattr(request.state, "username", None) or current_user()
    except Exception:
        _user = current_user()
    try:
        _graph = _srv_cfg.get_graph(_user)
    except Exception as e:
        return JSONResponse({"error": f"用户 graph 构建失败: {e}"}, status_code=500)

    # resume 模式：从中断点继续（payload 带 resume=true, allow, mode 或 answer）
    is_resume = bool((payload or {}).get("resume"))
    if is_resume:
        resume_val = {"allow": bool((payload or {}).get("allow", False))}
        mode = (payload or {}).get("mode")
        if mode in ("per_ask", "session_allow", "always_allow"):
            resume_val["mode"] = mode
        # ask_user 选项卡的答案：预置选项或用户自定义输入，经 interrupt resume 回传，
        # 由 executor 的 ask_user 工具消费（resume_val 是 interrupt() 的返回值）。
        _answer = (payload or {}).get("answer")
        if _answer is not None:
            resume_val["answer"] = str(_answer)[:4000]
        inputs = Command(resume=resume_val)
    else:
        message = (payload or {}).get("message", "").strip()
        if not message:
            return JSONResponse({"error": "message 不能为空"}, status_code=400)
        inputs = {"messages": [("user", message)]}
        # 每轮新用户消息显式清空单次 run 的手握信号（PendingPlan/PendingUserInput）。
        # 它们只允许在本次 run 内由 chatbot/工具写入并消费；若从 checkpointer 恢复的
        # state 里残留上一 run 的 pending_plan（LangGraph SqliteSaver 重启/中断后可能把
        # 半程 writes 回到 state），route_after_chatbot 会误判去 planner，用旧目标重规划、
        # 覆盖正在执行的任务——2026-09-29 线上根因（用户插话被拉去 planner 建旧目标新计划）。
        # resume（审批续跑）分支保持原样，因为中断恢复需保留 run 内状态。
        inputs["pending_plan"] = None
        inputs["pending_user_input"] = None

    # 每用户当前会话：普通数据一律落在该用户自己的 .thread_id，不读全局 _CONFIG
    try:
        from app.userctx import current_tid as _user_tid
        _tid = _user_tid(getattr(getattr(request, "state", None), "username", None) or current_user())
    except Exception:
        _tid = "default"
    thread_id = (payload or {}).get("thread_id") or _tid
    config = {"configurable": {"thread_id": thread_id}}
    # 本轮勾选参与检索的知识库（消息框多选，默认含 __global__）。
    # 空 list / 未传 → 仅全局对话图谱。
    _kbs = (payload or {}).get("selected_kbs") or []
    if _kbs:
        _kb_list = [str(k) for k in _kbs if str(k or "").strip()]
        if _kb_list:
            config["configurable"]["selected_kbs"] = _kb_list

    # 同 thread 运行保护（2026-09-29 插话入院）：DAG 多节点执行期间，该 thread
    # 的 graph.run 一直在跑（SSE 长连接，几分钟到几十分钟）。此时前端仍允许发新消息
    # （插话），后端不能再启动第二个 run（并发写 checkpointer 冲突 + state 互相覆盖）；
    # 改走"执行期插话分流"通道（不启动新 run，见下方）：
    #   - 查询类插话（进度/状态/多久…短问句）→ 直接只读回答当前 DAG 进度摘要（纯 DB 查询，
    #     不调 LLM、不动执行状态），不再把进度问句塞进"进行中任务"队列
    #   - 其余插话 → 进补充队列，正在执行的 executor 下批节点消费注入，
    #     本请求立即返回一条"已加入进行中任务"的轻量 SSE（前端借此结算本轮）
    # run 真正启动时才占用锁（try_lock_run 拿不到 = 已有 run），
    # 释放放在 run_graph() 的 finally 里（只有真正跑起来的 run 才需要释放）。
    from app.graph.run_registry import try_lock_run, push_supplement as _push_supplement
    from app.graph.builder import _dag_has_unfinished_work, _looks_like_query, _dag_progress_summary

    # ask 选项卡的答复：必须先于 run 锁判断。executor 正挂起在进程内注册表里等这个
    # 答案（线程池 worker 不能用 langgraph interrupt），submit_answer 直接唤醒它。
    # 答复既不是插话也不是新任务，绝不能落到「启动新 run」路径上。
    if is_resume and str((payload or {}).get("mode") or "") == "ask":
        from app.graph import ask_registry
        _ans = str((payload or {}).get("answer") or "")
        _hit = ask_registry.submit_answer(thread_id, _ans,
                                          str((payload or {}).get("ask_id") or ""))
        if _hit:
            return _ask_ack_sse_response(thread_id, _ans)
        # 注册表里没有待答问题（超时/进程重启已作废）：明确告知，不静默当插话处理
        return JSONResponse({"error": "该问题已超时或已失效，请让任务重新提问"},
                             status_code=409)

    # 执行期插话分流：必须先于 try_lock_run 判断。
    # 若放在锁之后，当 run 锁恰好空闲（上一 run 已退出但 DAG 仍是 executing 的
    # 陈旧状态）时，进度问句会走正常路径启动一个新的 graph run——2026-10-01
    # Test 线程「进度如何了」触发 executor 就是这个坑。
    if not is_resume and _dag_has_unfinished_work(thread_id):
        if _looks_like_query(message):
            _progress = _dag_progress_summary(thread_id)
            if _progress:
                # 进度查询：只读回答当前进度，不建 run、不入队、不改执行状态。
                return _supplement_sse_response(thread_id, message, answer=_progress,
                                                note="（执行期间插话：只读回答，不回写任务）")
        _push_supplement(thread_id, message)
        return _supplement_sse_response(thread_id, message)

    _run_owned = try_lock_run(thread_id)
    if not _run_owned:
        if is_resume:
            # resume（审批续跑）无法入队补充——上一 run 已中断且锁被 finally 释放，
            # 正常不会出现在锁占用中；出现则说明有并发 run，提示稍后再试。
            return JSONResponse({"error": "同一会话已有任务在执行，请稍后再试"}, status_code=409)
        # 锁被占但没有未收敛 DAG：只是普通对话轮次在跑（几秒即结束），
        # 消息入队没人消费会被丢弃——明确拒绝，避免用户消息静默丢失。
        return JSONResponse({"error": "回复仍在进行中，请先等本轮结束或点「⏹ 停止」"}, status_code=409)

    # resume 即审批决策：把 allow/mode 回填到最近一条审批提问记录（重启后可完整回放审批卡）
    if is_resume:
        try:
            from app.memory import MemoryManager as _MM
            _mm0 = _MM(db_path=DB_PATH, thread_id=thread_id)
            _mm0.set_last_approval_decision(
                thread_id,
                bool((payload or {}).get("allow", False)),
                str((payload or {}).get("mode") or ""),
            )
        except Exception:
            pass

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
                # 每用户上下文（Thread 不继承 ContextVar）：DB/RAG/LLM 均按此用户解析
                _u_tok = set_current_user(_user)
                try:
                    _l_tok = set_current_llm(get_user_llm(_user))
                except Exception:
                    _l_tok = None
                # 内部 LLM 输出过滤：update_summary 等节点内的非对话 LLM 调用（摘要生成）也会
                # 被 messages 流以 node='chatbot' 捕获，必须丢弃，否则摘要文本污染聊天框。
                _tok_state = {}
                # 事件监听桥：订阅 store 的 add_event/add_log_entry（与 /api/sse 同一机制），
                # 把"工具执行完成"（ReActLoop 的 on_tool_after → add_event("tool_call")）实时
                # 转成 SSE 的 tool end 事件。chatbot/executor 的工具都在自定义 ReActLoop 里执行，
                # ToolMessage 不会出现在节点 state update 里，updates 分支收不到结束信号，
                # 必须走事件监听通道。
                _listen_q: Queue = Queue()
                _pending_preview = {"text": ""}
                _store._event_listeners.append(_listen_q)

                # 页面事件持久化：把节点/模型调用/工具/思考/审批落 messages 表（kind+meta），
                # 重启后 /api/messages 可回放。一次请求复用一个 MemoryManager。
                _ev_mm = None
                try:
                    from app.memory import MemoryManager as _MM
                    _ev_mm = _MM(db_path=DB_PATH, thread_id=thread_id)
                except Exception:
                    _ev_mm = None

                def _persist_event(kind, content=None, meta=None):
                    """把一条页面事件落库；DB 不可用时静默降级（不阻断对话）。"""
                    if _ev_mm is None:
                        return
                    try:
                        _ev_mm.add_event(thread_id, kind, content, meta)
                    except Exception:
                        pass

                def _drain_listener():
                    """把监听队列里的事件转成 SSE 事件（同一线程，无锁竞争）。"""
                    while True:
                        try:
                            entry = _listen_q.get_nowait()
                        except Exception:
                            break
                        etype = entry.get("type")
                        if etype == "tool_call" and entry.get("thread_id") == thread_id:
                            data = entry.get("data") or {}
                            name = data.get("name", "")
                            params = data.get("params", {})
                            preview = _pending_preview.get("text") or None
                            _pending_preview["text"] = ""
                            if name:
                                sync_q.put({"step": "tool", "name": name,
                                            "args": str(params)[:500],
                                            "params": params,
                                            "result": data.get("result") or preview or "",
                                            "node": data.get("node") or "",
                                            "phase": "end",
                                            "output_preview": data.get("result") or preview or ""})
                                _persist_event("tool_call", content=name,
                                               meta={"name": name, "node": data.get("node") or "",
                                                     "params": params,
                                                     "result": data.get("result") or preview or ""})
                        elif etype == "log" and str(entry.get("message", "")).startswith("工具: "):
                            # on_tool_after 里 add_log_entry 先于 add_event 调用，带 result_preview
                            d = entry.get("data") or {}
                            _pending_preview["text"] = str(d.get("result_preview", ""))[:300]
                        elif etype in ("planner", "executor") and entry.get("thread_id") == thread_id:
                            # DAG 前端事件：planner 事件携带完整图结构（nodes+edges），
                            # executor 事件携带单个节点状态/全量快照。原样透传给前端渲染。
                            _event_data = entry.get("data") or {}
                            if etype == "planner" and _event_data.get("dag"):
                                sync_q.put({"step": "dag_push", "dag": _event_data.get("dag"),
                                            "goal": _event_data.get("goal", "")})
                            elif etype == "executor" and _event_data.get("nodes"):
                                sync_q.put({"step": "node_status",
                                            "node_id": _event_data.get("subtask", ""),
                                            "status": _event_data.get("status", ""),
                                            "goal": _event_data.get("goal", ""),
                                            "nodes": _event_data.get("nodes", [])})
                            elif etype == "executor" and _event_data.get("subtask", "").startswith("replan"):
                                sync_q.put({"step": "replan", "goal": _event_data.get("goal", "")})
                        elif etype in ("node_thought",) and entry.get("thread_id") == thread_id:
                            _t_data = entry.get("data") or {}
                            sync_q.put({"step": "node_thought", "data": _t_data})
                            _persist_event("thought",
                                           content=str(_t_data.get("title") or "思考"),
                                           meta=_t_data)
                        elif etype in ("node_start", "node_end") and entry.get("thread_id") == thread_id:
                            # 节点真实进入/退出时的实时事件（record_node_start/end 桥接），
                            # 与 llm_call 同一通道、按真实时刻排序 → 保证"▶ 开始"先于模型调用气泡。
                            d = entry.get("data") or {}
                            name = d.get("node", "")
                            if name:
                                sync_q.put({"step": "node", "name": name,
                                            "phase": "start" if etype == "node_start" else "end",
                                            "info": {}})
                                _persist_event("node_start" if etype == "node_start" else "node_end",
                                               content=name,
                                               meta={"node": name})
                        elif etype == "llm_call" and entry.get("thread_id") == thread_id:
                            # 每次 LLM 调用的完整输入/输出 → 前端独立"模型调用"气泡
                            d = entry.get("data") or {}
                            sync_q.put({"step": "llm_call", "data": d})
                            _persist_event("llm_call",
                                           content=str(d.get("node") or "llm"),
                                           meta=d)
                        elif etype == "retrieval" and entry.get("thread_id") == thread_id:
                            # L6 知识检索（向量+图谱混合召回）结果 → 前端"知识检索"气泡
                            r = entry.get("data") or {}
                            sync_q.put({"step": "retrieval", "data": r})
                            _persist_event("retrieval",
                                           content=str(r.get("query") or "知识检索")[:200],
                                           meta=r)

                try:
                    for mode, payload in _graph.stream(
                        inputs, config,
                        stream_mode=["updates", "messages"],
                        recursion_limit=40,
                    ):
                        try:
                            # ============ 单条流事件处理（messages / updates 双通道）============
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
                                    # AIMessage（非 chunk，on_llm_end 时 emit）：与流式 chunk 内容重复，
                                    # 忽略以免回复被推两遍。工具调用的完整信息由流式 chunk 的
                                    # tool_call_chunks（name 在首个 chunk、args 为 JSON 增量）提供，
                                    # 无需在此重复提取。
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
                                # 工具调用参数增量也透传（便于前端展示意图）。
                                # 注意：网关把 tool_call 增量以 dict 形态投递（实测 deepseek-v4-pro），
                                # name 只在首个 chunk 出现（含 id），args 是逐段 JSON 片段增量
                                # （如 '{'、'limit'、': 5'…），前端按序拼接即得完整参数。
                                tool_calls = getattr(chunk, "tool_call_chunks", None)
                                if tool_calls:
                                    for tc in tool_calls:
                                        if isinstance(tc, dict):
                                            tc_name = tc.get("name") or None
                                            tc_args = tc.get("args") or ""
                                            tc_index = tc.get("index")
                                        else:
                                            tc_name = getattr(tc, "name", None)
                                            tc_args = getattr(tc, "args", "") or ""
                                            tc_index = getattr(tc, "index", None)
                                        sync_q.put({"step": "tool_chunk", "name": tc_name, "args": str(tc_args)[:200], "index": tc_index, "node": node})
                            else:
                                # updates 模式：payload 是 {node: update_dict}
                                for node, upd in (payload or {}).items():
                                    if node == "__interrupt__":
                                        # 工具审批/ask_user 选项卡：upd 本身就是 interrupt tuple（langgraph 1.x）
                                        try:
                                            int_list = upd if isinstance(upd, (list, tuple)) else (upd.get("__interrupt__") or ())
                                            for int_info in int_list:
                                                val = getattr(int_info, "value", int_info) if not isinstance(int_info, dict) else int_info
                                                q_text = val.get("question", "") if isinstance(val, dict) else str(val)
                                                c_text = val.get("command", "") if isinstance(val, dict) else ""
                                                m_mode = val.get("mode", "per_ask") if isinstance(val, dict) else "per_ask"
                                                sync_q.put({"step": "approval", "data": {
                                                    "question": q_text,
                                                    "command": c_text,
                                                    "mode": m_mode,
                                                    "kind": val.get("kind", "approval") if isinstance(val, dict) else "approval",
                                                    "options": val.get("options", []) if isinstance(val, dict) else [],
                                                    "allow_custom": val.get("allow_custom", True) if isinstance(val, dict) else True,
                                                }})
                                                _persist_event("approval",
                                                               content=(q_text or c_text or "审批请求")[:200],
                                                               meta={"question": q_text, "command": c_text,
                                                                     "mode": m_mode,
                                                                     "kind": val.get("kind", "approval") if isinstance(val, dict) else "approval",
                                                                     "options": val.get("options", []) if isinstance(val, dict) else [],
                                                                     "answer": None})
                                        except Exception as ex:
                                            sync_q.put({"step": "approval", "data": {"question": str(ex), "command": "", "mode": "per_ask"}})
                                        # interrupt 后 graph 暂停，本流到此结束（用户操作后走 /api/chat resume）
                                        break
                                    # 提取节点返回的最终 AIMessage（最可靠的"本轮最终回复"，用于兜底）
                                    # 注：节点"▶ 开始 / ■ 结束"气泡不再在此补发——
                                    # 已由 record_node_start/end 走实时 listener 通道（app/trace.py
                                    # _broadcast_node_event → chat.py 监听桥），保证与 llm_call 真实时序一致。
                                    if isinstance(upd, dict) and isinstance(upd.get("messages"), list):
                                        # 先收集 AIMessage 的 tool_calls 映射（tool_call_id -> 工具名）
                                        call_names = {}
                                        for m in upd["messages"]:
                                            t = m.get("type") if isinstance(m, dict) else getattr(m, "type", "")
                                            if t == "ai" and isinstance(m, dict):
                                                for tc in (m.get("tool_calls") or []):
                                                    # 同时记录 name + args（dict 或对象两种形态兼容）
                                                    _tcid = tc.get("id", "")
                                                    _tcname = tc.get("name", "")
                                                    _tcargs = tc.get("args")
                                                    if isinstance(_tcargs, dict):
                                                        _tcargs = json.dumps(_tcargs, ensure_ascii=False)
                                                    call_names[_tcid] = {"name": _tcname, "args": _tcargs or ""}
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
                                                tc_info = call_names.get(tcid, {"name": "tool", "args": ""})
                                                tc_name = tc_info.get("name", "tool") if isinstance(tc_info, dict) else tc_info
                                                tc_args = tc_info.get("args", "") if isinstance(tc_info, dict) else ""
                                                tc_content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                                                if isinstance(tc_content, list):
                                                    tc_content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in tc_content)
                                                sync_q.put({"step": "tool", "name": tc_name, "args": str(tc_args)[:200], "phase": "end", "output_preview": str(tc_content)[:300]})
                                                # 兜底通道落库（listener 通道未覆盖时）；_persist_event 有 2s 幂等，双写只落一条
                                                _persist_event("tool_call", content=tc_name,
                                                               meta={"name": tc_name, "node": node,
                                                                     "params": tc_args, "result": str(tc_content)[:300]})
                        finally:
                            # 每次流事件后排空监听队列：工具完成事件在两次流 yield 之间到达
                            _drain_listener()
                except Exception as e:
                    sync_q.put({"_err": str(e)})
                finally:
                    # 最后排空一次 + 退订监听（避免队列泄漏到全局监听列表）
                    _drain_listener()
                    if _listen_q in _store._event_listeners:
                        _store._event_listeners.remove(_listen_q)
                    # 保存完整 graph state 快照（调试面板 State tab 展示，含 messages）。
                    # 之前 update_state 从未被调用 → _state_snapshots 恒空 → State 面板 messages 永远为空。
                    try:
                        from app.server import update_state as _upd_state
                        _gs = _graph.get_state(config)
                        _vals = getattr(_gs, "values", None) or {}
                        if isinstance(_vals, dict):
                            _upd_state(dict(_vals))
                    except Exception:
                        pass
                    try:
                        if _l_tok is not None:
                            reset_current_llm(_l_tok)
                        reset_current_user(_u_tok)
                    except Exception:
                        pass
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
            # 本次 run 是这一 thread 的唯一 run（chat_endpoint 里 try_lock_run 占的锁）
            # 到 graph 收敛/异常/被中断收尾时归还，解锁后该 thread 才能正常启动新 run。
            if _run_owned:
                from app.graph.run_registry import release_run
                try:
                    release_run(thread_id)
                except Exception:
                    pass
            # 移除本次 SSE 连接的 listener queue：否则每次刷新页面都会在
            # _store._event_listeners 累计一个无界 Queue，add_event 每次都往里 put
            # （最多 200KB/条的 LLM 全量输入输出），进程内存会涨到 GB 级
            # 而 put_nowait 满后只是 except: pass 静默吞掉，从不删除。
            try:
                _store._event_listeners.remove(_listen_q)
            except ValueError:
                pass

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
async def command_endpoint(payload: dict, request: Request = None):
    """斜杠命令端点：/new, /resume, /threads, /model, /help, /clear, /save, /system 等。
    返回 JSON 结果，UI 负责呈现。
    """
    _is_admin = True
    try:
        _u = getattr(getattr(request, "state", None), "username", None)
        if _u:
            from app.server.accounts import is_admin as _is_admin_fn
            _is_admin = _is_admin_fn(_u)
    except Exception:
        _is_admin = True
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
        # 每用户会话记录：只切换该用户自己的 thread_id（审批模式保留）。
        # 管理员额外写全局（兼容旧器 main.py CLI 行为），普通用户只写自己目录。
        try:
            from app.userctx import set_current_tid as _set_tid
            _set_tid(new_tid, _u or None)
        except Exception:
            pass
        if _is_admin:
            _cfg = dict(_srv_cfg._CONFIG or {})
            _cfg.setdefault("configurable", {})["thread_id"] = new_tid
            _srv_cfg._CONFIG = _cfg
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
        # 同样只切换该用户自己的 thread_id
        try:
            from app.userctx import set_current_tid as _set_tid
            _set_tid(arg, _u or None)
        except Exception:
            pass
        if _is_admin:
            _cfg = dict(_srv_cfg._CONFIG or {})
            _cfg.setdefault("configurable", {})["thread_id"] = arg
            _srv_cfg._CONFIG = _cfg
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
        if not _is_admin:
            return {"error": "审批模式仅管理员可修改"}
        try:
            from app.userctx import get_approval_mode as _get_mode, set_approval_mode as _set_mode
        except Exception:
            _get_mode = None
            _set_mode = None
        if not arg:
            cur = (_get_mode(_u or None) if _get_mode else "session_allow")
            return {"result": f"当前审批模式(用户 {_u}): {cur}"}
        if arg not in ("per_ask", "session_allow", "always_allow"):
            return {"error": f"未知模式: {arg}"}
        if _set_mode:
            _set_mode(arg, _u or None)
        return {"result": f"已切到 {arg}（仅当前用户生效）"}
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
        # 用 .get() 汇总，避免 store 侧增删计数键时这里再抛 KeyError
        # （历史上曾因 store 去掉 'tasks' 键而让本接口 500）
        detail = " ".join(f"{k}={deleted.get(k, 0)}"
                          for k in ("messages", "summaries", "commands", "checkpoints"))
        return {"result": f"已删除会话 {arg}（{detail}）", "deleted": deleted}
    return {"error": f"未知命令: /{name}，输入 /help 查看列表"}


async def _delete_thread(thread_id: str) -> dict:
    """删除某 thread 的所有数据（memory.db + checkpoints.db）。逻辑在 store 公共函数。"""
    from app.server.store import delete_thread_records
    return delete_thread_records(thread_id)


def _safe(obj):
    """把不可序列化的对象转成可序列化形式。"""
    try:
        return json.loads(json.dumps(obj, default=str, ensure_ascii=False))
    except Exception:
        return {"_raw": str(obj)}


# ==================== 定时任务调度器 ====================

