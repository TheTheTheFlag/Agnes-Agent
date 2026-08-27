"""
execute_command.py — 执行 shell 命令（跨平台用 Python subprocess 包装，含审批）。

替代旧的 system_command（直接在 shell 拼接命令，Windows/Linux 适配差）。
仍走人工审批（与 system_command 相同的审批机制）。
"""
import subprocess, json
from langchain_core.tools import tool
from app.config import DB_PATH


@tool
def execute_command(command: str, timeout: int = 60) -> str:
    """执行一条 shell 命令，返回结构化 JSON 结果。

    参数:
      command: 要执行的命令（Windows 用 cmd 语法，如 'dir'；Linux 用 'ls -la'）
      timeout: 超时秒数（默认 60）

    返回: {"command", "returncode", "stdout", "stderr"}
    注意: 该工具需要人工审批；请避免使用危险命令（rm -rf / 等）。
    """
    result = {"command": command, "returncode": 0, "stdout": "", "stderr": ""}
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, encoding='utf-8', errors='replace'
        )
        result["returncode"] = proc.returncode
        result["stdout"] = (proc.stdout or "").strip()[:4000]
        result["stderr"] = (proc.stderr or "").strip()[:2000]
        return json.dumps(result, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"命令超时（>{timeout}s）", "command": command}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "command": command}, ensure_ascii=False)
