from dotenv import load_dotenv
load_dotenv()
import uuid
import sys
import os
import time
from app.server import start_debug_server, add_log_entry, set_graph, load_model_config
from app.server.console_log import install_console_logger

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
    debug_thread = start_debug_server(host="0.0.0.0", port=8081)
    add_log_entry("info", "Agent 启动")

    graph = build_graph()
    # 沿用上次的 thread_id；传 --new 则开新会话
    config = {"configurable": {"thread_id": get_or_create_thread_id()}}
    # 把 graph 实例注入到 debug_server，使前端 /api/chat 能直接对话
    set_graph(graph, config)
    print(f"[thread_id] {config['configurable']['thread_id']}")
    if "--new" in sys.argv:
        print("[new session] 已开启新会话，旧 thread 仍保留在 SQLite 中（可手工查看）")

    # 控制台日志：把节点/LLM/工具/规划/任务等事件以彩色一行实时打印（取代旧的终端对话循环）。
    installed = install_console_logger()
    if installed:
        add_log_entry("success", "控制台日志已启动（后台服务模式，对话请到调试面板）")

    # 后台常驻：无终端交互，一切请求（对话/审批/定时任务）都走 HTTP。
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        add_log_entry("info", "Agent 关闭")
        print("[service] 已停止")


if __name__ == "__main__":
    main()