from dotenv import load_dotenv
load_dotenv()
import uuid
import sys
import os
import asyncio
from app.server import start_debug_server, add_log_entry, set_graph, load_model_config
from langgraph.types import Command
from langchain_core.messages import AIMessage

# 先读模型配置并设置环境变量（graph 模块 import 时会读取）
_model_cfg = load_model_config()
# 配置层解析：注入 OPENAI_BASE_URL / OPENAI_API_KEY（凭据来自 .model_config），
# 返回归一后的 provider（tongyi/deepseek 原样，其余一律 openai_compatible）
from app.server import resolve_provider_env
_real_provider = resolve_provider_env(_model_cfg["provider"])
os.environ["LLM_PROVIDER"] = _real_provider
os.environ["LLM_MODEL"] = _model_cfg["model"]

from app.graph.builder import build_graph

# thread_id 持久化文件：让连续启动保持同一会话
THREAD_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".thread_id")


def get_or_create_thread_id() -> str:
    """读取上次的 thread_id；除非用户传 --new，否则一直沿用。
    这样连续启动之间可以保留对话历史和记忆。"""
    new_requested = "--new" in sys.argv
    if new_requested or not os.path.exists(THREAD_ID_FILE):
        tid = str(uuid.uuid4())
        with open(THREAD_ID_FILE, "w", encoding="utf-8") as f:
            f.write(tid)
        return tid
    with open(THREAD_ID_FILE, "r", encoding="utf-8") as f:
        tid = f.read().strip()
    if not tid:
        tid = str(uuid.uuid4())
        with open(THREAD_ID_FILE, "w", encoding="utf-8") as f:
            f.write(tid)
    return tid


def main():
    # 启动调试服务器（后台线程；端口可能被占用会自动顺延）
    debug_thread = start_debug_server(host="0.0.0.0", port=8000)
    add_log_entry("info", "Agent 启动")

    graph = build_graph()
    # 沿用上次的 thread_id；传 --new 则开新会话
    config = {"configurable": {"thread_id": get_or_create_thread_id()}}
    # 把 graph 实例注入到 debug_server，使前端 /api/chat 能直接对话
    set_graph(graph, config)
    print(f"[thread_id] {config['configurable']['thread_id']}")
    if "--new" in sys.argv:
        print("[new session] 已开启新会话，旧 thread 仍保留在 SQLite 中（可手工查看）")

    print("🤖 Agent 已启动，输入 'quit' 或 'exit' 退出。")
    # 提示的端口保持 8000，但 start_debug_server 已在顺延后打印过实际端口；
    # 此处保留原提示以免破坏既有提示习惯。
    print("💡 调试面板: http://localhost:8000 （若已被占用，启动时已顺延到 8001+，请看上方面板提示）")
    print("💡 当工具调用需要审批时，您可以选择：")
    print("   [y] 允许执行本次")
    print("   [n] 拒绝执行本次")
    print("   [s] 本次会话自动允许（后续不再询问）")
    print("   [A] 完全无限制（永久允许所有工具）")
    print("   [v] 查看当前审批模式\n")

    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("quit", "exit"):
            print("👋 再见！")
            break
        if not user_input:
            continue

        add_log_entry("info", f"用户输入: {user_input[:100]}")

        inputs = {"messages": [("user", user_input)]}

        while True:
            events = graph.stream(inputs, config, stream_mode="updates")
            interrupted = False
            for event in events:
                if "__interrupt__" in event:
                    interrupted = True
                    interrupt_info = event["__interrupt__"][0]
                    value = interrupt_info.value
                    print("\n[系统] 需要人工确认：")
                    print(f"  问题: {value['question']}")
                    if "command" in value:
                        print(f"  命令: {value['command']}")
                    current_mode = value.get("mode", "per_ask")
                    print(f"  当前审批模式: {current_mode}")
                    print("选项: [y] 允许执行 [n] 拒绝执行 [s] 本次会话自动允许 [A] 完全无限制")
                    while True:
                        choice = input("请选择: ").strip().lower()
                        if choice == 'y':
                            resume_value = {"allow": True}
                            break
                        elif choice == 'n':
                            resume_value = {"allow": False}
                            break
                        elif choice == 's':
                            resume_value = {"allow": True, "mode": "session_allow"}
                            break
                        elif choice == 'A':
                            resume_value = {"allow": True, "mode": "always_allow"}
                            break
                        else:
                            print("无效输入，请重新选择:")
                            print("选项: [y] 允许执行 [n] 拒绝执行 [s] 本次会话自动允许 [A] 完全无限制")
                    inputs = Command(resume=resume_value)
                    add_log_entry("info", f"审批: {choice}")
                    break

            if not interrupted:
                final_state = graph.get_state(config).values
                break

        messages = final_state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage):
                print(f"🤖 助手: {last_msg.content}")
            else:
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage):
                        print(f"🤖 助手: {msg.content}")
                        break

if __name__ == "__main__":
    main()