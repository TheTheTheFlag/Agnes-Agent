"""
execute_command.py — 执行 shell 命令（跨平台用 Python subprocess 包装，含审批）。

替代旧的 system_command（直接在 shell 拼接命令，Windows/Linux 适配差）。
仍走人工审批（与 system_command 相同的审批机制）。

编码处理：Windows 控制台默认 GBK，子进程输出常非 UTF-8。
  - 给子进程注入 PYTHONUTF8=1（python 脚本会以 UTF-8 输出）；
  - 读取按字节，先试 UTF-8、失败回退 GBK（cp936），保证中文不乱码。
"""
import os
import subprocess, json
from langchain_core.tools import tool
from app.config import DB_PATH

# 子进程环境：python 脚本强制 UTF-8 输出（其余命令靠下方回退解码兜底）
_SUBPROC_ENV = dict(os.environ)
_SUBPROC_ENV.setdefault("PYTHONUTF8", "1")
_SUBPROC_ENV.setdefault("PYTHONIOENCODING", "utf-8")


def _decode(b: bytes) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


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
    # 命令可能绕过文件工具写核心路径（如 copy/del 到 static/data/.env 等）→ 拦截
    from app.tools.path_guard import command_targets_protected
    if command_targets_protected(command):
        return json.dumps({"error": "命令涉及受保护路径（app/server/static、data/、.env、.git 等），已拒绝执行。产出请写入 deliverables/。", "command": command}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, env=_SUBPROC_ENV, timeout=timeout
        )
        result["returncode"] = proc.returncode
        result["stdout"] = _decode(proc.stdout).strip()[:4000]
        result["stderr"] = _decode(proc.stderr).strip()[:2000]
        return json.dumps(result, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"命令超时（>{timeout}s）", "command": command}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "command": command}, ensure_ascii=False)
