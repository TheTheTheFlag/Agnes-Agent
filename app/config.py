"""
app.config — 全局配置中心

统一管理所有路径、数据库位置、模型配置来源。
数据文件（memory.db / checkpoints.db / model_config）统一存放在 data/ 目录。

多用户隔离：`DB_PATH` / `CHECKPOINT_DB_PATH` 是**上下文感知的路径代理**
（`_UserPath`），在运行时按当前用户解析为 `data/users/<username>/data/...`。
历史上直接 `from app.config import DB_PATH` 的 60+ 处代码无需改动：
它们把 DB_PATH 传给 sqlite3.connect / open / os.path 时，代理会实时解析。
"""
import os

# 项目根目录（app/ 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录（数据库 + 模型配置 + 每用户子目录）
DATA_DIR = os.path.join(BASE_DIR, "data")

# 静态资源目录（前端 HTML/CSS/JS）
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "static")

# 提示词模板
PROMPT_TEMPLATE_PATH = os.path.join(BASE_DIR, "app", "graph", "prompt_template.txt")

# 全局模型配置（管理员维护，不随用户变化）
MODEL_CONFIG_PATH = os.path.join(DATA_DIR, ".model_config")


class _UserPath(os.PathLike):
    """按当前用户解析的路径代理（path-like，可直接传给 sqlite3/open/os.path）。

    可附加子路径 parts；注意 `os.path.join(proxy, ...)` 会**立即**解析，
    因此模块级常量应使用 proxy 本身（含 parts），不要对其再 join。
    """

    __slots__ = ("_kind", "_parts")

    def __init__(self, kind: str, *parts: str):
        self._kind = kind
        self._parts = tuple(str(p) for p in parts if p != "")

    def _resolve(self) -> str:
        # 延迟导入，避免 app.config <-> app.userctx 循环依赖
        from app.userctx import resolve_path_prop
        base = resolve_path_prop(self._kind)
        return os.path.join(base, *self._parts) if self._parts else base

    def __fspath__(self) -> str:
        return self._resolve()

    def __str__(self) -> str:
        return self._resolve()

    def __repr__(self) -> str:  # noqa: D105
        return self._resolve()

    def __eq__(self, other):
        return self._resolve() == (other._resolve() if isinstance(other, _UserPath) else other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._resolve())

    def __bool__(self) -> bool:
        return True


# 数据库 / 用户目录（按当前用户解析）
DB_PATH = _UserPath("db_path")                          # 记忆 SQLite
CHECKPOINT_DB_PATH = _UserPath("checkpoint_db_path")    # LangGraph checkpoint
WORKSPACE_DIR = _UserPath("workspace")                  # 用户工作区根
UPLOADS_DIR = _UserPath("uploads_dir")                  # 用户上传目录
DELIVERABLES_DIR = _UserPath("deliverables_dir")        # 用户交付物目录
USER_DATA_DIR = _UserPath("data_dir")                   # 用户 data/（媒体库等）
RAG_ROOT = _UserPath("rag_root")                        # 用户 lightrag_storage
SKILLS_DIR = _UserPath("skills_dir")                    # 用户已安装技能目录


def user_subdir(kind: str, *parts: str) -> "_UserPath":
    """构造「当前用户某目录下的子路径」代理（模块级常量安全）。"""
    return _UserPath(kind, *parts)


def ensure_dirs():
    """确保必要的目录存在。"""
    for d in (DATA_DIR, STATIC_DIR):
        os.makedirs(d, exist_ok=True)
