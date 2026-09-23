"""app.workbench — 短剧创作工作台的命令执行作用域。

「execute_command 仅对短剧创作开放」的实现载体：普通用户默认拿不到 execute_command，
但当用户正通过「短剧创作」工作台工作（在**当前会话线程**下发起了短剧创作任务）时，
服务端按线程签发一个限时作用域，作用域内该用户的工具集临时包含 execute_command。

约束（不因作用域放开而解除）：
  - 审批流照旧：命令仍走 execute_command 的人工审批（per_ask / session_allow / always_allow）；
  - path_guard 照旧：命令只能操作当前用户自己的工作区与系统内置技能目录，
    触碰 data/、.env、.git 等一律拒绝；
  - 作用域按「用户 × 线程」隔离，带保鲜期（TTL）自动过期；离开工作台或新建会话即失效。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

# 作用域保鲜期：2 小时（前端发起模块时会刷新；过期自动失效，防长期残留）。
DRAMA_CMD_TTL = 2 * 3600

_FLAG_FILENAME = ".drama_cmd_threads.json"


def _flag_path(username: Optional[str] = None) -> str:
    from app.userctx import user_root
    return os.path.join(user_root(username), _FLAG_FILENAME)


def _load(username: Optional[str] = None) -> Dict[str, float]:
    try:
        with open(_flag_path(username), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save(username: Optional[str], data: Dict[str, float]) -> None:
    p = _flag_path(username)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def set_drama_command_enabled(thread_id: Optional[str] = None, enabled: bool = True,
                              username: Optional[str] = None) -> dict:
    """按线程开/关短剧创作命令作用域。

    enabled=True 时会刷新该线程的保鲜时间戳；False 则移除该线程条目。
    返回 {thread, enabled, threads, ttl_seconds}。
    """
    from app.userctx import current_user, current_tid
    u = username or current_user()
    tid = (thread_id or "").strip() or current_tid(u)
    if len(tid) > 80:
        tid = tid[:80]
    data = _load(u)
    if enabled:
        data[tid] = time.time()
    else:
        data.pop(tid, None)
        # 顺手清掉已过期条目，保持文件干净
        data = {t: ts for t, ts in data.items() if time.time() - ts < DRAMA_CMD_TTL}
    _save(u, data)
    return {"thread": tid, "enabled": enabled, "threads": len(data),
            "ttl_seconds": DRAMA_CMD_TTL}


def clear_drama_command_scope(username: Optional[str] = None) -> None:
    """清空该用户全部线程的作用域（管理员操作 / 退出登录兜底）。"""
    u = username or "Mirror"
    _save(u, {})


def is_drama_command_enabled(username: Optional[str] = None,
                             thread_id: Optional[str] = None) -> bool:
    """指定用户 + 线程是否有生效的短剧创作命令作用域（含保鲜期校验）。"""
    from app.userctx import current_tid
    u = username or "Mirror"
    tid = (thread_id or "").strip() or current_tid(u)
    data = _load(u)
    ts = data.get(tid)
    return bool(ts) and (time.time() - ts) < DRAMA_CMD_TTL


def drama_command_state(username: Optional[str] = None,
                        thread_id: Optional[str] = None) -> dict:
    """查询当前状态（供前端工作台展示）。"""
    from app.userctx import current_tid
    u = username or "Mirror"
    tid = (thread_id or "").strip() or current_tid(u)
    data = _load(u)
    ts = data.get(tid)
    now = time.time()
    active = bool(ts) and (now - ts) < DRAMA_CMD_TTL
    return {
        "enabled": active,
        "thread": tid,
        "threads": len(data),
        "ttl_seconds": DRAMA_CMD_TTL,
        "since": ts,
        "expires_in": int(ts + DRAMA_CMD_TTL - now) if active else 0,
        "path": _FLAG_FILENAME,
    }