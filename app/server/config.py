"""app.server.config — 模型配置管理（厂商目录 / 凭据存储 / 默认模型 / 运行时全局）。"""
import json
import os as _os
import threading as _threading
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import MODEL_CONFIG_PATH as _MODEL_CONFIG_FILE

router = APIRouter()

# 运行时全局（由 main.py 注入 graph 与 config）
_GRAPH = None
_CONFIG = None

# 每用户 graph 注册表（多用户隔离核心）。缺省用户（Mirror/admin）沿用 _GRAPH，
# 保证既有测试用 set_graph(FakeGraph) 覆盖 _GRAPH 的写法继续有效。
_GRAPHS: dict = {}
_GRAPHS_LOCK = _threading.RLock()


def _build_user_graph(user: str):
    """在指定用户上下文里构建 graph（节点闭包捕获该用户的 LLM，DB 路径经代理解析）。"""
    from app.userctx import (set_current_user, reset_current_user,
                             set_current_llm, reset_current_llm, get_user_llm,
                             ensure_user_dirs)
    from app.graph.builder import build_graph
    ensure_user_dirs(user)
    tok = set_current_user(user)
    ltok = set_current_llm(get_user_llm(user))
    try:
        return build_graph()
    finally:
        reset_current_llm(ltok)
        reset_current_user(tok)


def get_graph(username: Optional[str] = None):
    """取当前用户的 graph；首次访问时构建并缓存。"""
    from app.userctx import current_user
    user = username or current_user()
    with _GRAPHS_LOCK:
        g = _GRAPHS.get(user)
        if g is not None:
            return g
        g = _build_user_graph(user)
        _GRAPHS[user] = g
        return g


def clear_user_graphs():
    """清空每用户 graph 缓存（切换模型 / 用户密钥变更后调用）。"""
    with _GRAPHS_LOCK:
        _GRAPHS.clear()

# 内置来源目录已弃用（置空）：模型目录完全由自定义接入（.model_config 的 custom 列表）驱动，
# 避免模型页出现"内置"标签——所有来源统一以"自定义"呈现。凭据/模型均由用户在调试面板接入。
MODEL_CATALOG = {}

from app.config import MODEL_CONFIG_PATH as _MODEL_CONFIG_FILE

# 当前生效的模型（由 main.py 启动时 set_graph 前调用 apply_model_config 设置）
_current_model = {"provider": "openai_compatible", "model": "deepseek-v4-pro"}


def _load_model_file() -> dict:
    """读取 .model_config 完整内容（含 custom 列表）。"""
    try:
        with open(_MODEL_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {"provider": "openai_compatible", "model": "deepseek-v4-pro", "custom": []}


def _save_model_file(cfg: dict):
    try:
        with open(_MODEL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_model_config() -> dict:
    """返回当前默认模型（兼容 main.py 用法）。"""
    cfg = _load_model_file()
    provider = cfg.get("provider", "openai_compatible")
    model = cfg.get("model", "deepseek-v4-pro")
    return {"provider": provider, "model": model}


def save_model_config(provider: str, model: str):
    cfg = _load_model_file()
    cfg["provider"] = provider
    cfg["model"] = model
    _save_model_file(cfg)


def get_custom_models() -> list:
    """返回自定义模型接入列表。"""
    cfg = _load_model_file()
    return cfg.get("custom", []) or []


def save_custom_models(custom: list):
    cfg = _load_model_file()
    cfg["custom"] = custom
    _save_model_file(cfg)


def get_full_catalog() -> dict:
    """合并内置厂商 + 自定义接入，得到页面完整目录。
    内置目录（MODEL_CATALOG）已弃用置空，目录完全由自定义接入（.model_config custom）构成，
    全部以"自定义"呈现（builtin=False）。"""
    catalog = {}
    for pid, info in MODEL_CATALOG.items():
        catalog[pid] = dict(info, builtin=True, custom=False)
    for cm in get_custom_models():
        pid = cm.get("id")
        if pid:
            catalog[pid] = {
                "label": cm.get("label", pid),
                "models": cm.get("models", []),
                "base_url": cm.get("base_url", ""),
                "api_key": cm.get("api_key", ""),
                "builtin": pid in MODEL_CATALOG,
                "custom": True,
            }
    return catalog


def get_current_model() -> dict:
    # 懒初始化：首次读取时同步 .model_config 里的默认模型
    if not _current_model.get("_initialized"):
        cfg = load_model_config()
        _current_model.clear()
        _current_model.update({"provider": cfg["provider"], "model": cfg["model"], "_initialized": True})
    return {"provider": _current_model["provider"], "model": _current_model["model"]}


def resolve_provider_env(provider: str) -> str:
    """把任意 provider 解析为 create_llm 能用的 provider 名 + 注入 OPENAI_BASE_URL/OPENAI_API_KEY。

    .model_config 不再存任何 api_key：注入环境变量时 api_key 优先取数据库全库 agnes 池
    （resolve_user_keys），配置里的残留 api_key 作兜底。返回 openai_compatible。"""
    catalog = get_full_catalog()
    info = catalog.get(provider)
    base_url = (info or {}).get("base_url")
    if base_url:
        _os.environ["OPENAI_BASE_URL"] = base_url
    keys: list = []
    try:
        from app.userctx import resolve_user_keys
        keys = resolve_user_keys().get("agnes") or []
    except Exception:
        keys = []
    if not keys and info and info.get("api_key"):
        keys = [k.strip() for k in str(info["api_key"]).split(",") if k.strip()]
    if keys:
        _os.environ["OPENAI_API_KEY"] = ",".join(keys)
    return "openai_compatible"


def rebuild_graph_with_model(provider: str, model: str):
    """切换模型：重建 llm + graph，注入 _GRAPH。返回新模型信息。
    支持自定义 openai 格式接入（provider 是自定义 id 时注入其 base_url/api_key）。"""
    global _GRAPH, _current_model
    catalog = get_full_catalog()
    if provider not in catalog:
        raise ValueError(f"不支持的厂商: {provider}")
    if model not in catalog[provider]["models"]:
        raise ValueError(f"厂商 {provider} 不支持模型: {model}")
    from app.llm import create_llm
    from app.graph import builder as g_mod
    from app.tools import tools

    real_provider = resolve_provider_env(provider)
    save_model_config(provider, model)   # 先落盘：每用户 LLM 构建时读取新默认模型
    _current_model = {"provider": provider, "model": model}
    # 模块级 llm 兜底也要有 key：.model_config 不再存密钥，注入当前用户的 DB agnes 池
    try:
        from app.userctx import current_user as _cu, resolve_user_keys as _rku
        _keys = _rku(_cu()).get("agnes") or []
        if _keys:
            _os.environ["OPENAI_API_KEY"] = ",".join(_keys)
    except Exception:
        pass
    # 重建模块级 llm（兜底：无用户上下文时 chatbot/planner 使用）
    g_mod.llm = create_llm(provider=real_provider, model=model)
    g_mod.llm_with_tools = g_mod.llm.bind_tools(tools)
    # 每用户 LLM/graph 依赖全局默认模型：全部失效后按用户重建
    try:
        from app.userctx import clear_llm_cache, current_user
        clear_llm_cache()
        clear_user_graphs()
        new_graph = _build_user_graph(current_user())
    except Exception:
        new_graph = g_mod.build_graph()
    _GRAPH = new_graph
    return dict(_current_model)


def _is_request_admin(request: Optional[Request]) -> bool:
    user = getattr(getattr(request, "state", None), "username", None)
    if not user:
        return True  # 无请求上下文（内部/测试调用）按管理员处理
    try:
        from app.server.auth import is_admin
        return is_admin(user)
    except Exception:
        try:
            from app.server import accounts
            return accounts.is_admin(user)
        except Exception:
            return False


def _mask_catalog(catalog: dict) -> dict:
    out = {}
    for pid, info in (catalog or {}).items():
        info = dict(info or {})
        key = info.get("api_key") or ""
        if key:
            info["api_key"] = ("****" + key[-4:]) if len(key) > 4 else "****"
        out[pid] = info
    return out


@router.get("/api/models")
async def get_models(request: Request = None):
    catalog = get_full_catalog()
    if not _is_request_admin(request):
        catalog = _mask_catalog(catalog)
    return {
        "catalog": catalog,
        "current": get_current_model(),
    }


@router.post("/api/models/fetch")
def fetch_models(payload: dict, request: Request = None):
    """从 OpenAI 兼容网关拉取可用模型列表（GET {base_url}/models）。
    payload: {base_url, api_key}  api_key 支持逗号分隔多 key（逐个尝试）"""
    if not _is_request_admin(request):
        return JSONResponse({"error": "仅管理员可探测模型网关"}, status_code=403)
    base_url = ((payload or {}).get("base_url") or "").strip().rstrip("/")
    api_key = ((payload or {}).get("api_key") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        return JSONResponse({"error": "base_url 必须是 http(s):// 开头的完整地址"}, status_code=400)
    keys = [k.strip() for k in api_key.split(",") if k.strip()] or [None]
    import requests as _req
    last_err = ""
    for k in keys:
        try:
            headers = {"Authorization": f"Bearer {k}"} if k else {}
            resp = _req.get(base_url + "/models", headers=headers, timeout=10)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {resp.text[:150]}"
                continue
            data = resp.json()
            models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
            if not models:
                last_err = "返回的 data 为空"
                continue
            return {"ok": True, "models": sorted(models)}
        except Exception as e:
            last_err = str(e)[:150]
            continue
    return JSONResponse({"error": f"获取失败: {last_err or '未知错误'}"}, status_code=502)


@router.post("/api/switch-model")
async def switch_model(payload: dict, request: Request = None):
    if not _is_request_admin(request):
        return JSONResponse({"error": "仅管理员可切换全局模型"}, status_code=403)
    p = payload or {}
    provider = p.get("provider", "")
    model = p.get("model", "")
    try:
        info = rebuild_graph_with_model(provider, model)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"切换失败: {e}"}, status_code=500)
    return {"ok": True, "current": info, "note": "已重建 graph，页面后续对话使用新模型"}


@router.post("/api/models/add")
async def add_model(payload: dict, request: Request = None):
    """新增自定义 openai 格式模型接入。payload: {label, base_url, api_key, models}"""
    if not _is_request_admin(request):
        return JSONResponse({"error": "仅管理员可新增全局模型接入"}, status_code=403)
    p = payload or {}
    label = (p.get("label") or "").strip()
    base_url = (p.get("base_url") or "").strip()
    api_key = (p.get("api_key") or "").strip()
    models_raw = p.get("models", [])
    if isinstance(models_raw, str):
        models_raw = [m.strip() for m in models_raw.replace("\n", ",").split(",") if m.strip()]
    models = [str(m).strip() for m in models_raw if str(m).strip()]
    if not label:
        return JSONResponse({"error": "请填写模型名称"}, status_code=400)
    if not base_url or not base_url.startswith(("http://", "https://")):
        return JSONResponse({"error": "base_url 必须是 http(s):// 开头的完整地址"}, status_code=400)
    if not models:
        return JSONResponse({"error": "请至少填一个模型名"}, status_code=400)

    import uuid as _uuid
    pid = "custom-" + label.lower().replace(" ", "-") + "-" + _uuid.uuid4().hex[:4]
    custom = get_custom_models()
    entry = {
        "id": pid,
        "label": label,
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
    }
    if "agnes-ai.cn" in base_url:
        # agnes 网关的 key 已迁入 accounts.db：锚定当前管理员的 agnes_key，不入 .model_config
        _raw_key = api_key
        entry["api_key"] = ""
        api_key = ""
        try:
            from app.server import accounts
            _by_username = getattr(getattr(request, "state", None), "username", None)
            from app.userctx import DEFAULT_USER
            _uname = _by_username or DEFAULT_USER
            _full = accounts.get_user(_uname, include_secrets=True) or {}
            _cur = [k.strip() for k in str(_full.get("agnes_key") or "").split(",") if k.strip()]
            _key = _raw_key.strip()
            if _key:
                _cur = [_key]
            accounts.set_keys(_uname, ",".join(_cur), str(_full.get("siliconflow_key") or ""))
        except Exception:
            pass
    custom.append(entry)
    save_custom_models(custom)
    return {"ok": True, "model": custom[-1], "note": "已接入。可在设置中设为默认，或在上方下拉切换。"}


@router.post("/api/models/delete")
async def delete_model(payload: dict, request: Request = None):
    """删除自定义模型接入。若当前正在用它，回退到默认模型。"""
    if not _is_request_admin(request):
        return JSONResponse({"error": "仅管理员可删除全局模型接入"}, status_code=403)
    pid = (payload or {}).get("id", "")
    custom = get_custom_models()
    remaining = [c for c in custom if c.get("id") != pid]
    if len(remaining) == len(custom):
        return JSONResponse({"error": f"未找到自定义模型: {pid}"}, status_code=404)
    save_custom_models(remaining)
    if _current_model.get("provider") == pid:
        cfg = load_model_config()
        try:
            rebuild_graph_with_model(cfg["provider"], cfg["model"])
        except Exception:
            try:
                rebuild_graph_with_model("agnes2.0", "agnes-2.5-flash")
            except Exception:
                pass
    return {"ok": True, "note": "已删除"}


@router.post("/api/models/set-default")
async def set_default_model(payload: dict, request: Request = None):
    """设置默认模型（写入 .model_config，下次启动生效 + 立即重建）。"""
    if not _is_request_admin(request):
        return JSONResponse({"error": "仅管理员可设置全局默认模型"}, status_code=403)
    provider = (payload or {}).get("provider", "")
    model = (payload or {}).get("model", "")
    catalog = get_full_catalog()
    if provider not in catalog:
        return JSONResponse({"error": f"不支持的厂商: {provider}"}, status_code=400)
    if model not in catalog[provider]["models"]:
        return JSONResponse({"error": f"厂商 {provider} 不支持模型: {model}"}, status_code=400)
    try:
        info = rebuild_graph_with_model(provider, model)
    except Exception as e:
        return JSONResponse({"error": f"切换失败: {e}"}, status_code=500)
    return {"ok": True, "current": info, "note": "已设为默认模型，下次启动自动使用"}


