"""app/tools/skill_tools.py — 技能路由 + SkillHub 接入工具。

模型处理任务时如何使用技能：
  1. 本地技能元数据已注入 system prompt（见 prompt_template.txt 的 {{skills_section}}），
     模型可据此判断任务是否命中某个技能；
  2. 命中 → 调 read_skill(name) 读完整 SKILL.md，严格按其步骤执行；
  3. 本地无合适技能 → 调 search_skillhub(query) 到 SkillHub 在线市场找候选；
  4. 用户明确同意后 → 调 install_skill(hub_id) 下载到 app/skills/（立即生效）。
"""
import json

from langchain_core.tools import tool

from app.skills import loader
from app.skills.hub import search as _hub_search, install as _hub_install


@tool
def list_skills() -> str:
    """列出本地已安装的技能，每个技能一行：name — 描述（触发词）。
    当用户任务可能匹配某个技能、或想确认有哪些技能可用时调用。"""
    skills = loader.load_all_skills()
    if not skills:
        return "（本地暂无技能。可用 search_skillhub 到 SkillHub 查找）"
    lines = []
    for s in skills:
        trig = (f"（触发词: {', '.join(s['triggers'][:6])}）") if s.get("triggers") else ""
        lines.append(f"- {s['name']}: {s.get('description') or ''}{trig}")
    return "\n".join(lines)


@tool
def read_skill(name: str) -> str:
    """读取某个已安装技能的完整内容（SKILL.md：元数据 + 操作步骤正文），之后严格按其步骤执行。
    参数:
      name: 技能名（见 list_skills 输出）"""
    s = loader.load_skill((name or "").strip())
    if not s:
        return f"未找到技能 '{name}'。可用 list_skills 查看本地技能，或 search_skillhub 去 SkillHub 找。"
    head = f"技能: {s['name']}\n描述: {s['description']}\n触发词: {', '.join(s['triggers'])}"
    cfg = s.get("config") or {}
    if cfg:
        head += f"\n配置: {json.dumps(cfg, ensure_ascii=False)}"
    body = s.get("body") or "（本技能无正文，按 description 完成即可）"
    return head + "\n\n" + body


@tool
def search_skillhub(query: str) -> str:
    """到 SkillHub（在线技能市场）搜索可用技能。当本地技能无法满足用户需求时调用。
    参数:
      query: 搜索关键词（中英文均可）
    返回候选列表（含技能 id / 名称 / 作者 / 是否已安装）。
    找到合适候选后**先询问用户是否下载**，用户同意后再调 install_skill。"""
    results = _hub_search(query)
    if not results:
        return "SkillHub 没有返回结果，可换关键词重试。"
    if results and results[0].get("error"):
        return results[0]["error"]
    lines = []
    for r in results:
        mark = " [已安装]" if r.get("installed") else ""
        stars = f" ⭐{r['github_stars']}" if r.get("github_stars") else ""
        lines.append(f"- [{r['id']}] {r['name']}{mark}{stars} (by {r.get('author') or '?'})")
        lines.append(f"  描述: {(r.get('description') or '')[:150]}")
    lines.append("\n请把以上候选展示给用户，用户选定后我再调 install_skill 安装。")
    return "\n".join(lines)


@tool
def install_skill(hub_id: str) -> str:
    """把 SkillHub 上的技能下载安装到本地 app/skills/ 目录，安装后立即可用（无需重启）。
    参数:
      hub_id: search_skillhub 返回的候选技能 id（uuid），也可直接传技能名称（如 create-skill，
             会自动解析成对应 id）
    注意: 仅当用户明确同意下载时调用；安装成功后向用户确认。"""
    try:
        r = _hub_install((hub_id or "").strip())
    except Exception as e:
        return f"安装失败: {e}"
    return (f"已安装技能 [{r['name']}] 到 {r['path']}。"
            f"可调 read_skill('{r['name']}') 查看内容并按其步骤执行；"
            f"用户刷新调试面板的『技能』页也能看到。")
