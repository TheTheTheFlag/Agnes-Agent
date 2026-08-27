"""app.server.git_ops — Agent 工作区 git 版本管理。

能力：
  - auto_snapshot(reason)：Agent 每轮对话/工具改动后自动提交工作区变更（代码+交付物）
  - git_log / git_status：查看版本历史与当前变更
  - 开关控制（auto_git）：存于 data/.model_config
"""
import os
import subprocess
from typing import List, Dict, Any

from app.config import BASE_DIR

# 开关键名（存 .model_config）
_AUTO_GIT_KEY = "auto_git"


def _model_config() -> dict:
    from app.server.config import _load_model_file
    return _load_model_file()


def _save_model_config(cfg: dict):
    from app.server.config import _save_model_file
    _save_model_file(cfg)


def is_auto_git_enabled() -> bool:
    """自动快照开关（默认开启）。"""
    return bool(_model_config().get(_AUTO_GIT_KEY, True))


def set_auto_git(enabled: bool):
    cfg = _model_config()
    cfg[_AUTO_GIT_KEY] = bool(enabled)
    _save_model_config(cfg)


def _run_git(args: List[str], timeout: int = 15) -> str:
    """执行 git 命令，返回 stdout；失败返回错误信息（不抛异常）。"""
    try:
        r = subprocess.run(
            ["git", "-C", BASE_DIR] + args,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return (r.stdout or "").strip()
    except Exception as e:
        return f"[git error] {e}"


def _has_changes() -> bool:
    """工作区是否有未提交变更（含未跟踪文件）。"""
    out = _run_git(["status", "--porcelain"])
    return bool(out.strip())


def auto_snapshot(reason: str = "") -> Dict[str, Any]:
    """自动提交当前工作区变更。无变更则跳过。返回提交信息。"""
    if not is_auto_git_enabled():
        return {"ok": True, "skipped": "auto_git 已关闭"}
    if not _has_changes():
        return {"ok": True, "skipped": "无变更"}

    msg = f"[Agent] {reason[:80]}" if reason else "[Agent] 自动快照"
    _run_git(["add", "-A"])
    _run_git(["commit", "-m", msg])
    # 返回最新一条提交
    log = _run_git(["log", "-1", "--pretty=format:%h|%s|%ad", "--date=format:%m-%d %H:%M"])
    return {"ok": True, "commit": log}


def git_log(limit: int = 20) -> List[Dict[str, Any]]:
    """版本历史。"""
    out = _run_git(["log", f"-{limit}", "--pretty=format:%h|%an|%ad|%s", "--date=format:%Y-%m-%d %H:%M:%S"])
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            rows.append({"hash": parts[0], "author": parts[1], "date": parts[2], "message": parts[3]})
    return rows


def git_status() -> Dict[str, Any]:
    """当前工作区状态。"""
    out = _run_git(["status", "--porcelain"])
    files = []
    for line in out.splitlines():
        if line.strip():
            files.append({"code": line[:2].strip(), "path": line[3:]})
    return {"files": files, "count": len(files), "auto_git": is_auto_git_enabled()}


def git_diff(limit_chars: int = 3000) -> str:
    """当前未提交变更的 diff 预览。"""
    return (_run_git(["diff", "--stat"]) + "\n" + _run_git(["diff", "--", "*.py", "*.md", "*.txt"]) )[:limit_chars]
