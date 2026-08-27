from dotenv import load_dotenv
load_dotenv()
import platform
import os
import sqlite3
import json
import tiktoken
import logging
import time
import re
from datetime import datetime
from typing import List, Dict, Set, Any
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import RunnableConfig, interrupt, Command
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from openai import RateLimitError

from app.graph.state import State, TaskPlan
from app.graph.utils import (count_tokens, retry_llm_call, parse_tool_calls_from_content,
    ensure_tool_calls, compress_messages, ensure_token_limit, sync_state_to_db, load_prompt_template,
    MODEL_CONTEXT_LIMIT, TOKEN_LIMIT, KEEP_RECENT, MAX_TOOL_CALL_ROUNDS)
from app.tools import tools, request_planning
from app.llm import create_llm
from app.memory import MemoryManager
from app.planning.planner import create_planner_node
from app.planning.executor import create_executor_node
from app.planning.summarizer import create_summarizer_node
from app.planning.validator import create_validator_node
from app.planning.react_loop import ReActLoop
from app.server import update_state, update_prompt, add_log_entry, add_event

# ---------- 配置 ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("openai_compatible")

from app.config import DB_PATH, CHECKPOINT_DB_PATH, PROMPT_TEMPLATE_PATH

# 模块级 LLM 实例（供 chatbot / planner / executor 使用）
_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compatible")
_LLM_MODEL = os.getenv("LLM_MODEL") or "deepseek-v4-pro"
llm = create_llm(provider=_LLM_PROVIDER, model=_LLM_MODEL)
llm_with_tools = llm.bind_tools(tools)
print(f"LLM 类型: {type(llm)} | provider={_LLM_PROVIDER} model={_LLM_MODEL or '默认'}")

# ============================================================
# Chatbot 节点（统一入口）
# 取代旧版 L1/L2/L3 硬切分。模型在每一轮自主决定：
#   - 直接文本回复（无 tool_call）
#   - 调用一个或多个工具（update_user_info / tavily_tool / system_command / ...）
#   - 调用 request_planning → 触发跳转 planner 节点
# ============================================================

def chatbot(state: State, config: RunnableConfig):
    thread_id = config["configurable"]["thread_id"]
    state["thread_id"] = thread_id

    if not state.get("approval_mode"):
        state["approval_mode"] = "per_ask"

    if not state.get("messages"):
        return {"messages": []}

    last_msg = state["messages"][-1]
    user_content = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)

    mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)
    context = mm.build_context()
    state["profile"] = context["profile"]
    state["preferences"] = context["preferences"]
    state["recent_summary"] = context.get("recent_summary")

    # 记录用户消息到长期记忆
    mm.add_message(thread_id, "user", user_content)

    # 构造 system prompt
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_name = platform.system()
    os_cmds = "ls, find, head, cat" if os_name != "Windows" else "dir, type, echo"
    if os_name == "Windows":
        os_cmds += "\n注意：Windows 控制台默认编码为 GBK，如读取中文文件出现乱码，请先执行 'chcp 65001' 切换为 UTF-8。"

    profile_section = "\n".join([f"{k}: {v}" for k, v in state["profile"].items()]) if state["profile"] else ""
    preferences_section = "\n".join([f"{k}: {v}" for k, v in state["preferences"].items()]) if state["preferences"] else ""
    summary_section = state.get("recent_summary", "")
    task_plan_section = ""
    if state.get("task_plan") and state["task_plan"].status in ("planning", "executing"):
        task_plan_section = f"【当前任务计划】目标：{state['task_plan'].goal}\n进度：\n" + "\n".join([f"  - [{sub.id}] {sub.description} {sub.status}" for sub in state['task_plan'].subtasks])

    # ===== 5 层记忆注入（L2 / L3 / L4）=====
    # 每轮自动把"用户画像 + 近期任务 + 近期命令"塞进 system prompt，
    # 模型无需主动调工具即可"自然记住"用户。
    memory_injection = mm.build_memory_injection(thread_id, layers=["L2", "L3"])
    memory_section = ""
    if memory_injection:
        memory_section = "\n\n=== 分层记忆注入 ===\n" + "\n\n".join(memory_injection.values())

    cwd = os.getcwd()
    deliverables_dir = os.path.join(cwd, "deliverables")
    os.makedirs(deliverables_dir, exist_ok=True)

    template = load_prompt_template()
    system_text = template.replace("{{os}}", os_name).replace("{{os_cmds}}", os_cmds).replace("{{time}}", current_time)
    system_text = system_text.replace("{{cwd}}", cwd).replace("{{deliverables_dir}}", deliverables_dir)
    system_text = system_text.replace("{{profile_section}}", f"用户个人信息：\n{profile_section}\n" if profile_section else "")
    system_text = system_text.replace("{{preferences_section}}", f"用户偏好：\n{preferences_section}\n" if preferences_section else "")
    system_text = system_text.replace("{{summary_section}}", f"对话摘要：\n{summary_section}\n" if summary_section else "")
    system_text = system_text.replace("{{task_plan_section}}", task_plan_section)
    # 5 层记忆的注入（L2/L3/L4）追加到 system prompt 末尾
    if memory_section:
        system_text = system_text + "\n" + memory_section

    update_prompt(system_text)
    update_state(state)

    # 工具调用前的安全检查
    def on_tool_before(name, params):
        if name == "system_command":
            cmd = params.get('command', '')
            for d in ['rm -rf /', 'dd ', 'mkfs', 'format', 'shutdown']:
                if d in cmd:
                    print(f"⚠️ [安全] 阻止: {cmd}")
                    return False
        return True

    # 工具调用后的副作用写入
    def on_tool_after(name, params, result):
        try:
            if name == "update_user_preference":
                data = json.loads(params.get("info_json", "{}"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if v is not None:
                            mm.set_preference(k, v)
            elif name == "update_user_info":
                data = json.loads(params.get("info_json", "{}"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if v is not None:
                            mm.set_profile(k, v)
            elif name == "system_command":
                # 写 L4 命令历史
                cmd = params.get("command", "")
                success = not str(result).startswith("执行失败") and not str(result).startswith("工具执行错误")
                mm.add_command_history(
                    thread_id, cmd, success=success,
                    stdout_preview=str(result)[:2000] if result else "",
                )
            elif name == "tavily_search":
                # 写 L5 语义缓存（tavily_tool 的注册名是 tavily_search）
                query = params.get("query") or params.get("q") or json.dumps(params, ensure_ascii=False)[:200]
                mm.cache_knowledge(source="tavily", query=query, content=str(result)[:8000])
        except Exception as e:
            logger.error(f"写入失败: {e}")
        add_log_entry("info", f"工具: {name}", {"params": params, "result_preview": str(result)[:200]})
        add_event("tool_call", {"name": name, "params": params})

    # 审批 hook：仅 system_command 走人工审批；其他工具直接放行
    def interrupt_handler(name, params):
        if name == "system_command":
            cmd = params.get('command', '')
            mode = state.get("approval_mode", "per_ask")
            if mode == "per_ask":
                resp = interrupt({"question": f"执行命令？\n命令: {cmd}", "command": cmd, "mode": mode})
                allow = resp.get("allow", False)
                new_mode = resp.get("mode")
                if new_mode and new_mode in ["per_ask", "session_allow", "always_allow"]:
                    state["approval_mode"] = new_mode
                    print(f"[审批模式] {new_mode}")
                return allow, None
        return True, None

    def on_before_llm(msgs):
        return ensure_token_limit(msgs, system_text, thread_id)

    history_messages = state["messages"][-KEEP_RECENT:]
    initial_messages = ensure_token_limit([SystemMessage(content=system_text)] + history_messages, system_text, thread_id)

    print(f"[Chatbot] 自决模式（无 L1/L2/L3 硬切）")
    loop = ReActLoop(llm_with_tools, max_iterations=MAX_TOOL_CALL_ROUNDS)
    try:
        result = loop.run(
            messages=initial_messages,
            tools=tools,
            on_tool_before=on_tool_before,
            on_tool_after=on_tool_after,
            interrupt_handler=interrupt_handler,
            state=state,
            on_before_llm=on_before_llm,
        )
        content = result["final_answer"]
    except Exception as e:
        # langgraph 的 GraphInterrupt 必须冒泡，让 main.py 的审批循环能处理
        from langgraph.errors import GraphInterrupt
        if isinstance(e, GraphInterrupt):
            raise
        logger.error(f"Chatbot 异常: {e}")
        content = f"执行错误: {e}"

    content = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()
    content = re.sub(r'<tool_calls>.*?</tool_calls>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()
    if not content:
        content = "已处理完毕。"

    # 摘要更新
    if len(state["messages"]) > 0:
        try:
            new_summary = mm.update_summary(thread_id, state.get("recent_summary", "无"), state["messages"][-5:], llm)
            mm.save_summary(thread_id, new_summary)
            state["recent_summary"] = new_summary
        except Exception as e:
            pass

    mm.add_message(thread_id, "assistant", content)
    sync_state_to_db(state, mm)

    print(f"✅ [Chatbot] 回答长度: {len(content)}")
    add_log_entry("success", f"回答完成: {len(content)} 字符")
    # 关键：request_planning 工具把 pending_plan 写进了局部 state，
    # 必须随 return 传回 graph state，route_after_chatbot 才能据此跳转 planner。
    # 同时 thread_id 也要传回（planner/executor 需要真实 thread_id 而非 "default"）。
    ret = {"messages": [AIMessage(content=content)], "thread_id": thread_id}
    if state.get("pending_plan"):
        ret["pending_plan"] = state["pending_plan"]
    return ret


# ============================================================
# 路由函数
# 取代旧版 route_after_router，改为 chatbot 自决 + 节点间条件路由
# ============================================================

def route_after_chatbot(state: State) -> str:
    """
    chatbot 退出后判断下一步：
      - 如果有 pending_plan → 进入 planner
      - 如果有未完成的 task_plan → 进入 executor 继续执行
      - 否则 END
    """
    if state.get("pending_plan"):
        return "planner"
    plan = state.get("task_plan")
    if plan and plan.status in ("planning", "executing"):
        return "executor"
    return END


# ============================================================
# 路由：executor / validator / summarizer 之间的条件边（保留旧逻辑）
# ============================================================

def route_after_executor(state: State):
    plan = state.get("task_plan")
    if plan:
        failed = any(s.status == "failed" for s in plan.subtasks)
        if failed:
            fail_count = state.get("_executor_fail_count", 0)
            if fail_count >= 2:
                print("↩️ 重规划（失败次数过多）")
                return "planner"
            state["_executor_fail_count"] = fail_count + 1
            return "executor"
        if all(s.status == "done" for s in plan.subtasks):
            print("✅ 进入验证")
            return "validator"
        return "executor"
    return "executor"


def route_after_validator(state: State):
    if state.get("_validation_passed", False):
        return "summarizer"
    print("↩️ 验证失败，重规划")
    return "planner"


def route_after_summarizer(state: State):
    """summarizer 完成后清空 pending_plan，再回到 chatbot 让用户继续对话。"""
    return "chatbot"


# ============================================================
# Build Graph
# ============================================================

def build_graph():
    builder = StateGraph(State)

    # planner_node 需要做特殊包装：从 state["pending_plan"] 读 goal
    planner_inner = create_planner_node(llm)

    def planner_node(state: State):
        # 把 pending_plan 包装为最后一条 human message 供 planner 使用，
        # 同时清空 pending_plan 避免下次重入
        if state.get("pending_plan"):
            from langchain_core.messages import HumanMessage
            state["messages"] = list(state["messages"]) + [HumanMessage(content=state["pending_plan"])]
            state["pending_plan"] = None
        return planner_inner(state)

    builder.add_node("chatbot", chatbot)
    builder.add_node("planner", planner_node)
    builder.add_node("executor", create_executor_node(llm, tools))
    builder.add_node("validator", create_validator_node())
    builder.add_node("summarizer", create_summarizer_node(llm))

    builder.add_edge(START, "chatbot")
    # chatbot 退出后按 state 决定下一步
    builder.add_conditional_edges("chatbot", route_after_chatbot, {
        "planner": "planner",
        "executor": "executor",
        END: END,
    })
    # planner 之后总是进入 executor
    builder.add_edge("planner", "executor")
    # executor 退出后根据子任务状态决定
    builder.add_conditional_edges("executor", route_after_executor, {
        "executor": "executor", "planner": "planner", "validator": "validator"
    })
    # validator 决定通过 or 重规划
    builder.add_conditional_edges("validator", route_after_validator, {
        "summarizer": "summarizer", "planner": "planner"
    })
    # summarizer 完成后回到 chatbot 让用户继续
    builder.add_edge("summarizer", "chatbot")

    conn = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(checkpointer=checkpointer)
