"""app.graph.run_registry — 进程内 DAG 运行注册表 + 执行期插话补充队列。

背景（2026-09-29）：DAG 多节点执行时，/api/chat 的 graph.run 会长期霸占
该 thread 的 SSE 流（直到整个计划收敛），前端 State.streaming 一直为 True，
用户无法再发消息，也无法给正在执行的任务追加补充说明。

方案：每个 thread 同一时刻只允许一个 graph.run。
  - 新消息到达时若该 thread 已有 run 在执行 → 不启动第二个 run，把消息推进
    本注册表的补充队列（入队），立即返回轻量 SSE（"已加入进行中任务"）。
  - 正在执行的 dag_executor 每处理一批 ready 节点前，从队列 drain 补充内容
    并注入到本批节点 prompt（与原 state.pending_user_input 合并）。
  - run 结束时释放注册（finally 里调用 release_run），队列残留一并清掉。

线程安全：chat_endpoint 的 async 与 dag_executor 的工作线程都可能访问，
统一用 threading.Lock 保护。
"""
import threading
from typing import Dict, List

_lock = threading.Lock()
_active: set = set()              # thread_id 集合：当前有 graph.run 正在执行
_queue: Dict[str, List[str]] = {} # thread_id -> 排队的补充消息列表


def try_lock_run(thread_id: str) -> bool:
    """尝试占用该 thread 的运行许可。成功返回 True（本次 run 真正启动）；
    返回 False 说明已有 run 在执行，调用方应把新消息入队而不是启动新 run。"""
    with _lock:
        if thread_id in _active:
            return False
        _active.add(thread_id)
        return True


def release_run(thread_id: str) -> None:
    """graph.run 结束时释放许可；同时清掉该 thread 的补充队列残留。"""
    with _lock:
        _active.discard(thread_id)
        _queue.pop(thread_id, None)


def push_supplement(thread_id: str, text: str) -> None:
    """执行期插话入队。executor 下批节点会消费。"""
    with _lock:
        _queue.setdefault(thread_id, []).append(text.strip())


def drain_supplements(thread_id: str) -> List[str]:
    """取出并清空该 thread 当前积压的补充消息（executor 每批消费一次）。"""
    with _lock:
        return _queue.pop(thread_id, [])