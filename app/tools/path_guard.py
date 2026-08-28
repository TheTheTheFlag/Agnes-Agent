"""
path_guard.py — 关键路径保护：防止 Agent 的文件操作/命令执行破坏核心文件。

保护范围（Agent 可写区域应为 deliverables/ 等产出目录）：
  - app/server/static/   前端面板（曾被模型覆盖成游戏）
  - data/                数据库 + 模型配置（含 API key）
  - .env / .model_config 密钥
  - .git/                版本历史
  - checkpoints.db / memory.db
  - pyproject.toml / uv.lock  项目元数据
"""
import os
import re

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


def is_protected(path: str) -> bool:
    """判断路径是否命中保护清单（写/删应被拒绝）。"""
    rel = normalize(path)
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
