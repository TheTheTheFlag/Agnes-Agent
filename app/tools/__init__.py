from .tavily_search import tavily_search
from .update_user_info import update_user_info
# from .system_command import system_command  # 已废弃：跨平台适配差，改用 Python 包装工具集
from .update_user_preference import update_user_preference
from .validate_html import validate_html
from .request_planning import request_planning
from .search_my_memory import search_my_memory
from .list_my_recent_tasks import list_my_recent_tasks
from .get_command_history import get_command_history
from .file_ops import ls, read_file, write_file, edit_file, delete_file, glob_files, grep_files
from .execute_command import execute_command

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
    validate_html,
    tavily_search,
    request_planning,
    search_my_memory,
    list_my_recent_tasks,
    get_command_history,
]
