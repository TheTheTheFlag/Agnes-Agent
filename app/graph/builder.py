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

from app.graph.state import State
from app.graph.utils import (count_tokens, retry_llm_call, parse_tool_calls_from_content,
    ensure_tool_calls, ensure_token_limit, sync_state_to_db, load_prompt_template,
    MODEL_CONTEXT_LIMIT, TOKEN_LIMIT, KEEP_RECENT, MAX_TOOL_CALL_ROUNDS, _tool_params_summary,
    prepare_context_messages, apply_reply_guard)
from app.tools import tools, request_planning
from app.llm import create_llm
from app.memory import MemoryManager
from app.planning.dag_planner import create_dag_planner_node
from app.planning.dag_executor import create_executor
from app.planning.dag_summarizer import create_dag_summarizer
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
    # trace：chatbot 节点进入
    try:
        from app.trace import record_node_start
        record_node_start(thread_id, "chatbot")
    except Exception:
        pass

    if not state.get("messages"):
        # 保持 trace 对称：节点进入即有开始，无消息直接结束也记录结束
        try:
            from app.trace import record_node_end
            record_node_end(thread_id, "chatbot", "无消息")
        except Exception:
            pass
        return {"messages": []}

    last_msg = state["messages"][-1]
    user_content = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)

    mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)
    # 注：用户画像/偏好(L2) 与 历史对话摘要(history_summary) 均不在 chatbot 里单独注入，
    # 统一由下方 build_memory_injection 拼进 system prompt 末尾的"=== 分层记忆注入 ==="块，
    # 避免同一份数据在 prompt 里出现两次。

    # 记录用户消息到长期记忆
    mm.add_message(thread_id, "user", user_content)

    # 构造 system prompt
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_name = platform.system()
    os_cmds = "ls, find, head, cat" if os_name != "Windows" else "dir, type, echo"
    if os_name == "Windows":
        os_cmds += "\n注意：Windows 控制台默认编码为 GBK，如读取中文文件出现乱码，请先执行 'chcp 65001' 切换为 UTF-8。"

    # ===== 5 层记忆注入（L2 / L3）=====
    # 每轮自动把"用户画像/偏好 + 历史摘要 + 近期任务"塞进 system prompt，
    # 模型无需主动调工具即可"自然记住"用户。
    # L2 从 memory_injection 注入（以【用户画像】/【用户偏好】形式显示在"分层记忆注入"块内），
    # 不再通过 {{profile_section}} 占位符重复注入。
    memory_injection = mm.build_memory_injection(thread_id, layers=["history_summary", "L2", "L3"])
    memory_section = ""
    if memory_injection:
        memory_section = "\n\n=== 分层记忆注入 ===\n" + "\n\n".join(memory_injection.values())

    cwd = os.getcwd()
    deliverables_dir = os.path.join(cwd, "deliverables")
    os.makedirs(deliverables_dir, exist_ok=True)

    template = load_prompt_template()
    system_text = template.replace("{{os}}", os_name).replace("{{os_cmds}}", os_cmds).replace("{{time}}", current_time)
    system_text = system_text.replace("{{cwd}}", cwd).replace("{{deliverables_dir}}", deliverables_dir)
    # 技能路由元数据：动态读取 app/skills/ 下所有 SKILL.md（实时，新增技能无需重启）
    try:
        from app.skills.loader import load_all_skills as _load_all_skills
        _local_skills = _load_all_skills()
        if _local_skills:
            _skill_lines = []
            for _s in _local_skills:
                _trig = (f"（触发词: {', '.join(_s['triggers'][:6])}）") if _s.get("triggers") else ""
                _skill_lines.append(f"- {_s['name']}: {_s.get('description') or ''}{_trig}")
            _skills_section = "本地已安装技能（用户任务匹配技能时，read_skill 后按其步骤执行）：\n" + "\n".join(_skill_lines)
        else:
            _skills_section = "本地暂无技能。"
        system_text = system_text.replace("{{skills_section}}", _skills_section)
    except Exception:
        system_text = system_text.replace("{{skills_section}}", "本地暂无技能。")
    # 5 层记忆的注入（L2/L3/L4）追加到 system prompt 末尾
    if memory_section:
        system_text = system_text + "\n" + memory_section

    update_prompt(system_text, thread_id)
    # state 不缓存业务数据（profile/preferences/task_plan 等都已从 DB 查）
    state["thread_id"] = thread_id

    # 需要人工审批的工具：命令执行 + 文件写/改/删
    _APPROVAL_TOOLS = ("system_command", "execute_command", "write_file", "edit_file", "delete_file")

    # 工具调用前的安全检查
    def on_tool_before(name, params):
        if name in _APPROVAL_TOOLS:
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
            elif name in ("system_command", "execute_command"):
                # 写 L4 命令历史（shell 命令）
                cmd = params.get("command", "")
                success = not str(result).startswith("执行失败") and not str(result).startswith("工具执行错误")
                mm.add_command_history(
                    thread_id, cmd, success=success,
                    stdout_preview=str(result)[:2000] if result else "",
                )
            elif name in ("write_file", "edit_file", "delete_file"):
                # 文件写/改/删也记入操作历史（command 字段存操作摘要，便于审查）
                op = {
                    "write_file": "写入文件",
                    "edit_file": "编辑文件",
                    "delete_file": "删除文件",
                }.get(name, name)
                target = params.get("path", "")
                success = not str(result).startswith("执行失败") and not str(result).startswith("工具执行错误")
                mm.add_command_history(
                    thread_id, f"{op}: {target}", success=success,
                    stdout_preview=str(result)[:2000] if result else "",
                )
            elif name == "tavily_search":
                # 写 L5 语义缓存（tavily_tool 的注册名是 tavily_search）
                query = params.get("query") or params.get("q") or json.dumps(params, ensure_ascii=False)[:200]
                mm.cache_knowledge(source="tavily", query=query, content=str(result)[:8000])
        except Exception as e:
            logger.error(f"写入失败: {e}")
        # 审计：所有工具调用都记入操作历史（便于完整审查 Agent 行为）
        try:
            if name not in ("update_user_preference", "update_user_info", "tavily_search"):
                # 已在上方分支记录的命令/文件操作不再重复；其余工具（ls/read/glob/grep/memory 等）补记
                summary = _tool_params_summary(name, params)
                success = not str(result).startswith("执行失败") and not str(result).startswith("工具执行错误")
                mm.add_command_history(thread_id, f"{name}: {summary}", success=success,
                                       stdout_preview=str(result)[:500])
        except Exception:
            pass
        add_log_entry("info", f"工具: {name}", {"params": params, "result_preview": str(result)[:200]})
        add_event("tool_call", {"name": name, "params": params, "result": str(result)}, thread_id)
        # trace：chatbot 节点工具调用
        try:
            from app.trace import record_tool
            record_tool(thread_id, "chatbot", name, params, result)
        except Exception:
            pass

    # 审批 hook：命令执行 + 文件写/改/删走人工审批；其余工具直接放行
    def interrupt_handler(name, params):
        if name in _APPROVAL_TOOLS:
            # 工具不同，"命令"字段不一样：system_command 用 command，文件操作用 path
            cmd = params.get('command') or params.get('path') or ''
            if name in ("write_file", "edit_file", "delete_file") and params.get('path'):
                cmd = f"{name}: {params['path']}"
                if params.get('content'):
                    cmd += f" ({len(str(params['content']))} 字符)"
            elif name == "system_command" and params.get('command'):
                cmd = params['command']
            # 审批模式来源（优先级）：
            #   1) 用户实时点按钮：写到 _srv_cfg._CONFIG（跨进程生效）
            #   2) 默认：session_allow（本次会话内不重复审批；每次启动 Agent 时重置为这个）
            try:
                from app.server import config as _srv_cfg
                mode = (_srv_cfg._CONFIG or {}).get("configurable", {}).get("approval_mode") or "session_allow"
            except Exception:
                mode = "session_allow"
            if mode == "per_ask":
                resp = interrupt({"question": f"执行命令？\n命令: {cmd}", "command": cmd, "mode": mode})
                allow = resp.get("allow", False)
                new_mode = resp.get("mode")
                if new_mode and new_mode in ["per_ask", "session_allow", "always_allow"]:
                    # 写进程级配置（跨节点/跨轮持久化，无需 state 缓存）
                    try:
                        from app.server import config as _srv_cfg
                        if _srv_cfg._CONFIG is None:
                            _srv_cfg._CONFIG = {"configurable": {}}
                        _srv_cfg._CONFIG.setdefault("configurable", {})["approval_mode"] = new_mode
                    except Exception:
                        pass
                    print(f"[审批模式] {new_mode}")
                if not allow:
                    # 被拒的操作也记入历史，便于审查"模型想做什么但被拒绝了"
                    try:
                        desc = params.get('command') or params.get('path') or ''
                        mm.add_command_history(
                            thread_id, f"{name}: {desc}", success=False,
                            stdout_preview="[用户拒绝]",
                        )
                    except Exception:
                        pass
                return allow, None
        return True, None

    def on_before_llm(msgs):
        return ensure_token_limit(msgs, system_text, thread_id)

    history_messages = state["messages"]
    # 业界做法组装上下文：退化尾巴清理 + 结构清洗 + 按完整回合裁剪（不拆散 assistant↔ToolMessage），
    # 修复"坏会话因孤儿 tool 消息 / 满屏兜底文案而持续空返回"的问题。
    initial_messages = prepare_context_messages(history_messages, system_text, keep_recent=KEEP_RECENT)

    print(f"[Chatbot] 自决模式（无 L1/L2/L3 硬切）")
    loop = ReActLoop(llm_with_tools, max_iterations=MAX_TOOL_CALL_ROUNDS, node="chatbot")
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
        # 透传 ReActLoop 捕获的 pending_plan（来自 request_planning 工具的 Command.update）
        # → 让 route_after_chatbot 跳到 planner
        react_pending_plan = result.get("pending_plan")
    except Exception as e:
        # langgraph 的 GraphInterrupt 必须冒泡，让 main.py 的审批循环能处理
        from langgraph.errors import GraphInterrupt
        if isinstance(e, GraphInterrupt):
            raise
        logger.error(f"Chatbot 异常: {e}")
        content = f"执行错误: {e}"

    content = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()
    content = re.sub(r'<tool_calls>.*?</tool_calls>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()
    # 不再设置默认内容"已处理完毕。"，让空内容直接触发规划跳转

    # 检测规划触发（两种来源，结果等价）：
    # 1) ReActLoop 从工具返回的 Command 中捕获的 pending_plan（最可靠，工具直接告诉 state）
    # 2) 消息流检测：找 ToolMessage("已接收规划请求，目标：...") 提取 goal（兜底）
    triggered_goal = react_pending_plan
    if not triggered_goal:
        # 仅在 DAG 状态允许时检查历史消息中的规划请求
        # 防止"用户说继续"时重复触发规划（此时 chat历史中有 request_planning 的 ToolMessage，但任务已在执行中）
        try:
            from app.planning.dag_storage import DAGStorage
            _dag = DAGStorage(DB_PATH)
            _existing_plan = _dag.get_plan_by_thread(thread_id)
            _has_active_plan = _existing_plan and _existing_plan["status"] in ("planning", "executing")
        except Exception:
            _has_active_plan = False
        if not _has_active_plan:
            for m in reversed(state.get("messages", [])[-5:]):
                if hasattr(m, 'type') and m.type == 'tool' and '已接收规划请求' in (m.content or ''):
                    try:
                        triggered_goal = m.content.split('目标：', 1)[1].strip()
                    except Exception:
                        pass
                    break
    # 不再显示过渡消息"🚀 正在为你规划并执行"，直接让后续节点处理

    # history_summary 压缩（context engineering，对齐业界做法）：
    #   - 触发：只看"实际发送预算"(TOKEN_LIMIT) 的占比，**不再用 120 秒时间兜底**
    #     （旧阈值用 MODEL_CONTEXT_LIMIT×0.8 = 419K，比工作上限 TOKEN_LIMIT(367K) 还高，
    #      条件永不成立，实际退化成"每 120 秒必压一次"，短对话被反复压缩 3 次调用）
    #   - 对象：只压 messages[:-KEEP_RECENT]，最近 30 条保留原文
    #   - 代价：单次 LLM 调用（旧实现要 3 次：逐条打分 → 摘要 → 再给摘要评分）
    #   - 位置：**后台线程**执行，不占用用户等待的同步路径
    try:
        from app.memory.compaction import compact_in_background
        if compact_in_background(thread_id, state["messages"], llm, DB_PATH):
            add_log_entry("info", "已触发后台历史压缩（不阻塞本轮回复）")
    except Exception:
        pass

    # 连续异常回复熔断：异常兜底文案首次照常落库、第二次替换为提示、其后跳过写入，
    # 避免"同一句兜底文案无限刷屏"（历史上最后几十条全被同一句占满 → 模型持续空返回 → 再刷屏）。
    content, _skip_reply = apply_reply_guard(state["messages"], content)
    if _skip_reply:
        add_log_entry("warning", "模型连续多轮未正常回复，已跳过本轮写入（防刷屏）")
        try:
            from app.trace import record_node_end
            record_node_end(thread_id, "chatbot", "[已跳过：连续异常回复]")
        except Exception:
            pass
        return {"messages": [], "thread_id": thread_id}

    mm.add_message(thread_id, "assistant", content)
    sync_state_to_db(state, mm)

    print(f"✅ [Chatbot] 回答长度: {len(content)}")
    add_log_entry("success", f"回答完成: {len(content)} 字符")
    # 关键：request_planning 工具把 pending_plan 写进了局部 state，
    # 必须随 return 传回 graph state，route_after_chatbot 才能据此跳转 planner。
    # 同时 thread_id 也要传回（planner/executor 需要真实 thread_id 而非 "default"）。
    ret = {"messages": [AIMessage(content=content)], "thread_id": thread_id}
    # trace：chatbot 节点退出
    try:
        from app.trace import record_node_end
        record_node_end(thread_id, "chatbot", content[:200])
    except Exception:
        pass
    # 触发了规划 → 让 route_after_chatbot 跳到 planner（基于消息检测判断，与上面 content 替换同一来源）
    if triggered_goal:
        ret["pending_plan"] = triggered_goal
    # approval_mode 存 _srv_cfg._CONFIG（不再写 state）
    # 自动 git 版本快照：Agent 本轮改动（代码/交付物）提交入库
    try:
        from app.server.git_ops import auto_snapshot
        _snap = auto_snapshot(f"对话：{user_content[:40]}")
        if _snap.get("commit"):
            add_log_entry("info", f"git 快照: {_snap['commit']}")
    except Exception:
        pass
    return ret


# ============================================================
# 路由函数
# 取代旧版 route_after_router，改为 chatbot 自决 + 节点间条件路由
# ============================================================

def route_after_chatbot(state: State) -> str:
    """
    chatbot 退出后判断下一步：
      - 如果有 pending_plan → 进入 planner
      - 如果 DAG 存储里有未完成的任务计划 → 进入 executor 继续执行
      - 否则 END
    """
    if state.get("pending_plan"):
        return "planner"
    # DAG 唯一真相源：按 thread 查进行中计划，看是否有未完成节点
    try:
        from app.planning.dag_storage import DAGStorage
        from app.config import DB_PATH
        thread_id = state.get("thread_id") or "default"
        dag = DAGStorage(DB_PATH)
        plan = dag.get_plan_by_thread(thread_id)
        if plan and plan["status"] in ("planning", "executing"):
            nodes = dag.get_nodes(plan["id"])
            if any(n["status"] not in ("success", "skipped", "failed") for n in nodes) or \
               any(n["status"] == "failed" for n in nodes):
                return "executor"
    except Exception as e:
        print(f"[Router chatbot] 读 DAG DB 失败: {e}")
    return END


# ============================================================
# 路由：executor / validator / summarizer 之间的条件边（保留旧逻辑）
# ============================================================

def route_after_executor(state: State):
    """DAG executor 退出后：所有决策走 dag_storage（唯一真相源）。
      1) 如果连续 3 次无可执行节点 → 进 summarizer（防死循环）
      2) 还有未完成节点（pending/ready/running）→ 继续 executor
      3) 还有 failed 节点且重规划未用完 → 继续 executor（让入口触发下一轮局部重规划）
      4) 全部终态且重规划已用完 → 进 summarizer 汇总结论"""
    # 首先检查是否连续空批达到上限
    empty_streak = int(state.get("_empty_streak", 0))
    if empty_streak >= 3:
        add_log_entry("info", f"DAG 路由→summarizer: 连续 {empty_streak} 次无可执行节点")
        return "summarizer"

    thread_id = state.get("thread_id") or "default"
    try:
        from app.planning.dag_core import MAX_REPLAN
        from app.planning.dag_storage import DAGStorage
        from app.config import DB_PATH
        dag = DAGStorage(DB_PATH)
        plan = dag.get_plan_by_thread(thread_id)
        if not plan:
            add_log_entry("info", "DAG 路由→END: 无进行中计划")
            return "summarizer"
        nodes = dag.get_nodes(plan["id"])
    except Exception as e:
        print(f"[Router DAG] 读 DB 失败: {e}")
        return "summarizer"

    unfinished = [n for n in nodes if n["status"] in ("pending", "ready", "running")]
    if unfinished:
        add_log_entry("info", f"DAG 路由→executor: 还有 {len(unfinished)} 节点未完成")
        return "executor"

    # 还有 failed 节点、且重规划次数没用完 → 回 executor 触发下一轮局部重规划。
    # 缺了这条时：一批节点全失败 → unfinished 为空 → 直接收敛结束，
    # 而重规划挂在 executor 入口，于是全程只可能重规划一次（上限形同虚设）。
    replan_count = int(plan.get("replan_count", 0))
    failed = [n for n in nodes if n["status"] == "failed"]
    if failed and replan_count < MAX_REPLAN:
        add_log_entry(
            "info",
            f"DAG 路由→executor: 仍有 {len(failed)} 个失败节点，触发第 "
            f"{replan_count + 1}/{MAX_REPLAN} 次局部重规划",
        )
        return "executor"

    # 全部终态 → 汇总结论（含失败节点也在 summarizer 兜底说明）
    add_log_entry("info", "DAG 路由→summarizer: 全部节点终态")
    return "summarizer"


def route_after_chatbot_fallback(state: State):
    return "chatbot"


def route_after_summarizer(state: State):
    """summarizer 完成后清空 pending_plan，再回到 chatbot 让用户继续对话。"""
    return "chatbot"


# ============================================================
# Build Graph
# ============================================================

def build_graph():
    builder = StateGraph(State)

    # planner_node 需要做特殊包装：从 state["pending_plan"] 读 goal
    planner_inner = create_dag_planner_node(llm)

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
    builder.add_node("executor", create_executor([llm], tools))
    # DAG 全部终态后的「交付汇总」节点：把各子任务结果汇总成最终答复交给用户。
    # 注意：它与 build_memory_injection（历史对话压缩注入）是两件事，不能互相替代——
    # 少了它，任务跑完直接 END，用户只看到过程气泡、没有人"复命"。
    builder.add_node("summarizer", create_dag_summarizer(llm))

    builder.add_edge(START, "chatbot")
    # chatbot 退出后按 state 决定下一步
    builder.add_conditional_edges("chatbot", route_after_chatbot, {
        "planner": "planner",
        "executor": "executor",
        END: END,
    })
    # planner 之后总是进入 executor
    builder.add_edge("planner", "executor")
    # executor 退出后根据 DAG 状态决定：还有未完成 → 继续；全部终态 → summarizer 汇总
    builder.add_conditional_edges("executor", route_after_executor, {
        "executor": "executor", "summarizer": "summarizer",
    })
    # 汇总节点把最终答复写成 AIMessage（SSE 推成 step=final,node=summarizer），随后本图结束
    builder.add_edge("summarizer", END)

    conn = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(checkpointer=checkpointer)
