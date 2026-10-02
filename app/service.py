"""app.service — 服务模式入口（HTTP 常驻服务）。

与 app.main（python -m app.main）同为纯后台服务：
  - 没有终端交互循环，Agent 完全通过 HTTP 驱动（调试面板 /api/chat、/api/scheduler 等）；
  - 端口固定，被占用直接报错（app.main 会自动顺延）。

用法：
    python -m app.service                       # 默认 0.0.0.0:8081
    python -m app.service --port 9000           # 指定端口
    python -m app.service --new                 # 开新会话（新的 thread_id，沿用 .thread_id 文件）

改代码后需手动重启（systemctl restart agnes-agent）才生效。
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

import uvicorn

# 统一配置：.env → data/.model_config 并写回环境变量（须在 builder 读取 env 之前）
from app.config_store import bootstrap as _bootstrap_config
_bootstrap_config()

# Windows 控制台默认 GBK：Agent 输出含中文/emoji 时 print 会抛
# UnicodeEncodeError 中断流程。统一按 UTF-8 输出（Linux 上无影响）。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 先读模型配置并设置环境变量：app.graph.builder 在 import 时就会创建 LLM 实例，
# 必须在 import builder 之前把 provider/model/base_url 注入环境（与 main.py 顺序一致）。
from app.server import load_model_config, resolve_provider_env
_model_cfg = load_model_config()
_real_provider = resolve_provider_env(_model_cfg["provider"])
os.environ["LLM_PROVIDER"] = _real_provider
os.environ["LLM_MODEL"] = _model_cfg["model"]

from app.server import app, set_graph, add_log_entry
from app.graph.builder import build_graph

# thread_id 持久化文件：与 main.py 共用同一文件，两种模式之间会话连续
THREAD_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".thread_id")


def resolve_thread_id(force_new: bool = False) -> str:
    """读取/生成 thread_id（逻辑与 main.get_or_create_thread_id 一致，不依赖 sys.argv）。
    force_new=True 时立即生成新 id 写入文件（供 --new 使用）。"""
    if force_new or not os.path.exists(THREAD_ID_FILE):
        tid = str(uuid.uuid4())
        with open(THREAD_ID_FILE, "w", encoding="utf-8") as f:
            f.write(tid)
        return tid
    with open(THREAD_ID_FILE, "r", encoding="utf-8") as f:
        tid = f.read().strip()
    return tid or str(uuid.uuid4())


def inject_graph() -> dict:
    """构建 graph 并注入 server（与 main.py 的启动前置逻辑一致）。

    放在 FastAPI lifespan 中执行：无论是 python -m app.service 还是
    uvicorn app.service:app，加载 app 时都会自动完成注入；
    lifespan 触发时 graph 随服务一起重建。"""
    model_cfg = load_model_config()
    real_provider = resolve_provider_env(model_cfg["provider"])
    os.environ["LLM_PROVIDER"] = real_provider
    os.environ["LLM_MODEL"] = model_cfg["model"]
    graph = build_graph()
    config = {"configurable": {"thread_id": resolve_thread_id()}}
    set_graph(graph, config)
    return config


@asynccontextmanager
async def lifespan(_app):
    # 控制台日志：节点/LLM/工具/规划/任务等事件以彩色一行实时打印
    from app.server.console_log import install_console_logger
    install_console_logger()
    config = inject_graph()
    tid = config["configurable"]["thread_id"]
    add_log_entry("info", f"Service 启动 (thread_id={tid})")
    print(f"[service] graph 已注入 · thread_id={tid}")

    # 企业微信「智能机器人」长连接：配置了 WECHAT_BOT_ID/SECRET 才真正启动，
    # 失败只记日志（不影响 HTTP 服务）。用任务方式启动，避免建连阻塞服务就绪。
    from app.wecom.bot import start_wecom_bot, stop_wecom_bot
    _wecom_task = asyncio.create_task(start_wecom_bot())

    # 初始化用户作品库：_user_items 为进程内内存态，启动时需加载 manifest
    try:
        from app.server.api.image import _load_user_manifest as _load_image_manifest
        from app.server.api.video import _load_user_manifest as _load_video_manifest
        import app.server.accounts as _accounts
        for _user_info in _accounts.list_users():
            _u = _user_info.get("username") or _user_info
            _load_image_manifest(_u)
            _load_video_manifest(_u)
        add_log_entry("info", f"用户作品库已初始化（{_accounts.count_users()} 个用户）")
    except Exception as _e:
        add_log_entry("warn", f"作品库初始化失败: {_e}")

    try:
        yield
    finally:
        _wecom_task.cancel()
        stop_wecom_bot()
        add_log_entry("info", "Service 关闭")


# FastAPI 在 app.server 创建 app 时未传 lifespan，这里覆盖 router 的默认 lifespan，
# 使 graph 注入跟随服务生命周期。
app.router.lifespan_context = lifespan


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agnes Agent 服务模式（HTTP 常驻服务）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8081, help="监听端口（默认 8081）")
    parser.add_argument("--new", action="store_true", help="开启新会话（新的 thread_id）")
    args = parser.parse_args()

    if args.new:
        resolve_thread_id(force_new=True)
        print("[new session] 已生成新 thread_id，服务将以新会话启动")

    # 不用 import string 也行，但保持字符串形式以便可被 uvicorn 正常加载。
    uvicorn.run(
        "app.service:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
