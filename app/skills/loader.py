"""app/skills/loader.py — 本地技能加载器。

技能 = app/skills/ 目录下的一个 SKILL.md（或任意 .md），结构：
  ---            # YAML frontmatter（元数据）
  name: xxx
  description: ...
  triggers: [...]   # 可选，触发词
  config: {...}     # 可选，技能配置
  ---
  正文...          # 可选，给模型的操作步骤指引

扫描规则（两种放法都支持）：
  - 目录式：app/skills/<name>/SKILL.md（推荐，可附带其他资源文件）
  - 单文件：app/skills/<name>.md

loader 每次调用实时扫描（无缓存），因此往 app/skills/ 新增/修改 SKILL.md 立即生效，
不需要重启（热重载只监控 .py，不监控 .md，这里动态读正好补上）。
"""
import os
import re

import yaml

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../app
PROJECT_ROOT = os.path.dirname(APP_DIR)
SKILLS_DIR = os.path.join(APP_DIR, "skills")

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse(raw: str) -> tuple[dict, str]:
    """解析 SKILL.md：返回 (frontmatter 元数据 dict, 正文)。无 frontmatter 时返回 ({}, 全文)。"""
    m = _FM_RE.match(raw)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}
        body = raw[m.end():].strip()
        return meta, body
    return {}, raw.strip()


def _skill_dir_files() -> list[tuple[str, str]]:
    """返回 [(name, abs_path)]：name 取 frontmatter 的 name，否则用文件/目录名。"""
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for entry in sorted(os.listdir(SKILLS_DIR)):
        if entry.startswith("__") or entry.startswith("."):
            continue
        full = os.path.join(SKILLS_DIR, entry)
        if os.path.isdir(full):
            for fname in ("SKILL.md", "skill.md"):
                fp = os.path.join(full, fname)
                if os.path.isfile(fp):
                    meta, _ = _parse(_read(fp))
                    name = (meta.get("name") or entry).strip() or entry
                    out.append((name, fp))
                    break
        elif entry.lower().endswith(".md"):
            meta, _ = _parse(_read(full))
            name = (meta.get("name") or os.path.splitext(entry)[0]).strip() or entry
            out.append((name, full))
    return out


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_all_skills() -> list[dict]:
    """返回所有本地技能元数据（不含正文），供路由 / 展示 / prompt 注入。"""
    skills = []
    for name, path in _skill_dir_files():
        raw = _read(path)
        meta, body = _parse(raw)
        skills.append({
            "name": name,
            "description": meta.get("description") or "",
            "triggers": meta.get("triggers") or [],
            "config": meta.get("config") or {},
            "path": os.path.relpath(path, PROJECT_ROOT),
            "has_body": bool(body),
        })
    return skills


def load_skill(name: str) -> dict | None:
    """读取单个技能：元数据 + 完整原文（frontmatter+正文），模型执行时用。"""
    for n, path in _skill_dir_files():
        if n == name:
            raw = _read(path)
            meta, body = _parse(raw)
            return {
                "name": n,
                "description": meta.get("description") or "",
                "triggers": meta.get("triggers") or [],
                "config": meta.get("config") or {},
                "body": body,
                "raw": raw,
                "path": os.path.relpath(path, PROJECT_ROOT),
            }
    return None


def skill_exists(name: str) -> bool:
    return any(n == name for n, _ in _skill_dir_files())


def install_skill_md(name: str, raw: str) -> str:
    """把 SKILL.md 原文安装到 app/skills/<name>/SKILL.md。name 做安全清洗。"""
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", name).strip("._")
    if not safe:
        raise ValueError("skill 名称非法")
    d = os.path.join(SKILLS_DIR, safe)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, "SKILL.md")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(raw)
    return os.path.relpath(fp, PROJECT_ROOT)
