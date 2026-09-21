"""
path_guard.py — 关键路径保护：防止 Agent 的文件操作/命令执行破坏核心文件。

保护范围（Agent 可写区域应为 deliverables/ 等产出目录）：
  - app/server/static/   前端面板（曾被模型覆盖成游戏）
  - data/                数据库 + 模型配置（含 API key）
  - .env / .model_config 密钥
  - .git/                版本历史
  - checkpoints.db / memory.db
  - pyproject.toml / uv.lock  项目元数据

例外：**当前用户自己的工作区**（data/users/<当前用户>/）内开放全权读写——
deliverables/ uploads/ 等产出目录均可写；但其中用户的运行时文件继续受保护：
  - data/users/<用户>/data/           （用户的 memory.db / checkpoints.db / 媒体库）
  - data/users/<用户>/.thread_id       （会话指针）
  - data/users/<用户>/.approval_mode   （审批模式）
其他用户的工作区不在豁免内（data/ 前缀仍保护）。
"""
import os
from typing import Optional

from app.config import BASE_DIR

# 受保护路径（相对 BASE_DIR 的前缀/精确匹配）
PROTECTED_PREFIXES = [
    "app/server/static",
    "data/",
    ".env",
    ".model_config",
    ".git/",
    "checkpoints.db",
    "memory.db",
    "pyproject.toml",
    "uv.lock",
]

# execute_command 命令中的危险目标关键词（匹配到即拒绝，路径分隔符已统一）
CMD_DANGEROUS = [
    "del .env",
    "rm .env",
    "rm -rf .git",
]


def normalize(path: str) -> str:
    """把相对/绝对路径归一为相对 BASE_DIR 的规范路径（小写、反斜杠统一）。"""
    p = os.path.abspath(os.path.join(BASE_DIR, path or ""))
    rel = os.path.relpath(p, BASE_DIR).replace("\\", "/").lower()
    return rel


def _current_workspace_rel() -> Optional[str]:
    """当前用户工作区相对于 BASE_DIR 的规范路径（data/users/<当前用户>）。"""
    try:
        from app.config import WORKSPACE_DIR
        rel = os.path.relpath(os.path.abspath(str(WORKSPACE_DIR)), BASE_DIR).replace("\\", "/").lower()
        return rel
    except Exception:
        return None


def _workspace_runtime_protected(rel: str, ws: str) -> bool:
    """工作区内仍需保护的路径：工作区根（整目录删除=连带丢数据库）、
    用户自身 data/（数据库/媒体库）与运行时点文件。"""
    rest = rel[len(ws):].lstrip("/")
    if not rest:
        return True
    if rest.startswith("data/"):
        return True
    if rest in (".thread_id", ".approval_mode"):
        return True
    return False


def is_protected(path: str) -> bool:
    """判断路径是否命中保护清单（写/删应被拒绝）。

    当前用户自己的工作区豁免于 data/ 前缀，其内仅运行时文件（自身 data/、.thread_id、
    .approval_mode）受保护；其余路径按 PROTECTED_PREFIXES 判定。
    """
    rel = normalize(path)
    ws = _current_workspace_rel()
    if ws and (rel == ws or rel.startswith(ws + "/")):
        return _workspace_runtime_protected(rel, ws)
    for prefix in PROTECTED_PREFIXES:
        p = prefix.lower().replace("\\", "/").rstrip("/")
        if rel == p or rel.startswith(p + "/"):
            return True
    return False


def command_targets_protected(cmd: str) -> bool:
    """命令字符串是否包含对保护路径的写入/删除操作。"""
    if not cmd:
        return False
    # 统一路径分隔符（\ → /）后匹配保护路径
    low = cmd.lower().replace("\\", "/")
    for prefix in PROTECTED_PREFIXES:
        p = prefix.lower().replace("\\", "/").rstrip("/")
        if p in low:
            return True
    # 危险删除类命令（.env / .git 等）
    for kw in CMD_DANGEROUS:
        if kw in low:
            return True
    return False
