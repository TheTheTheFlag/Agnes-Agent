"""app/skills — 本地技能系统。

用户把技能放进本目录（见 loader.py：目录式 <name>/SKILL.md 或单文件 <name>.md），
Agent 通过元数据路由，调试面板"技能"页可浏览。
技能来源可为手工编写，或由 agent 从 SkillHub 搜索后经用户同意下载安装。
"""
from . import loader

__all__ = ["loader"]
