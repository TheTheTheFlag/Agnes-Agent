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
deliverables/ uploads/ 等产出目录均可写（文件工具与命令执行一致放行）；
但其中用户的运行时文件继续受保护：
  - data/users/<用户>/data/           （用户的 memory.db / checkpoints.db / 媒体库）
  - data/users/<用户>/.thread_id       （会话指针）
  - data/users/<用户>/.approval_mode   （审批模式）
其他用户的工作区不在豁免内（data/ 前缀仍保护）。
"""
import os
import re
from typing import Optional

from app.config import BASE_DIR

# 极简 shell 分词：识别成对引号包裹的参数，其余按空白切分（不吞反斜杠，
# 兼容 Windows 反斜杠路径与 POSIX 路径）。
_TOKEN_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')

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


def _shell_tokens(cmd: str) -> list:
    """把命令拆成参数 token（剥掉外层成对引号）。"""
    return [t.strip("\"'") for t in _TOKEN_RE.findall(cmd)]


def _token_in_workspace(tok: str, ws: str) -> bool:
    """单个路径 token 是否落在当前用户工作区内且非运行时受保护文件。"""
    if not tok or not ws:
        return False
    rel = normalize(tok)
    if rel == ws or rel.startswith(ws + "/"):
        return not _workspace_runtime_protected(rel, ws)
    return False


def command_targets_protected(cmd: str) -> bool:
    """命令字符串是否包含对保护路径的写入/删除操作。

    与 is_protected 一致：当前用户自己工作区内的普通路径（deliverables/ uploads/
    skills/ 等）对命令同样放行——短剧工作台等系统级功能要在此处跑 node 脚本；
    但工作区根目录、用户自身 data/, .thread_id, .approval_mode 仍拦截，其他用户的
    工作区（data/ 前缀）与危险删除关键词全局生效。
    """
    if not cmd:
        return False
    ws = _current_workspace_rel()
    # 摘掉工作区内放行的路径 token，其余再按保护清单判定
    kept = [t for t in _shell_tokens(cmd) if not (ws and _token_in_workspace(t, ws))]
    if not kept:
        return False
    hay = " ".join(kept).lower().replace("\\", "/")
    for prefix in PROTECTED_PREFIXES:
        p = prefix.lower().replace("\\", "/").rstrip("/")
        if p in hay:
            return True
    # 危险删除类命令（.env / .git 等）
    for kw in CMD_DANGEROUS:
        if kw in hay:
            return True
    return False


# ==================== 递归删除的工作区边界（2026-09-28） ====================
# 背景：前置门禁原以子串 'rm -rf /' 拦截，凡「rm -rf + 绝对路径」一律误杀——
# 连本用户工作区内的合法删除/重建（deliverables/ 下剧目目录）也被拦，且拒因含糊，
# 模型改参数反复重试触发「连续 3 次被拒」纪律终止。现改为路径级判定：
# 递归删除（rm -r/-rf/--recursive）的目标必须全部位于当前用户工作区内
# （data/users/<用户名>/，区内全权）；区外、工作区根、运行时受保护文件仍拒绝，
# 并返回具体目标 token 供拒因提示（模型凭此可直接改正）。

# shell 操作符：把命令切成「简单命令」段（含后台 &）
_OP_SPLIT_RE = re.compile(r"&&|\|\||;|\||&")
# 重定向构造（2>&1、>out、>>log、<<EOF 等）：先整体剔除再切段，
# 避免 fd 数字（如 2>&1 的 "2"）与重定向文件名混入 rm 目标
_REDIR_RE = re.compile(r"(?:>>|\d*>|<|&>)\s*(?:&\s*\d+|\d+|\S*)")
# cd 参数不可静态判定（变量/命令替换/通配）时的特征 → cwd 置为未知（相对目标保守拒绝）
_CD_UNSAFE_RE = re.compile(r"[$`*?\[\]]")
# 段首 rm（含 bash -c "rm -rf …" 这类引号内命令被拆出的 token）
_RM_START_RE = re.compile(r"^rm\s")


def _segments(cmd: str) -> list:
    """按 shell 操作符切段、分词；引号内本身是命令（bash -c "rm -rf …"）时拆开继续分析。"""
    segs = []
    for part in _OP_SPLIT_RE.split(_REDIR_RE.sub(" ", cmd or "")):
        toks = []
        for t in _shell_tokens(part or ""):
            if t == "rm" or _RM_START_RE.match(t):
                toks.extend(t.split())
            else:
                toks.append(t)
        if toks:
            segs.append(toks)
    return segs


def _abs_in_workspace(path: str, ws: str) -> bool:
    """绝对路径是否位于当前用户工作区内（工作区根与运行时受保护文件视为区外）。"""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(BASE_DIR)).replace("\\", "/").lower()
    if ws and (rel == ws or rel.startswith(ws + "/")):
        return not _workspace_runtime_protected(rel, ws)
    return False


def _rm_target_in_workspace(tok: str, cwd: Optional[str], ws: str) -> bool:
    """递归删除的单个目标 token 是否位于工作区内（保守：无法静态判定=区外）。"""
    if not tok or "$" in tok or "`" in tok:
        return False
    # 通配符安全：* ? [ 只能展开为「前缀目录」内的条目（不会跨 /），前缀在区内才放行
    cut = len(tok)
    for ch in ("*", "?", "["):
        i = tok.find(ch)
        if i != -1:
            cut = min(cut, i)
    core = tok[:cut]
    if not core:
        return False
    if core.startswith("~"):
        p = os.path.expanduser(core)
    elif os.path.isabs(core):
        p = core
    elif cwd:
        p = os.path.join(cwd, core)
    else:
        return False
    return _abs_in_workspace(p, ws)


def rm_recursive_outside_workspace(cmd: str) -> Optional[str]:
    """检出「递归删除且目标逃出当前用户工作区」的命令。

    返回首个区外目标 token（供拒因提示）；全部安全（含非递归 rm、无目标）返回 None。
    工作区=data/users/<用户名>/：区内普通路径（deliverables/ uploads/ skills/ 等）
    全权放行（含删后重建）；区外所有路径（/、~、他人目录、相对越界、含变量/命令
    替换的不可判定目标）与工作区根、自身 data/、.thread_id、.approval_mode 仍拦截。
    支持 `cd <区内路径> && rm -r <相对路径>` 的 cwd 跟踪。
    """
    if not cmd:
        return None
    ws = _current_workspace_rel() or ""
    cwd = os.path.realpath(BASE_DIR)
    for toks in _segments(cmd):
        # cd 跟踪：让「cd 区内目录 && rm -r 相对路径」判定正确
        if toks[0] == "cd" and len(toks) == 2:
            arg = toks[1]
            if _CD_UNSAFE_RE.search(arg) or arg.startswith("-"):
                cwd = None
            elif arg.startswith("~"):
                cwd = os.path.expanduser(arg)
            elif os.path.isabs(arg):
                cwd = arg
            else:
                cwd = os.path.join(cwd, arg) if cwd else None
            continue
        # rm 允许出现在段内任意位置（sudo rm / /bin/rm / $(rm …)）
        idx = -1
        for i, t in enumerate(toks):
            if t == "rm" or t.endswith("/rm") or t in ("$(rm", "`rm"):
                idx = i
                break
        if idx < 0:
            continue
        recursive = False
        targets = []
        seen_dashdash = False
        for t in toks[idx + 1:]:
            if not seen_dashdash and t == "--":
                seen_dashdash = True
                continue
            if not seen_dashdash and t.startswith("--"):
                if t.startswith("--recursive"):
                    recursive = True
                continue
            if not seen_dashdash and t.startswith("-") and len(t) > 1:
                if "r" in t[1:] or "R" in t[1:]:
                    recursive = True
                continue
            targets.append(t)
        if not recursive or not targets:
            continue
        for tgt in targets:
            if not _rm_target_in_workspace(tgt, cwd, ws):
                return tgt
    return None
