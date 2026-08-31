import json, re, time
from typing import List, Dict, Any, Callable, Optional
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage
from langgraph.types import Command
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from openai import RateLimitError
from app.server import add_log_entry, add_event

class ReActLoop:
    def __init__(self, llm_with_tools, max_iterations: int = 15):
        self.llm_with_tools = llm_with_tools
        self.max_iterations = max_iterations
        # 工具返回的 pending_plan（chatbot 节点用它判断是否要跳到 planner）
        self._captured_pending_plan = None

    def run(self, messages, tools, on_tool_before=None, on_tool_after=None,
            interrupt_handler=None, state=None, on_before_llm=None, verbose=True):
        final_answer = None
        iteration = 0
        last_tool_calls = None
        same_call_count = 0

        while iteration < self.max_iterations:
            iteration += 1
            if verbose and iteration % 3 == 0:
                print(f"[ReAct] 第 {iteration}/{self.max_iterations} 轮")

            current_messages = messages
            if on_before_llm:
                current_messages = on_before_llm(messages)

            try:
                response = self._invoke_llm(current_messages)
            except Exception as e:
                # GraphInterrupt 必须冒泡，不当作普通异常
                from langgraph.errors import GraphInterrupt
                if isinstance(e, GraphInterrupt):
                    raise
                # 从 tenacity RetryError 里挖出原始异常（含 HTTP 状态和 message）
                cause = e
                from tenacity import RetryError as _RetryError
                if isinstance(e, _RetryError) and e.last_attempt and e.last_attempt.exception():
                    cause = e.last_attempt.exception()
                # 估算当前消息 token 数（排查上下文超长）
                try:
                    import tiktoken
                    enc = tiktoken.get_encoding("cl100k_base")
                    tok = sum(len(enc.encode(str(getattr(m, 'content', '') or ''))) for m in current_messages)
                    detail = f" | 当前消息约 {tok} tokens"
                except Exception:
                    detail = ""
                print(f"[LLM ERROR] {type(cause).__name__}: {cause}{detail}", flush=True)
                final_answer = f"LLM 调用失败: {type(cause).__name__}: {str(cause)[:300]}{detail}"
                break

            tool_calls = self._extract_tool_calls(response)

            if not tool_calls:
                final_answer = self._extract_final_answer(response)
                break

            # 检测连续相同工具调用
            current_calls = [(tc.get("name"), str(tc.get("args", {}))) for tc in tool_calls]
            if current_calls == last_tool_calls:
                same_call_count += 1
                if same_call_count >= 3:
                    print("⚠️ 连续3次相同工具调用，终止循环并报告信息不足")
                    final_answer = "无法获取有效信息，请尝试更具体的查询或稍后再试。"
                    break
            else:
                same_call_count = 0
                last_tool_calls = current_calls

            # OpenAI 兼容 API 要求：assistant 消息带 tool_calls 时，必须为每个 tool_call_id
            # 都有对应 ToolMessage。用一个标志保证 response 只 append 一次，
            # 被拦截/被取消/成功的工具都补 ToolMessage，避免"孤儿 tool_calls"导致 400。
            response_appended = False

            for tc in tool_calls:
                tool_name = tc.get("name")
                params = tc.get("args", {})
                tool_id = tc.get("id", "unknown")

                # 1) 安全策略拦截：补 ToolMessage（拒绝原因），response 不重复 append
                if on_tool_before and not on_tool_before(tool_name, params):
                    if not response_appended:
                        messages.append(response)
                        response_appended = True
                    messages.append(ToolMessage(content="[安全策略] 该工具调用被拒绝执行", tool_call_id=tool_id))
                    continue

                # 2) 审批：interrupt 由 langgraph 挂起（GraphInterrupt 冒泡），
                #    若 interrupt_handler 返回拒绝（如自定义 handler），补"用户取消"
                allow = True
                if interrupt_handler:
                    allow, _ = interrupt_handler(tool_name, params)
                    if not allow:
                        if not response_appended:
                            messages.append(response)
                            response_appended = True
                        messages.append(ToolMessage(content="用户取消", tool_call_id=tool_id))
                        continue

                result = self._execute_tool(tool_name, params, tools, state, tool_id=tool_id)
                if on_tool_after:
                    on_tool_after(tool_name, params, result)

                # 捕获工具返回的 pending_plan（如 request_planning），让 chatbot 节点 return 时
                # 写入 ret["pending_plan"] → route_after_chatbot 据此跳到 planner。
                # 原因：chatbot 节点不用 LangGraph 的 ToolNode，工具的 Command(update=...) 不会
                # 自动合并到 state，必须手动提取。
                if isinstance(result, Command) and getattr(result, "update", None):
                    _pp = result.update.get("pending_plan")
                    if _pp:
                        # 用实例属性累积（ReActLoop 局部 state）
                        if not hasattr(self, "_captured_pending_plan") or not self._captured_pending_plan:
                            self._captured_pending_plan = _pp

                if isinstance(result, Command):
                    # 序列要求：AIMessage(tool_calls) 必须先于 ToolMessage。
                    # Command 通常自带 ToolMessage（带 tool_call_id），不再重复 append success，
                    # 避免同一个 tool_call_id 出现两个 ToolMessage 导致 400。
                    if not response_appended:
                        messages.append(response)
                        response_appended = True
                    has_tool_msg = False
                    if result.update:
                        for key, value in result.update.items():
                            if key == "messages":
                                for msg in value:
                                    messages.append(msg)
                                    if getattr(msg, "tool_call_id", None) == tool_id:
                                        has_tool_msg = True
                            elif state is not None:
                                state[key] = value
                    if not has_tool_msg:
                        messages.append(ToolMessage(content=json.dumps({"status": "success"}), tool_call_id=tool_id))
                    # 关键：工具返回 pending_plan（如 request_planning）后，规划请求已提交，
                    # 应立即终止 ReAct 循环，避免模型继续调工具/重复规划。
                    # 不读 state["pending_plan"]（chatbot 节点不用 ToolNode，Command.update 不会
                    # 合并到 state）；改读 self._captured_pending_plan（在 _execute_tool 后捕获）。
                    if self._captured_pending_plan:
                        return {
                            "final_answer": "已提交规划请求，进入规划流程。",
                            "iteration_count": iteration,
                            "pending_plan": self._captured_pending_plan,
                        }
                else:
                    # 优先用 str()，因为 tavily 等工具可能返回 Document / dict 等非 JSON 可序列化对象
                    if isinstance(result, str):
                        result_str = result
                    else:
                        try:
                            result_str = json.dumps(result, ensure_ascii=False, default=str)
                        except Exception as e:
                            result_str = str(result)
                    if not response_appended:
                        messages.append(response)
                        response_appended = True
                    messages.append(ToolMessage(content=result_str, tool_call_id=tool_id))

        if final_answer is None:
            final_answer = self._force_answer(messages)
        return {
            "final_answer": final_answer,
            "iteration_count": iteration,
            "pending_plan": getattr(self, "_captured_pending_plan", None),
        }

    def _invoke_llm(self, messages):
        # 检测 langgraph GraphInterrupt：不要让 retry 吞掉它，必须冒泡到 graph 层
        # 让 main.py 的审批循环能看到 interrupt
        from langgraph.errors import GraphInterrupt
        @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10),
               retry=retry_if_exception(lambda e: not isinstance(e, (RateLimitError, GraphInterrupt))))
        def _wrapper():
            return self.llm_with_tools.invoke(messages)
        return _wrapper()

    def _extract_tool_calls(self, response):
        if hasattr(response, 'tool_calls') and response.tool_calls:
            return response.tool_calls
        content = getattr(response, 'content', '')
        if isinstance(content, list):
            # 多模态 content 数组 → 只取 text 拼成字符串
            content = "".join(str(x.get("text", "") or "") if isinstance(x, dict) else str(x) for x in content)
        content = str(content or "")
        if "<tool_calls>" in content or "<tool_call>" in content or "<invoke" in content:
            return self._parse_xml_tool_calls(content)
        return []

    def _parse_xml_tool_calls(self, content):
        tool_calls = []
        pattern = r'<invoke\s+name=["\']?([^"\'<>]+)["\']?\s*>(.*?)</invoke>'
        for name, params_xml in re.findall(pattern, content, re.DOTALL | re.IGNORECASE):
            param_pattern = r'<parameter\s+name=["\']?([^"\'<>]+)["\']?\s*>(.*?)</parameter>'
            params = {pname.strip(): pvalue.strip() for pname, pvalue in re.findall(param_pattern, params_xml, re.DOTALL | re.IGNORECASE)}
            tool_calls.append({"name": name.strip(), "args": params, "id": f"manual_{int(time.time())}_{len(tool_calls)}"})
        if not tool_calls:
            short = r'<tool_call>.*?<function=([^>]+)>.*?<parameter=([^>]+)>(.*?)</parameter>.*?</tool_call>'
            for name, pname, pvalue in re.findall(short, content, re.DOTALL | re.IGNORECASE):
                tool_calls.append({"name": name.strip(), "args": {pname.strip(): pvalue.strip()}, "id": f"manual_{int(time.time())}_{len(tool_calls)}"})
        return tool_calls

    def _extract_final_answer(self, response):
        # content 可能是 str / list（多模态）/ None，统一转成 str 再处理，避免 TypeError / 空回退
        raw = getattr(response, "content", "")
        if isinstance(raw, list):
            parts = []
            for x in raw:
                if isinstance(x, dict):
                    parts.append(str(x.get("text", "") or ""))
                else:
                    parts.append(str(x))
            content = "".join(parts).strip()
        else:
            content = str(raw or "").strip()
        if "FINAL_ANSWER:" in content:
            return content.split("FINAL_ANSWER:", 1)[1].strip()
        return content if content else "任务完成"

    def _execute_tool(self, tool_name, params, tools, state, tool_id=None):
        for tool in tools:
            if tool.name == tool_name:
                try:
                    # 统一用完整 ToolCall 格式调用：langchain 对所有工具都支持该格式，
                    # 且带 InjectedToolCallId 的工具（request_planning / update_user_*）必须用它，
                    # 否则会抛 "must be invoked with a full model ToolCall" 错误。
                    if tool_id:
                        return tool.invoke({
                            'args': params,
                            'name': tool_name,
                            'type': 'tool_call',
                            'id': tool_id,  # LangChain ToolCall 用 id 字段（非 tool_call_id）
                        })
                    return tool.invoke(params)
                except Exception as e:
                    return f"工具执行错误: {str(e)}"
        return f"未知工具: {tool_name}"

    def _force_answer(self, messages):
        messages.append(SystemMessage(content="请基于已有信息直接回答"))
        try:
            response = self._invoke_llm(messages)
            return re.sub(r'<tool_call>.*?</tool_call>', '', response.content, flags=re.DOTALL).strip() or "已处理"
        except Exception as e:
            return f"强制回答失败: {e}"