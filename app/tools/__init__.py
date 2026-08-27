from .tavily_search import tavily_tool
from .update_user_info import update_user_info
from .system_command import system_command
from .update_user_preference import update_user_preference
from .validate_html import validate_html
from .request_planning import request_planning
from .search_my_memory import search_my_memory
from .list_my_recent_tasks import list_my_recent_tasks
from .get_command_history import get_command_history

# 工具列表：所有工具（除 system_command 需审批）都对模型可见
# 写工具：update_user_info / update_user_preference / system_command / tavily_tool
# 虚拟跳转工具：request_planning
# 读取工具（记忆）：search_my_memory / list_my_recent_tasks / get_command_history
tools = [
    system_command,
    update_user_info,
    update_user_preference,
    validate_html,
    tavily_tool,
    request_planning,
    search_my_memory,
    list_my_recent_tasks,
    get_command_history,
]
