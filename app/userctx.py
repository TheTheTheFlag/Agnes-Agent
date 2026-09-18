"""app.userctx — 每用户运行时上下文（路径 / 密钥 / 当前用户）。

设计目标：把"单用户全局状态"平滑升级为"每用户隔离"，同时尽量不改动
下游 60+ 处直接使用 `app.config.DB_PATH` 的代码——做法是让这些常量变成
**上下文感知的路径代理**（见 app.config 的 `_UserPath`）：运行时按当前用户
解析到 `data/users/<username>/...`。

当前用户由 `ContextVar` 保存，来源：
  - HTTP 请求：认证中间件（app.server.auth）在请求开始时 `set_current_user`；
  - 图执行：/api/chat 的 sync_runner 线程内显式设置（raw Thread 不继承上下文）；
  - 后台线程：用 `run_in_user_thread()` 包装以继承用户上下文；
  - 缺省：`Mirror`（兼容离线单用户 / 定时任务 / 旧数据迁移）。

密钥解析：
  - 普通用户 → 仅用自己在注册时提交的 Agnes / 硅基流动 Key；
  - 管理员（Mirror）→ 全局 .model_config 的 Key + 所有已审批用户 Key 组成轮换池。
"""
from __future__ import annotations

import contextvars
import os
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.config import BASE_DIR, DATA_DIR

DEFAULT_USER = "Mirror"
USERS_DIR = os.path.join(DATA_DIR, "users")

_cur_user: contextvars.ContextVar[str] = contextvars.ContextVar("agnes_current_user", default="")
_cur_llm: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar("agnes_current_llm", default=None)


# ==================== 当前用户 ====================
def current_user() -> str:
    return _cur_user.get() or DEFAULT_USER


def set_current_user(username: Optional[str]):
    """设置当前用户，返回 token（可传回 reset_current_user）。"""
    return _cur_user.set(username or DEFAULT_USER)


def reset_current_user(token) -> None:
    try:
        _cur_user.reset(token)
    except Exception:
        pass


# ==================== 每用户路径 ====================
@dataclass(frozen=True)
class UserPaths:
    username: str
    root: str
    data_dir: str
    db_path: str
    checkpoint_db_path: str
    uploads_dir: str
    deliverables_dir: str
    rag_root: str
    skills_dir: str
    workspace: str
    thread_file: str


def user_root(username: Optional[str] = None) -> str:
    return os.path.join(USERS_DIR, username or current_user())


def user_paths(username: Optional[str] = None) -> UserPaths:
    u = username or current_user()
    root = user_root(u)
    data = os.path.join(root, "data")
    return UserPaths(
        username=u,
        root=root,
        data_dir=data,
        db_path=os.path.join(data, "memory.db"),
        checkpoint_db_path=os.path.join(data, "checkpoints.db"),
        uploads_dir=os.path.join(root, "uploads"),
        deliverables_dir=os.path.join(root, "deliverables"),
        rag_root=os.path.join(root, "lightrag_storage"),
        skills_dir=os.path.join(root, "skills"),
        workspace=root,
        thread_file=os.path.join(root, ".thread_id"),
    )


def ensure_user_dirs(username: Optional[str] = None) -> UserPaths:
    p = user_paths(username)
    for d in (p.data_dir, p.uploads_dir, p.deliverables_dir, p.rag_root, p.skills_dir):
        os.makedirs(d, exist_ok=True)
    return p


_ensured_users: set = set()


def resolve_path_prop(kind: str) -> str:
    """供 app.config 的路径代理按当前用户解析具体路径（首次访问时建目录）。"""
    u = current_user()
    if u not in _ensured_users:
        ensure_user_dirs(u)
        _ensured_users.add(u)
    return getattr(user_paths(u), kind)


# ==================== 旧单用户数据 → Mirror(admin) 迁移 ====================
_MIGRATION_FLAG = os.path.join(USERS_DIR, ".migrated_to_mirror")


def _merge_dir(src: str, dst: str) -> None:
    """把 src 目录内容合并进 dst（同名冲突时以 src 覆盖），随后删除空的 src。"""
    os.makedirs(dst, exist_ok=True)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s) and not os.path.islink(s):
            if os.path.exists(d) and not os.path.isdir(d):
                os.remove(d)
            _merge_dir(s, d)
        else:
            if os.path.exists(d):
                os.remove(d)
            os.rename(s, d)
    try:
        os.rmdir(src)
    except OSError:
        pass


def _migrate_item(src: str, dst: str) -> str:
    """迁移单个文件或目录；目标已存在时按"源更完整则合并/覆盖"处理，避免空占位导致漏迁。"""
    if not os.path.exists(src):
        return "absent"
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)

    src_is_dir = os.path.isdir(src) and not os.path.islink(src)
    dst_exists = os.path.exists(dst)

    if dst_exists and not force_placeholder(src, dst):
        return "skipped"

    try:
        if src_is_dir:
            if dst_exists:
                _merge_dir(src, dst)
            else:
                os.rename(src, dst)
            return "moved"
        # 文件：目标是目录、或需覆盖时先删目标
        if os.path.isdir(dst):
            return "error:目标为目录"
        if dst_exists and src_is_dir is False:
            try:
                if os.path.getsize(dst) >= os.path.getsize(src):
                    return "skipped"
            except OSError:
                pass
        if dst_exists:
            os.remove(dst)
        os.rename(src, dst)
        return "moved"
    except OSError:
        try:
            import shutil
            if src_is_dir:
                shutil.copytree(src, dst, dirs_exist_ok=True)
                shutil.rmtree(src, ignore_errors=True)
            else:
                shutil.copy2(src, dst)
                os.remove(src)
            return "moved"
        except Exception as e:
            return f"error:{e}"
    except Exception as e:
        return f"error:{e}"


def force_placeholder(src: str, dst: str) -> bool:
    """判断是否应视为"目标只是占位、需要迁移"。

    - 目标是目录：只要 src 是目录就继续合并（真目录/占位目录不区分）。
    - 目标是文件：src 大小 > dst 大小视为目标不完整（如空 db），需覆盖。
    """
    try:
        if os.path.isdir(src):
            return True
        if os.path.isdir(dst):
            return False
        return os.path.getsize(src) > os.path.getsize(dst)
    except OSError:
        return True


def migrate_legacy_to_mirror(force: bool = False) -> dict:
    """把仓库根/全局 data 目录下的旧单用户数据物理迁移到 users/Mirror/。

    幂等：完成后落标记文件；标记存在且非 force 时直接返回。
    目标已存在（如被 ensure_user_dirs 预建的空目录）时会合并内容，避免漏迁。
    有 error 项时不落标记，便于下次启动重试。返回 {name: moved|skipped|absent|error:...}。
    """
    result: Dict[str, str] = {}
    if os.path.exists(_MIGRATION_FLAG) and not force:
        return result

    my = user_paths(DEFAULT_USER)
    # 只预建 data/（放 db）；uploads/deliverables/rag/skills 交给迁移或首次访问创建。
    os.makedirs(my.data_dir, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)

    pairs = [
        ("memory.db", os.path.join(DATA_DIR, "memory.db"), my.db_path),
        ("checkpoints.db", os.path.join(DATA_DIR, "checkpoints.db"), my.checkpoint_db_path),
        ("lightrag_storage", os.path.join(BASE_DIR, "lightrag_storage"), my.rag_root),
        ("uploads", os.path.join(BASE_DIR, "uploads"), my.uploads_dir),
        ("deliverables", os.path.join(BASE_DIR, "deliverables"), my.deliverables_dir),
        ("image_library", os.path.join(DATA_DIR, "image_library"), os.path.join(my.data_dir, "image_library")),
        ("video_library", os.path.join(DATA_DIR, "video_library"), os.path.join(my.data_dir, "video_library")),
        ("video_ref", os.path.join(DATA_DIR, "video_ref"), os.path.join(my.data_dir, "video_ref")),
    ]
    for name, src, dst in pairs:
        result[name] = _migrate_item(src, dst)

    if not any(str(v).startswith("error:") for v in result.values()):
        try:
            os.makedirs(USERS_DIR, exist_ok=True)
            with open(_MIGRATION_FLAG, "w", encoding="utf-8") as f:
                f.write("ok\n")
        except Exception:
            pass
    return result


# ==================== 后台线程：继承用户上下文 ====================
def run_in_user_thread(username: Optional[str], fn, *args, **kwargs):
    """在后台线程里带着用户上下文运行 fn（子线程不会自动继承 ContextVar）。"""
    user = username or current_user()

    def _target():
        set_current_user(user)
        try:
            pair = get_user_llm(user)
            set_current_llm(pair)
        except Exception:
            pass
        try:
            fn(*args, **kwargs)
        except Exception:
            pass

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


# ==================== 密钥解析 ====================
def _split_keys(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [k.strip() for k in str(raw).split(",") if k.strip()]


def _dedup(keys: List[str]) -> List[str]:
    seen = set()
    out = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def global_agnes_keys() -> List[str]:
    """全局 .model_config 中 agnes 接入的 Key（逗号分隔多 Key）。"""
    from app import config_store
    cfg = config_store.get_config()
    for cm in cfg.get("custom", []) or []:
        base = str((cm or {}).get("base_url") or "")
        if "agnes-ai.cn" in base:
            return _split_keys(cm.get("api_key"))
    return []


def global_sf_keys() -> List[str]:
    """全局 .model_config 的 Embedding / Rerank Key（硅基流动）。"""
    from app import config_store
    cfg = config_store.get_config()
    emb = (cfg.get("embedding") or {}).get("api_key")
    rer = (cfg.get("rerank") or {}).get("api_key")
    return _dedup(_split_keys(emb) + _split_keys(rer))


def _all_active_user_keys() -> Tuple[List[str], List[str]]:
    from app.server import accounts
    agnes, sf = [], []
    try:
        for u in accounts.list_users():
            if u.get("status") != "active":
                continue
            full = accounts.get_user(u.get("username"), include_secrets=True) or {}
            agnes += _split_keys(full.get("agnes_key"))
            sf += _split_keys(full.get("siliconflow_key"))
    except Exception:
        pass
    return agnes, sf


def resolve_user_keys(username: Optional[str] = None) -> Dict[str, List[str]]:
    """返回 {agnes: [...], siliconflow: [...]}（管理员为全局池 + 所有已审批用户）。"""
    user = username or current_user()
    from app.server import accounts
    is_admin = False
    try:
        from app.server.auth import is_admin as _is_admin
        is_admin = _is_admin(user)
    except Exception:
        try:
            is_admin = accounts.is_admin(user)
        except Exception:
            is_admin = (user == DEFAULT_USER)

    if is_admin:
        ua, usf = _all_active_user_keys()
        return {
            "agnes": _dedup(global_agnes_keys() + ua),
            "siliconflow": _dedup(global_sf_keys() + usf),
        }

    full = {}
    try:
        full = accounts.get_user(user, include_secrets=True) or {}
    except Exception:
        full = {}
    return {
        "agnes": _split_keys(full.get("agnes_key")),
        "siliconflow": _split_keys(full.get("siliconflow_key")),
    }


# ==================== 每用户 LLM ====================
_llm_lock = threading.RLock()
_llm_cache: Dict[str, tuple] = {}


def _default_provider_spec() -> dict:
    from app import config_store
    cfg = config_store.get_config()
    provider = cfg.get("provider") or "openai_compatible"
    model = cfg.get("model") or ""
    entry = None
    for cm in cfg.get("custom", []) or []:
        if (cm or {}).get("id") == provider:
            entry = cm
            break
    return {"provider": provider, "model": model, "entry": entry or {},
            "custom": cfg.get("custom", []) or []}


def _build_llm_pair(username: str) -> tuple:
    """构造该用户的 (llm, llm_with_tools)。"""
    from app.llm import create_llm
    from app.tools import tools

    spec = _default_provider_spec()
    keys = resolve_user_keys(username)
    agnes_keys = keys["agnes"]

    admin = False
    try:
        from app.server.auth import is_admin as _is_admin
        admin = _is_admin(username)
    except Exception:
        try:
            from app.server import accounts
            admin = accounts.is_admin(username)
        except Exception:
            admin = (username == DEFAULT_USER)

    entry = spec["entry"]
    model = spec["model"]
    base_url = entry.get("base_url") or os.getenv("OPENAI_BASE_URL") or ""

    if not admin:
        # 普通用户只配置了 Agnes Key：固定走 agnes 接入网关。
        # 若管理员尚未配置 agnes 接入，则不能把用户 Key 发往未知网关 → 回退全局 LLM。
        agnes_entry = next((c for c in spec["custom"]
                            if "agnes-ai.cn" in str((c or {}).get("base_url") or "")), None)
        if not agnes_entry:
            from app.graph import builder as _b
            return (_b.llm, _b.llm_with_tools)
        entry = agnes_entry
        base_url = agnes_entry.get("base_url") or base_url
        models = agnes_entry.get("models") or []
        if model not in models:
            model = "agnes-2.5-flash" if "agnes-2.5-flash" in models else (models[0] if models else model)

    api_key = ",".join(agnes_keys) or os.getenv("OPENAI_API_KEY") or ""
    if not base_url or not api_key:
        # 缺凭据：回退到全局模块 LLM，避免整服务不可用
        from app.graph import builder as _b
        return (_b.llm, _b.llm_with_tools)

    llm = create_llm(provider="openai_compatible", model=model,
                     base_url=base_url, api_key=api_key)
    return (llm, llm.bind_tools(tools))


def get_user_llm(username: Optional[str] = None) -> tuple:
    user = username or current_user()
    with _llm_lock:
        cached = _llm_cache.get(user)
    if cached is not None:
        return cached
    pair = _build_llm_pair(user)
    with _llm_lock:
        _llm_cache[user] = pair
    return pair


def clear_llm_cache(username: Optional[str] = None) -> None:
    with _llm_lock:
        if username is None:
            _llm_cache.clear()
        else:
            _llm_cache.pop(username, None)


def set_current_llm(pair: Optional[tuple]):
    return _cur_llm.set(pair)


def reset_current_llm(token) -> None:
    try:
        _cur_llm.reset(token)
    except Exception:
        pass


def current_llm() -> Optional[tuple]:
    return _cur_llm.get()
