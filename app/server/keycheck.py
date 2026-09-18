"""app.server.keycheck — 校验用户提交的 Agnes / 硅基流动 API Key 是否可用。

在注册时做一次轻量校验（请求各平台的模型列表接口）：
  - 返回 ("ok", "")        验证通过
  - 返回 ("invalid", msg)  明确无效（401/403 等），注册会被拒绝
  - 返回 ("unknown", msg)  平台不可达/超时等，无法确认（注册放行，交给管理员人工审批）

校验用 requests（同步）执行，调用方需放进线程避免阻塞事件循环。
"""
from __future__ import annotations

from typing import List, Tuple

AGNES_BASE = "https://api.agnes-ai.cn/v1"
SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"

_AGNES_ENDPOINTS = [f"{AGNES_BASE}/models"]
_SILICONFLOW_ENDPOINTS = [f"{SILICONFLOW_BASE}/models", f"{SILICONFLOW_BASE}/user/info"]


def _probe(url: str, key: str, timeout: float = 10.0) -> Tuple[str, str]:
    """请求单个端点，返回 (verdict, detail)。verdict ∈ {"ok","invalid","unknown"}。"""
    import requests

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=timeout,
        )
    except Exception as e:
        return "unknown", f"{type(e).__name__}: {e}"
    if resp.status_code == 200:
        return "ok", ""
    if resp.status_code in (401, 403):
        return "invalid", f"HTTP {resp.status_code}"
    if resp.status_code == 404:
        return "unknown", "HTTP 404"
    return "unknown", f"HTTP {resp.status_code}"


def validate_key(endpoints: List[str], key: str) -> Tuple[str, str]:
    """依次探测候选端点：任一 ok 即通过；否则优先报告 invalid，其次 unknown。"""
    if not key or not key.strip():
        return "invalid", "未填写"
    saw_invalid: str = ""
    saw_unknown: str = ""
    for url in endpoints:
        verdict, detail = _probe(url, key.strip())
        if verdict == "ok":
            return "ok", ""
        if verdict == "invalid" and not saw_invalid:
            saw_invalid = detail
        if verdict == "unknown" and not saw_unknown:
            saw_unknown = detail
    if saw_invalid:
        return "invalid", saw_invalid
    return "unknown", saw_unknown or "无可用端点"


def validate_agnes(key: str) -> Tuple[str, str]:
    return validate_key(_AGNES_ENDPOINTS, key)


def validate_siliconflow(key: str) -> Tuple[str, str]:
    return validate_key(_SILICONFLOW_ENDPOINTS, key)
