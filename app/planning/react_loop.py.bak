import json, re, time
from typing import List, Dict, Any, Callable, Optional
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage
from langgraph.types import Command
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from openai import RateLimitError
from app.server import add_log_entry, add_event


def _llm_msg_to_text(m):
    """把单条 message 序列化为可读文本（content 可能为 str/list/None）。"""
    role = getattr(m, "type", None) or (m.get("type") if isinstance(m, dict) else "?")
    raw = None
    if isinstance(m, dict):
        raw = m.get("content")
    else:
        raw = getattr(m, "content", "")
    if isinstance(raw, list):
        parts = []
        for x in raw:
            if isinstance(x, dict):
                parts.append(str(x.get("text", "") or ""))
            else:
                parts.append(str(x))
        content = "".join(parts)
    else:
        content = str(raw or "")
    # 补充工具调用信息，便于展示"模型调了什么工具"
    tool_calls = None
    if isinstance(m, dict):
        tool_calls = m.get("tool_calls")
    else:
        tool_calls = getattr(m, "tool_calls", None)
    if tool_calls:
        try:
            content += ("\n[tool_calls] " + json.dumps(
                [{"name": tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", ""),
                  "args": (tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})) or {}}
                 for tc in tool_calls], ensure_ascii=False))
        except Exception:
            pass
    return f"--- {role} ---\n{content}\n"


def _llm_messages_to_text(messages):
    """把所有输入消息序列化为一段完整文本（含 system/user/history），供前端独立气泡展示。"""
    try:
        return "\n".join(_llm_msg_to_text(m) for m in messages)
    except Exception:
        return ""


# 连续无进展轮数上限：超过此值仍未产生任何 tool_calls → 强制 break 并给 fail_node 引导。
# - executor 节点（require_final_marker=True）走这条路径。设 3 平衡"给模型补救机会"和"别空转"。
# - chatbot 节点不依赖这个计数器（有 apply_reply_guard 兜底）。
_NO_PROGRESS_LIMIT = 3

# ---- 长会话上下文窗口管理 ----
# agnes-flash 在超大上下文（实测约 14.6 万字符 → 对应几十万 token）下输出劣化：
# 表现为发空参/缺参工具调用（write_file params={}、execute_command params={}）。
# 对策（源头治理）：
#   1) 每条工具结果写回上下文前截断（_bounded_tool_result），防止单轮就把窗口刷爆；
#   2) 每轮调模型前压缩中间历史（_compact_window），只保留系统区 + 最近几轮。
_MAX_WINDOW_CHARS = 60_000          # 累计字符数超过则压缩中间历史
_KEEP_TAIL_MSGS = 18                # 保留最近 N 条消息（当前轮 + 最近执行轮）
_MAX_TOOL_RESULT_CHARS = 6_000      # 单条工具结果保留上限（防上下文被结果刷爆）


class ReActLoop:
    def __init__(self, llm_with_tools, max_iterations: int = 15, node: str = "agent",
                 require_final_marker: bool = False, terminate_tools: set = None):
        self.llm_with_tools = llm_with_tools
        self.max_iterations = max_iterations
        # executor 等执行器节点（require_final_marker=True）：纯文本（无工具调用）永远不算完成，
        # 会被引导继续直到有实际动作（调用 write_file / 终止工具）或轮次耗尽。
        # 工业方案：不依赖模型输出 "FINAL_ANSWER:" 文本标记判定完成。
        self._require_final_marker = require_final_marker
        # 结构化终止工具（如 complete_subtask / fail_subtask）：调用后立即结束循环，
        # 由 executor 读取工具写出的结构化结果（_outcome）判定子任务状态。
        self._terminate_tools = terminate_tools or set()
        # 工具返回的 pending_plan（chatbot 节点用它判断是否要跳到 planner）
        self._captured_pending_plan = None
        # trace：LLM 调用归属的节点名 + 会话 id
        self._trace_node = node
        self._trace_thread_id = None
        # 纪律：连续被拒工具计数（ReActLoop 层强制终止）
        self._reject_count = 0
        # 已成功执行的 (工具名|规范化参数)：用于拦截"同一条命令反复执行"的浪费/死循环
        self._done_cmds = set()

    def run(self, messages, tools, on_tool_before=None, on_tool_after=None,
            interrupt_handler=None, state=None, on_before_llm=None, verbose=True):
        # trace 上下文：thread_id + 当前节点名（chatbot/executor）
        if state:
            tid = state.get("thread_id") or state.get("_thread_id")
            if tid:
                self._trace_thread_id = tid
        self._trace_node = getattr(self, "_trace_node", None) or "agent"
        final_answer = None
        iteration = 0
        last_tool_calls = None
        same_call_count = 0
        # 连续无进展计数器：每轮若既没产生有效 tool_calls 也没产生新写入，记一次。
        # 达到 _NO_PROGRESS_LIMIT（按节点类型区分）→ 强制 break，避免空转/死循环。
        # 历史教训：executor 节点曾因模型反复空 output 但不调 complete_node/fail_node，
        # 把 max_iterations 跑满也收不了尾（dag_executor.py:79 _run_node 拿到
        # "[自动判定] 未调用..." 才最终收尾，但 LLM 调用次数已经爆炸）。
        no_progress_streak = 0
        # 「继续执行」引导：最多注入 3 轮逐步升级（_should_force_continue 检测一次后即进入强推循环）
        self._nudge_used = 0
        # 收尾标记：轮次是否因耗尽退出 / 最终答复是否来自纯文本（用于"不静默收尾"诚实汇报）
        exhausted = False
        final_from_text_only = False
        _terminated = False

        while iteration < self.max_iterations:
            iteration += 1
            if verbose and iteration % 3 == 0:
                print(f"[ReAct] 第 {iteration}/{self.max_iterations} 轮")

            current_messages = messages
            if on_before_llm:
                current_messages = on_before_llm(messages)
            # 长会话上下文压缩：只影响本次模型调用的窗口，不动外部 messages
            current_messages = self._compact_window(current_messages)

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
                if self._require_final_marker:
                    # executor 等执行器节点：纯文本（无工具调用）不算完成——
                    # 完成必须有实际动作：调用 write_file 写入产出，或调用
                    # complete_subtask/fail_subtask 声明结果。
                    # 连续 _NO_PROGRESS_LIMIT 轮仍无 tool_calls → 注入"立即 fail_node"
                    # 引导并强制 break，不再 continue 空转（曾经跑到 max_iterations 才收尾）。
                    no_progress_streak += 1
                    if no_progress_streak >= _NO_PROGRESS_LIMIT:
                        messages.append(response)
                        messages.append(SystemMessage(
                            content=(
                                f"[强制收尾] 你已连续 {no_progress_streak} 轮没有调用任何工具。"
                                f"为避免无限空转，请立即调用 fail_node 声明失败（reason 写明卡在哪），"
                                f"或调用 complete_node 列出已写文件结束本子任务；不再继续。"
                            )
                        ))
                        final_answer = (
                            f"[强制收尾] 连续 {no_progress_streak} 轮无工具调用，"
                            f"子任务无法推进（模型空转未调用 complete_node/fail_node）。"
                        )
                        break
                    # 前几轮：继续注入引导，期望模型下一轮产生动作
                    messages.append(response)
                    messages.append(SystemMessage(
                        content="[引导] 你刚才只输出了文字，没有任何实际动作。请调用 write_file 工具把完整文件"
                                "写入 deliverables 目录（一次写完整），或调用 complete_subtask 声明完成 / fail_subtask 声明失败。"
                    ))
                    continue
                # 空回复（无 tool_calls 且无正文）多为瞬时网关抖动 → 快速重试一次；
                # 仍空则交给 _extract_final_answer 记录并兜底（连续异常由 builder 层 apply_reply_guard 熔断）。
                if not self._response_has_text(response):
                    try:
                        response = self._invoke_llm(current_messages)
                        tool_calls = self._extract_tool_calls(response)
                    except Exception:
                        pass  # 重试失败：保留原空响应，走统一兜底
                if not tool_calls:
                    # 「继续执行」引导（状态驱动，不靠关键词）：
                    # 用户每发一条消息 = 工具调用计数/预算重置（chatbot 每回合新建 ReActLoop）+
                    # 若会话正处于未完成的工具工作态 → 强制续作。触发只看历史状态
                    # （上一轮在干工具活 / 干到一半被打断 / 纯问答不打扰），与用户这条
                    # 消息写了什么（好的/继续/调用工具呀…）无关。
                    # 最多升 3 级：第 1 步检测到工作态后，后续 2 步持续强推（纯文本不得作为最终答复混过去）。
                    if self._nudge_used < 3 and (self._nudge_used > 0 or (iteration == 1 and self._should_force_continue(messages))):
                        self._nudge_used += 1
                        _guide = self._nudge_guide(self._nudge_used)
                        messages.append(response)
                        messages.append(SystemMessage(content=_guide))
                        print(f"[ReAct] 检测到“继续执行”意图，注入强制工具执行引导（第 {self._nudge_used}/3 级）", flush=True)
                        continue
                    final_answer = self._extract_final_answer(response)
                    final_from_text_only = True
                    break

            current_calls = [(tc.get("name"), tc.get("args", {}) or {}) for tc in tool_calls]

            # 已成功执行过的同参调用（如同一生成命令被反复发起）→ 不再执行，
            # 提示模型基于已有结果直接收尾，避免"能出图却不收敛"的循环。
            repeated_done = []
            for _n, _a in current_calls:
                _key = f"{_n}|{json.dumps(_a, sort_keys=True, ensure_ascii=False)}"
                if _key in self._done_cmds:
                    repeated_done.append(_n)
            if repeated_done:
                messages.append(SystemMessage(
                    content="[防重复执行] 你正在重复执行一条已成功完成过的调用（结果已在上文给出）："
                            + ", ".join(sorted(set(repeated_done)))
                            + "。请直接使用已有结果给用户最终答复并结束本轮；"
                              "若确有新需求（如再生成一张不同的），请修改参数（prompt/size/seed）后重新发起，不要原样重复执行。"
                ))
                print("⚠️ 检测到已成功命令被再次执行，拦截并引导收尾", flush=True)
                continue

            # 检测连续相同工具调用（原逻辑，args 保留字符串形式供比对）
            current_calls_str = [(n, str(a)) for n, a in current_calls]
            if current_calls_str == last_tool_calls:
                same_call_count += 1
                if same_call_count >= 3:
                    print("⚠️ 连续3次相同工具调用，终止循环并报告信息不足")
                    final_answer = (
                        "任务未能继续完成：连续 3 次发起了完全相同的工具调用，未产生任何推进，"
                        "循环已安全终止。常见原因：工具参数不完整（如 write_file 只带 path 没带 content）、"
                        "或模型在长会话下输出劣化。请修正参数后重试，或改用技能脚本（execute_command）生成目标文件。"
                    )
                    break
                if same_call_count >= 2:
                    # 第二次发起完全相同的工具调用（参数逐字一致）：多半是模型空转/没消化结果。
                    # 不再执行该轮重复调用（避免白跑一次 + 再塞一大坨重复结果进上下文），
                    # 改为注入"防死循环"引导，让模型要么换动作、要么直接收尾。
                    dup = "; ".join(f"{n}({str(a)[:100]})" for n, a in current_calls_str)
                    messages.append(SystemMessage(
                        content="[防死循环] 你刚发起了与上一轮完全相同的工具调用：" + dup +
                                "。该调用并未推进任务。请立即采取行动：若所需信息已掌握，"
                                "就调用真正完成任务的工具（如 execute_command / 写文件），或直接给出最终回答；"
                                "不要再次重复相同调用。"
                    ))
                    print(f"⚠️ 检测到重复工具调用（第 {same_call_count} 次），注入防死循环引导")
                    continue
            else:
                same_call_count = 0
                last_tool_calls = current_calls_str

            # OpenAI 兼容 API 要求：assistant 消息带 tool_calls 时，必须为每个 tool_call_id
            # 都有对应 ToolMessage。用一个标志保证 response 只 append 一次，
            # 被拦截/被取消/成功的工具都补 ToolMessage，避免"孤儿 tool_calls"导致 400。
            response_appended = False
            _terminated = False

            for tc in tool_calls:
                tool_name = tc.get("name")
                params = self._normalize_tool_args(tc.get("args", {}) or {})
                tool_id = tc.get("id", "unknown")

                # 1) 安全策略拦截：on_tool_before 返回 (allow, reason)
                #    - True/False 旧形态也兼容
                #    - (False, "...") 新形态：把 reason 写进 ToolMessage 让 LLM 知道怎么改
                _gate = on_tool_before(tool_name, params) if on_tool_before else None
                if _gate is False or (isinstance(_gate, tuple) and _gate[0] is False):
                    _reason = _gate[1] if isinstance(_gate, tuple) and len(_gate) > 1 else "[安全策略] 该工具调用被拒绝执行"
                    if not response_appended:
                        messages.append(response)
                        response_appended = True
                    messages.append(ToolMessage(content=_reason, tool_call_id=tool_id))
                    # 连续被拒 3 次 → 强制终止（LLM 反复尝试违规工具 = 纪律失控）
                    self._reject_count = getattr(self, "_reject_count", 0) + 1
                    if self._reject_count >= 3:
                        final_answer = (
                            f"[纪律] 连续 3 次工具调用被拒绝。"
                            f"最近一次被拒原因: {_reason}"
                            f"请调整参数后重试或调用 fail_node 声明无法完成。"
                        )
                        break
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

                # 结构化终止工具（complete_subtask / fail_subtask）：调用后立即结束 ReAct 循环，
                # 子任务最终状态由 executor 读取工具写出的结构化结果决定（系统观测，非文本约定）。
                if tool_name in self._terminate_tools:
                    final_answer = f"[{tool_name}] " + (result if isinstance(result, str) else str(result))
                    _terminated = True
                    break

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
                            "final_answer": "",  # 不显示过渡消息，直接让后续节点处理
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
                    # 成功标志检测用原始完整结果（截断可能切掉 returncode/URL 字段）
                    try:
                        if '"returncode": 0' in result_str or 'URL=' in result_str:
                            self._done_cmds.add(
                                f"{tool_name}|{json.dumps(params, sort_keys=True, ensure_ascii=False)}")
                    except Exception:
                        pass
                    # 写回上下文的工具结果做上限截断（防上下文被单条结果刷爆）
                    messages.append(ToolMessage(
                        content=self._bounded_tool_result(result_str), tool_call_id=tool_id))
                    # write_file/edit_file 连续缺 content 升级引导：模型反复只带 path/空参时，
                    # 是闪档模型在长会话下的典型输出劣化（content 没进工具参数）。
                    # 第 2 次起注入决定性方案，避免空转到「连续 3 次相同调用」被一刀切终止。
                    if tool_name in ("write_file", "edit_file") and "缺少必填参数" in result_str and "content" in result_str:
                        self._wf_missing_content = getattr(self, "_wf_missing_content", 0) + 1
                        if self._wf_missing_content >= 2:
                            messages.append(SystemMessage(
                                content=(
                                    "[写文件参数修复] 你已连续多次发起 write_file 但参数里始终缺 content，"
                                    "这些调用都未执行成功。请按顺序选一种可立即执行的方式继续，"
                                    "禁止再次发起只有 path 或空参数的 write_file：\n"
                                    "1) 重新调用 write_file，把 path 和完整 content（JSON string）都放进工具参数；\n"
                                    "2) 直接调用 execute_command 运行对应技能脚本生成文件"
                                    "（如角色卡：cd /home/mirror/Agnes-Agent && node app/skills/novel-characters/scripts/novel-characters.mjs <按该技能 SKILL.md 的必填参数>），"
                                    "脚本会自己写盘，不需要手工拼完整 JSON；\n"
                                    "3) 把内容拆成 2–3 个小文件分别 write_file（每个 content 控制在 2KB 内），"
                                    "再用 execute_command 的 node 单行脚本把碎片合并成目标文件；\n"
                                    "4) 若确实无法继续，调用 fail_subtask / complete_node 声明，并写明阻塞原因。"
                                )
                            ))
                            print("⚠️ write_file 连续缺 content，注入写文件参数修复引导", flush=True)
                    else:
                        # 非缺参错误/正常完成 → 重置计数（成功一次即视为模型学会）
                        self._wf_missing_content = 0

            # 纪律强制终止：连续被拒 ≥3 次 → 退出整个 ReAct 循环（保留纪律消息，不再覆盖）
            if getattr(self, "_reject_count", 0) >= 3:
                break
            # 结构化终止工具已调用 → 退出整个 ReAct 循环
            if _terminated:
                break

        # —— 收尾：#2 轮次耗尽不再静默收尾 ——
        # 历史教训（20:22:55 大轮刚好吃满 24 次）：max_iterations 耗尽时若 final_answer 为空，
        # 旧代码直接 _force_answer("请基于已有信息直接回答")，模型会自编「由于工具调用限制」的
        # 假完成报告，把 episodes 未合并进 outline.json 说成"成功"。这里强制换措辞——
        # 告知模型：轮次已用尽，若产物未完成/校验未通过，必须在最终答复里如实写明，
        # 不得宣称已完成。
        if final_answer is None:
            if iteration >= self.max_iterations:
                exhausted = True
                print(f"[ReAct] ⚠️ 工具轮次上限({self.max_iterations})已耗尽，强制收尾并要求如实汇报", flush=True)
                try:
                    add_log_entry("warning",
                                  f"chatbot 工具轮次上限({self.max_iterations})已耗尽",
                                  {"node": self._trace_node})
                except Exception:
                    pass
                messages.append(SystemMessage(
                    content=(
                        "[轮次上限已用尽] 你已达本回合工具调用次数上限。"
                        "若当前任务尚未实际完成——例如关键产物（如 outline.json / episodes）缺失、"
                        "validate 校验未通过、环节之间存在未合并的中间产物——请在最终答复里明确写出"
                        "『未完成』的原因与缺失项，并列出已完成的文件路径，不要宣称全部完成。"
                        "用户看到未完成后会继续追问，届时工具预算会随新回合重置。"
                    )
                ))
            final_answer = self._force_answer(messages)
        elif final_from_text_only and iteration >= self.max_iterations:
            # 纯文本收尾恰好撞上轮次上限：上面那轮可能被当最终答复兜住（如"由于工具调用限制"），
            # 但产物未必完整。为避免静默放行，追加一轮强制自检：让模型复核 key 产物是否齐备。
            exhausted = True
            print(f"[ReAct] ⚠️ 纯文本收尾且轮次已达上限({self.max_iterations})，追加产物完整性自检", flush=True)
            try:
                add_log_entry("warning",
                              "纯文本收尾且工具轮次已达上限，追加产物完整性自检",
                              {"node": self._trace_node})
            except Exception:
                pass
            messages.append(SystemMessage(
                content=(
                    "[收尾自检] 你的上一条答复是本回合的最终答复，但本回合工具调用次数恰好用尽。"
                    "请先复核本轮关键产物是否真实存在且内容完整（如有 outline.json / 各分集文件，"
                    "确认 episodes 数量与集数一致），再重写最终答复：若一切齐备就正常汇报各文件路径；"
                    "若有缺失/未合并/未过校验，必须明确写出『未完成』项，不要以口头承诺代替实际产物。"
                )
            ))
            try:
                response = self._invoke_llm(messages)
                final_answer = re.sub(r'<tool_call>.*?</tool_call>', '', response.content, flags=re.DOTALL).strip()
            except Exception:
                pass  # 自检失败则保留原有最终答复
        return {
            "final_answer": final_answer,
            "iteration_count": iteration,
            "pending_plan": getattr(self, "_captured_pending_plan", None),
            "rounds_exhausted": exhausted,
        }

    def _invoke_llm(self, messages):
        # 检测 langgraph GraphInterrupt：不要让 retry 吞掉它，必须冒泡到 graph 层
        # 让 main.py 的审批循环能看到 interrupt
        from langgraph.errors import GraphInterrupt
        # trace：记录本次 LLM 调用的输入输出（thread_id 从调用方 state 传入，这里用 on_before 的兜底）
        thread_id = getattr(self, "_trace_thread_id", None) or "default"
        _t0 = time.time()
        @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=15, min=15, max=60),
               retry=retry_if_exception(lambda e: not isinstance(e, (RateLimitError, GraphInterrupt))))
        def _wrapper():
            return self.llm_with_tools.invoke(messages)
        try:
            resp = _wrapper()
            from app.trace import record_llm
            record_llm(thread_id, getattr(self, "_trace_node", "llm"),
                       messages, resp, duration_ms=(time.time() - _t0) * 1000,
                       model=getattr(getattr(self, "llm_with_tools", None), "model_name", "") or "")
            # 把本次 LLM 调用的完整输入/输出推到前端，作为独立气泡展示
            try:
                add_event("llm_call", {
                    "node": getattr(self, "_trace_node", "llm"),
                    "input": _llm_messages_to_text(messages),
                    "output": _llm_messages_to_text([resp]),
                    "duration_ms": int((time.time() - _t0) * 1000),
                }, thread_id)
            except Exception:
                pass
            return resp
        except Exception as e:
            from app.trace import record_error
            record_error(thread_id, getattr(self, "_trace_node", "llm"), e)
            raise

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
        """把文本里 XML 形态的工具调用解析成结构化 tool_calls。

        模型/gateway 未走结构化 tool_calls 时，常以两类文本格式回工具调用：
          <invoke name="write_file"><parameter name="path">…</parameter><parameter name="content">…</parameter></invoke>
          <tool_call><function=write_file><parameter=path>…</parameter><parameter=content>…</parameter></tool_call>
        重要：一次调用里所有 <parameter> 块必须全部抓取（旧实现只抓第一个，
        导致 write_file 的 content 丢失 → pydantic “Field required” 校验错误）。
        """
        tool_calls = []

        def _mk(name, body):
            return {
                "name": str(name).strip(),
                "args": self._parse_xml_parameters(body),
                "id": f"manual_{int(time.time())}_{len(tool_calls)}",
            }

        # 1) <invoke name="工具">…<parameter name="k">v</parameter>…</invoke>
        for name, body in re.findall(
            r'<invoke\s+name\s*=\s*["\']?([^"\'<>]+)["\']?\s*>(.*?)</invoke>',
            content, re.DOTALL | re.IGNORECASE,
        ):
            tool_calls.append(_mk(name, body))
        # 2) <tool_call>…<function=工具>…<parameter=path>…<parameter=content>…</tool_call>
        #    函数名在 body 内；所有参数块都要抓（旧实现只取第一个 → content 丢失）。
        if not tool_calls:
            for body in re.findall(r'<tool_call\b[^>]*>(.*?)</tool_call>', content, re.DOTALL | re.IGNORECASE):
                fn = re.search(r'function\s*=\s*["\']?([^"\'<>\s]+)["\']?', body)
                if fn:
                    tool_calls.append(_mk(fn.group(1), body))
        return tool_calls

    @staticmethod
    def _parse_xml_parameters(body):
        """抓取一段文本里所有 <parameter …>value</parameter> 块（name= / name=".." 两种写法）。"""
        params = {}
        for pname, pvalue in re.findall(
            r'<parameter(?:\s+name)?\s*=\s*["\']?([^"\'<>]+)["\']?\s*>\s*(.*?)\s*</parameter>',
            body, re.DOTALL | re.IGNORECASE,
        ):
            params[pname.strip()] = pvalue
        return params

    @staticmethod
    def _normalize_tool_args(params):
        """把各 gateway 形态的工具参数归一成 dict：
        dict 原样返回；{"arguments"/"args"/"input": "…"} 包裹结构解包；
        JSON 字符串解包；其余兜底为 {}。"""
        if isinstance(params, dict):
            if params.keys() & {"arguments", "args", "input"} and len(params) <= 2:
                for k in ("arguments", "args", "input"):
                    v = params.get(k)
                    if isinstance(v, str):
                        try:
                            parsed = json.loads(v)
                            if isinstance(parsed, dict):
                                return parsed
                        except Exception:
                            pass
            return params
        if isinstance(params, str):
            try:
                parsed = json.loads(params.strip())
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    def _response_has_text(self, response):
        """判断响应是否含正文（content 可能为 str / list(多模态) / None）。"""
        raw = getattr(response, "content", "")
        if isinstance(raw, list):
            return any(str((x.get("text", "") if isinstance(x, dict) else x) or "").strip() for x in raw)
        return bool(str(raw or "").strip())

    def _should_force_continue(self, messages):
        """「继续执行」判定——纯状态驱动，不匹配任何关键词（对齐用户要求）：
        用户发送消息 → 工具调用计数/预算重置（每轮新 ReActLoop 天然重置）。
        只要会话正处于未完成的工具工作态，就在本回合一开场强制续作：
          a) 最近一条 assistant 消息带 tool_calls（上轮正干工具活，被预算/中断切断）→ True
          b) 最近一条 assistant 是纯文本，但其后可见的工具痕迹（ToolMessage / 带 tool_calls 的
             assistant）存在于最近一两个回合（上轮"说了一堆但不动手"）→ True
          c) 纯问答（最近几轮无任何工具痕迹）→ False（不打扰正常聊天）
        """
        # 找到当前用户消息（回放末尾的 human）
        idx = len(messages) - 1
        while idx >= 0 and getattr(messages[idx], "type", "") != "human":
            idx -= 1
        if idx < 0:
            return False
        # 再往前找最近的 assistant
        idx -= 1
        while idx >= 0 and getattr(messages[idx], "type", "") != "ai":
            idx -= 1
        if idx < 0:
            return False
        prev = messages[idx]
        if getattr(prev, "tool_calls", None):
            return True
        # 最近一条 assistant 是纯文本：向前看最近的工具痕迹（最多再扫 ~2 个用户回合的跨度）
        scan = 0
        while idx >= 0 and scan < 40:
            m = messages[idx]
            t = getattr(m, "type", "") or (m.get("type") if isinstance(m, dict) else "")
            if t == "human":
                scan += 15  # 每跨一个用户回合加权重，最多约 2.5 个回合
            if t == "tool" or getattr(m, "tool_calls", None):
                return True
            idx -= 1
        return False

    def _nudge_guide(self, level: int) -> str:
        """「继续执行」引导文案：分 3 级升级，逐级更强硬。"""
        if level >= 3:
            return (
                "[继续执行·最后提醒] 你已经连续多轮只输出文字而没有调用任何工具。"
                "本回合的工作必须通过真实工具动作才能推进（read_file 定位目标文件 / execute_command 运行技能脚本复用结果 / write_file 写入或修正 / 再校验）。"
                "现在请直接调用至少一个工具开始执行，不要再输出计划或承诺；"
                "若确实无法继续（比如缺少关键输入），请明确说出阻塞原因并结束。"
            )
        if level >= 2:
            return (
                "[继续执行] 上一条你仍然只输出了文字而没有调用工具。"
                "用户催促的工作默认必须产生真实工具动作（read_file / execute_command / write_file 等）才算完成。"
                "请不要输出计划或口头承诺，直接调用工具执行；若确实需要用户提供额外信息，请调用交互工具询问并说明原因。"
            )
        return (
            "[继续执行] 会话正处于未完成的工具工作态（上一轮你在干工具活/只说了没动手），"
            "用户已发来新消息继续本任务。不要再只输出口头承诺：立即通过工具实际操作来推进——"
            "read_file 定位当前未完成的目标文件 → execute_command 重新运行技能脚本 → write_file 写入/修正 → 再执行校验直到通过。"
            "全部完成后，用 2–3 句话汇报每个产物文件的路径。"
            "例外：若你判断当前确实不需要任何工具操作（例如用户只是在致谢/闲聊），可给出简短说明并直接结束本回合。"
        )

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
        if not content:
            # 空回复：不再静默兜底为"任务完成"（会误导用户，掩盖网关额度耗尽/模型配置错误等真实原因），
            # 改为明确提示并在 trace 记录 error，便于排查。
            print("[LLM] 模型返回空内容（无 tool_calls），请检查模型配置/API 额度", flush=True)
            try:
                from app.trace import record_error
                record_error(
                    getattr(self, "_trace_thread_id", None) or "default",
                    getattr(self, "_trace_node", "llm"),
                    ValueError("模型返回空内容（无 tool_calls）"),
                )
            except Exception:
                pass
            return "[模型未返回内容] 请检查模型配置或 API 额度（网关可能已耗尽配额或返回空响应）。"
        return content

    def _bounded_tool_result(self, s: str) -> str:
        """截断过长的工具结果（保头保尾），防止单轮工具输出刷爆上下文。"""
        if len(s) <= _MAX_TOOL_RESULT_CHARS:
            return s
        cut_head = _MAX_TOOL_RESULT_CHARS - 1200
        return (
            s[:cut_head]
            + f"\n…[结果过长，原 {len(s)} 字符，已截断为 {_MAX_TOOL_RESULT_CHARS} 字符；"
              f"如需要完整内容，请用技能脚本把结果写盘后再读取文件]…\n"
            + s[-1200:]
        )

    def _compact_window(self, messages):
        """长会话上下文压缩：累计超过 _MAX_WINDOW_CHARS 时，丢弃中间旧消息，
        只保留【开头系统区】+【最近 _KEEP_TAIL_MSGS 条】，并插入一条压缩说明。

        只作用于本次模型调用的输入窗口（调用处 current_messages），外部 messages
        保持原样（trace / 日志 / 最终答复提取不受影响）。
        根因：agnes-flash 在约 14 万字符上下文下输出劣化（空参工具调用），
        压缩窗口让模型始终工作在可控上下文内。

        配对安全：本循环中每条带 tool_calls 的 assistant 消息后紧跟其全部
        ToolMessage（连续 append）。因此裁剪边界如恰好落在 ToolMessage 上，
        会被保留的工具结果就会失去所属的 tool_calls（OpenAI 兼容 API 会 400）。
        对策：边界前移若干位，保证保留段起点不是 tool 消息（即为普通 text /
        system / assistant-tool_calls 的起点，其后缀工具结果都随之保留）。
        """
        if not messages:
            return messages
        try:
            total = sum(len(str(getattr(m, "content", "") or "")) for m in messages)
        except Exception:
            return messages
        if total <= _MAX_WINDOW_CHARS or len(messages) <= _KEEP_TAIL_MSGS + 2:
            return messages
        # 头部系统区：开头的连续 SystemMessage（系统提示 + 记忆注入）
        head = []
        i = 0
        while i < len(messages) and getattr(messages[i], "type", "") == "system":
            head.append(messages[i])
            i += 1
        # 最近保留段：从尾部取 _KEEP_TAIL_MSGS 条，边界对齐（起点不能是 tool 消息）
        tail_start = len(messages) - _KEEP_TAIL_MSGS
        if tail_start < 0:
            tail_start = 0
        while tail_start > 0 and getattr(messages[tail_start], "type", "") == "tool":
            tail_start -= 1
        # 若起点仍是一条带 tool_calls 的 assistant 消息，其工具结果在其后已保留；安全
        tail = list(messages[tail_start:])
        dropped = tail_start - len(head)
        if dropped <= 2:
            return messages
        kept = head + tail
        dropped_chars = max(0, total - sum(len(str(getattr(m, "content", "") or "")) for m in kept))
        summary = SystemMessage(
            content=(
                f"[上下文压缩] 为避免长会话上下文膨胀导致模型输出劣化，已移除中间"
                f" {dropped} 条较早的消息（约 {dropped_chars} 字符，多为早期对话与工具执行结果）。"
                f"任务目标与最近几轮内容保持不变，请基于现有信息继续，不要假设被移除内容仍可引用。"
            )
        )
        return head + [summary] + tail

    def _execute_tool(self, tool_name, params, tools, state, tool_id=None):
        # 参数完整性前置检查：模型/gateway 偶尔只带 path 漏 content（或完全空参数），
        # 提前给出可操作的报错，让模型下一轮针对性补全，而不是等到 pydantic 校验才失败。
        if tool_name in ("write_file", "edit_file"):
            params = self._normalize_tool_args(params or {})
            missing = [k for k in ("path", "content") if not str((params or {}).get(k) or "").strip()]
            if missing:
                return (
                    f"工具参数错误: {tool_name} 缺少必填参数 {', '.join(missing)}。"
                    f"{tool_name} 需要 path（目标文件路径）和 content（文件完整内容，作为 JSON string 放进工具参数，"
                    f"不能写在消息正文里）。若内容太大一次写不全，请改用 execute_command 运行对应技能脚本生成文件，"
                    f"或在收尾前分块写入。"
                )
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
                    msg = str(e)
                    if "validation error" in msg.lower() or "field required" in msg.lower():
                        return (
                            f"工具参数错误: {tool_name} 的参数校验未通过（{msg[:200]}）。"
                            f"请重新发起该调用并补全全部必填参数。"
                        )
                    return f"工具执行错误: {msg}"
        return f"未知工具: {tool_name}"

    def _force_answer(self, messages):
        messages.append(SystemMessage(content="请基于已有信息直接回答"))
        try:
            response = self._invoke_llm(messages)
            return re.sub(r'<tool_call>.*?</tool_call>', '', response.content, flags=re.DOTALL).strip() or "[模型未返回内容] 请检查模型配置或 API 额度。"
        except Exception as e:
            return f"强制回答失败: {e}"