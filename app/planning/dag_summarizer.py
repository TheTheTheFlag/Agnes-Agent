"""app.planning.dag_summarizer — DAG 执行完成后的「交付汇总」节点

原 summarizer 节点（见 git 基线 app/planning/summarizer.py）在迁移到新规划器时被一并删除，
但它承担的是「汇总所有子任务结果 → 生成完整的最终答复交给用户」，与
build_memory_injection（把历史对话压缩后注入 system prompt）职责完全不同，不能互相替代。
少它的直接后果：DAG 跑完直接 END，用户只看到过程气泡，没有人"复命"。

本模块按新规划器（dag_plans / dag_nodes）重写该职责：
  - 数据源：dag_nodes（description / status / artifacts / result），跳过 skipped 的作废节点
  - 产出：一条 AIMessage 追加进 messages
      → SSE 的 updates 模式会把它推成 {"step":"final","node":"summarizer","text":…}
        （app/server/api/chat.py 只对 chatbot/summarizer 节点这样做），并兜底落 messages 表
      → 前端 app.js 对 node==="summarizer" 的 final 事件会"无条件覆盖"当前气泡，展示最终答复
  - 失败兜底：存在 failed 节点时不调 LLM，直接给结构化说明，避免模型把失败讲成成功
"""
from typing import Dict, List

import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config import DB_PATH
from app.graph.state import State
from app.planning import dag_core as core
from app.planning.dag_storage import DAGStorage
from app.server import add_event, add_log_entry

_SUMMARY_SYSTEM_PROMPT = (
    "请根据以下子任务的执行结果，生成一个简洁、完整的最终回答，直接满足用户的原始需求。"
    "如果用户要求开发一个游戏或应用，请先确认文件已生成，然后简要说明如何使用；"
    "不要重复生成完整代码（除非用户明确要求），而是告诉用户文件已存在并给出使用说明。"
    "用中文回答，重点说清「交付了什么、文件在哪、怎么用」，不要罗列内部实现细节。"
)


def _collect(nodes: List[Dict]):
    """汇总可见节点。返回 (给 LLM 的结果清单, 产物路径列表, 失败节点列表)。"""
    lines: List[str] = []
    artifacts: List[str] = []
    failed: List[Dict] = []
    for n in nodes:
        st = n.get("status")
        if st == core.STATUS_SKIPPED:
            continue  # 被局部重规划替换 / 前置失败跳过的作废节点，不向用户汇报
        if st == core.STATUS_FAILED:
            failed.append(n)
        # 只保留磁盘上真实存在的产物：执行器记录的 _written 不会因 delete_file 回滚，
        # 校验脚本之类的中间产物已被删除；不过滤会在总结里把它们列成"交付文件"，误导用户。
        arts: List[str] = []
        for a in (n.get("artifacts") or []):
            p = str(a).replace("\\", "/").strip()
            if p and p not in arts and os.path.exists(p):
                arts.append(p)
        for a in arts:
            if a not in artifacts:
                artifacts.append(a)
        line = f"- [{st}] {(n.get('description') or '')[:120]}"
        if arts:
            line += "\n  产出：" + ", ".join(arts)
        res = (n.get("result") or "").strip()
        if res:
            line += "\n  结果：" + res[:400]
        lines.append(line)
    return "\n".join(lines) or "（无可见节点）", artifacts, failed


def create_dag_summarizer(llm):
    """返回 summarizer 节点函数：把 DAG 各节点结果汇总成给用户的最终答复。"""

    def summarizer_node(state: State):
        thread_id = state.get("thread_id") or "default"
        from app.trace import record_node_start, record_node_end
        record_node_start(thread_id, "summarizer")

        dag = DAGStorage(DB_PATH)
        plan = dag.get_plan_by_thread(thread_id)
        if not plan:
            record_node_end(thread_id, "summarizer", "无计划可汇总")
            return {"thread_id": thread_id}

        nodes = dag.get_nodes(plan["id"])
        combined, artifacts, failed = _collect(nodes)
        goal = plan.get("goal", "")

        if failed:
            # 有失败节点 → 不调 LLM（避免把失败讲成成功，也省一轮调用）
            detail = "\n".join(
                f"- {(f.get('description') or '')[:80]}（{(f.get('result') or '').strip()[:200]}）"
                for f in failed
            )
            summary = f"任务未完全完成：{len(failed)} 个子任务失败。\n\n失败项：\n{detail}"
            if artifacts:
                summary += "\n\n已产出的文件：\n" + "\n".join(f"- {a}" for a in artifacts)
        else:
            summary = ""
            try:
                resp = llm.invoke([
                    SystemMessage(content=_SUMMARY_SYSTEM_PROMPT),
                    HumanMessage(content=f"目标：{goal}\n执行结果：\n{combined}"),
                ])
                summary = (getattr(resp, "content", "") or "").strip()
            except Exception as e:
                add_log_entry("warn", f"交付汇总 LLM 调用失败，改用结构化输出: {e}")
            if not summary:
                # LLM 不可用时也不能让用户什么都看不到
                summary = f"任务已完成。\n\n执行结果：\n{combined}"
                if artifacts:
                    summary += "\n\n产出文件：\n" + "\n".join(f"- {a}" for a in artifacts)

        dag.set_plan_status(plan["id"], "completed")
        # 注：任务交付汇报**不再**写入 history_summaries 表。
        # 那张表的语义是"历史对话压缩摘要"（由 app/memory/compaction.py 在 token 达阈值时写入），
        # 把任务汇报混进去会让 build_memory_injection 的 history_summary 层注入错误语义的内容。
        # 任务汇报本身已由 dag_plans.status + add_event("summarizer") + 前端气泡完整表达。
        add_event("summarizer", {"done": True, "artifacts": artifacts}, thread_id)
        add_log_entry("success", f"交付汇总完成（产物 {len(artifacts)} 个）")
        record_node_end(thread_id, "summarizer", f"{len(artifacts)} 个产物")

        # 用 add_messages reducer 追加，不要返回整个 list（否则会重复）
        return {
            "thread_id": thread_id,
            "messages": [AIMessage(content=summary)],
            "pending_plan": None,
        }

    return summarizer_node
