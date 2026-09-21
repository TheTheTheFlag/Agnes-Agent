from typing import List, Optional

from .tavily_search import tavily_search
from .update_user_info import update_user_info
# from .system_command import system_command  # 已废弃：跨平台适配差，改用 Python 包装工具�?
from .update_user_preference import update_user_preference
from .request_planning import request_planning
from .search_my_memory import search_my_memory
from .list_my_recent_tasks import list_my_recent_tasks
from .get_command_history import get_command_history
from .file_ops import ls, read_file, write_file, edit_file, delete_file, glob_files, grep_files
from .execute_command import execute_command
from .skill_tools import list_skills, read_skill, search_skillhub, install_skill
from .graph_rag_tools import record_graph, lightgraph_query
from .scheduler_tools import (list_scheduled_tasks, create_scheduled_task,
                              update_scheduled_task, delete_scheduled_task,
                              run_scheduled_task_now)

# 工具列表：
#   - 文件操作（Python 包装，无需审批，限项目目录内）：ls / read_file / write_file / edit_file / delete_file / glob_files / grep_files
#   - 命令执行（需审批）：execute_command（替代 system_command）
#   - 写工具：update_user_info / update_user_preference / tavily_search
#   - 虚拟跳转工具：request_planning
#   - 读取工具（记忆）：search_my_memory / list_my_recent_tasks / get_command_history
tools = [
    ls,
    read_file,
    write_file,
    edit_file,
    delete_file,
    glob_files,
    grep_files,
    execute_command,
    update_user_info,
    update_user_preference,
    tavily_search,
    request_planning,
    search_my_memory,
    list_my_recent_tasks,
    get_command_history,
    # 技能路由 + SkillHub：list_skills / read_skill / search_skillhub / install_skill
    list_skills,
    read_skill,
    search_skillhub,
    install_skill,
    # L6 知识图谱：record_graph（显式建图）/ lightgraph_query（图谱检索）
    record_graph,
    lightgraph_query,
    # 定时任务（人人可用，按当前用户自己的表）：增删改查 + 立即执行
    list_scheduled_tasks,
    create_scheduled_task,
    update_scheduled_task,
    delete_scheduled_task,
    run_scheduled_task_now,
]

# 仅管理员可见的工具：命令执行（高风险）。
# 联网搜索（tavily_search）对所有用户开放，key 按当前用户从 accounts.db 读取
#（见 tavily_search.py：每用户自己的 users.extra.tavily_api_key）。
_ADMIN_ONLY_TOOLS = ("execute_command",)


def _role_is_admin(username: Optional[str]) -> bool:
    """按当前账号库判定是否管理员；无法查证时放行（管理员不能因鉴权模块故障被误伤）。"""
    if not username:
        return True
    try:
        from app.server.auth import is_admin as _is_admin
        return bool(_is_admin(username))
    except Exception:
        try:
            from app.server import accounts
            return bool(accounts.is_admin(username))
        except Exception:
            return True


def get_tools_for_user(username: Optional[str] = None) -> List:
    """某个用户可见的工具集：非管理员不提供 execute_command（风险命令）。

    联网搜索 tavily_search 面向所有用户开放，key 按用户各自读取；
    其余只读/记忆/文件工具全员可见。调用方需保证 username 语义正确
    （通常传 current_user()）；缺省时用当前上下文用户。
    """
    from app.userctx import current_user
    u = username or current_user()
    try:
        if _role_is_admin(u):
            return tools
    except Exception:
        pass
    return [t for t in tools if t.name not in _ADMIN_ONLY_TOOLS]
