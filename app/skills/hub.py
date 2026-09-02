"""app/skills/hub.py — SkillHub（https://www.skillhub.club）客户端。

免 key 可用端点（探测确认）：
  - 搜索: GET https://www.skillhub.club/api/search?q=<query>
  - 详情: GET https://www.skillhub.club/api/v1/skills/<id>   （含 skill_md_raw 完整原文）

安装流程：搜索 → 用户同意 → 详情取 skill_md_raw → 写入 app/skills/<name>/SKILL.md。
"""
import os

import requests

HUB_BASE = "https://www.skillhub.club"
TIMEOUT = 20

from app.skills.loader import skill_exists, install_skill_md


def _get(url: str, **kw) -> dict:
    r = requests.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def search(query: str, limit: int = 8) -> list[dict]:
    """搜索 SkillHub，返回裁剪后的候选（含本地是否已装标记）。"""
    q = (query or "").strip()
    if not q:
        return []
    try:
        data = _get(f"{HUB_BASE}/api/search", params={"q": q})
    except Exception as e:
        return [{"error": f"SkillHub 搜索失败: {e}"}]
    skills = data.get("skills") or []
    out = []
    for s in skills[:limit]:
        sid = s.get("id", "")
        name = s.get("name", "")
        out.append({
            "id": sid,
            "name": name,
            "slug": s.get("slug", ""),
            "author": s.get("author", ""),
            "description": (s.get("description_zh") or s.get("description") or "")[:300],
            "github_stars": s.get("github_stars"),
            "composite_score": s.get("composite_score"),
            "repo_url": s.get("repo_url", ""),
            "installed": skill_exists(name) if name else False,
        })
    return out


def fetch_detail(skill_id: str) -> dict:
    """拉取单个技能详情（含 skill_md_raw 完整 SKILL.md 原文）。"""
    data = _get(f"{HUB_BASE}/api/v1/skills/{skill_id}")
    s = (data or {}).get("skill") or {}
    return {
        "id": s.get("id", skill_id),
        "name": s.get("name", ""),
        "slug": s.get("slug", ""),
        "author": s.get("author", ""),
        "description": (s.get("description_zh") or s.get("description") or "")[:500],
        "repo_url": s.get("repo_url", ""),
        "skill_md_raw": s.get("skill_md_raw") or "",
        "skill_path": s.get("skill_path", ""),
    }


def install(skill_id: str) -> dict:
    """按 id 安装技能到本地：详情 → 写 app/skills/<name>/SKILL.md。"""
    d = fetch_detail(skill_id)
    raw = d.get("skill_md_raw") or ""
    if not raw:
        raise ValueError(f"该技能无内容可下载（可能未开源）：{d.get('name', skill_id)}")
    rel = install_skill_md(d.get("name") or "skill", raw)
    return {"name": d.get("name"), "path": rel}
