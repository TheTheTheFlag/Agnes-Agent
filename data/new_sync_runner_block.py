            def sync_runner():
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
                                            "args": str(params)[:200], "phase": "end",
                                            "output_preview": preview})
                        elif etype == "log" and str(entry.get("message", "")).startswith("工具: "):
                            # on_tool_after 里 add_log_entry 先于 add_event 调用，带 result_preview
                            d = entry.get("data") or {}
                            _pending_preview["text"] = str(d.get("result_preview", ""))[:300]

                try:
                    for mode, payload in _srv_cfg._GRAPH.stream(
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
                                    sync_q.put({"step": "node", "name": node, "phase": "end"})
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
                    sync_q.put(None)  # 哨兵
