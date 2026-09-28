"""
execute_command.py — 执行 shell 命令（跨平台用 Python subprocess 包装，含审批）。

替代旧的 system_command（直接在 shell 拼接命令，Windows/Linux 适配差）。
仍走人工审批（与 system_command 相同的审批机制）。

编码处理：Windows 控制台默认 GBK，子进程输出常非 UTF-8。
  - 给子进程注入 PYTHONUTF8=1（python 脚本会以 UTF-8 输出）；
  - 读取按字节，先试 UTF-8、失败回退 GBK（cp936），保证中文不乱码。

超时处理：命令以独立进程组（start_new_session=True）启动，超时后对整组
  SIGTERM → 3s → SIGKILL，保证 shell 派生出的子/孙进程一并终结（避免孤儿
  进程继续后台运行、被误判为"任务已死"而触发重复执行）。返回 killed=True。
  长任务请改用后台模式：nohup python3 x.py > x.log 2>&1 & 再轮询日志。
"""
import os
import signal
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


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """终止以 proc 为组长的整条进程组：SIGTERM → 最多等 3s → SIGKILL。"""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    for sig, wait_after in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, OSError):
                break  # 整组已退出
        else:
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                break
        try:
            proc.wait(timeout=wait_after)
            break
        except subprocess.TimeoutExpired:
            continue


@tool
def execute_command(command: str, timeout: int = 60) -> str:
    """执行一条 shell 命令，返回结构化 JSON 结果。

    参数:
      command: 要执行的命令（Windows 用 cmd 语法，如 'dir'；Linux 用 'ls -la'）
      timeout: 超时秒数（默认 60）

    返回: {"command", "returncode", "stdout", "stderr"}
    注意: 该工具需要人工审批；rm -rf 只允许删除工作区内的路径（工作区外与工作区根一律拒绝）。
      命令超时后整条进程组会被强制终止（返回 killed=True）；长任务请用 nohup 后台化。
    """
    result = {"command": command, "returncode": 0, "stdout": "", "stderr": ""}
    # 命令可能绕过文件工具写核心路径（如 copy/del 到 static/data/.env 等）→ 拦截
    from app.tools.path_guard import command_targets_protected, rm_recursive_outside_workspace
    if command_targets_protected(command):
        return json.dumps({"error": "命令涉及受保护路径（app/server/static、data/、.env、.git 等），已拒绝执行。产出请写入 deliverables/。", "command": command}, ensure_ascii=False)
    _esc = rm_recursive_outside_workspace(command)
    if _esc:
        return json.dumps({"error": f"递归删除目标「{_esc}」不在你的工作区内，已拒绝。工作区内绝对路径可自由删除。", "command": command}, ensure_ascii=False)
    try:
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_SUBPROC_ENV, start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
            return json.dumps({
                "error": (
                    f"命令超时（>{timeout}s），已终止整条进程组（含所有子进程）。"
                    "长任务不要前台等待：请改用后台模式——"
                    "nohup python3 脚本.py > 脚本.log 2>&1 &（命令立即返回），"
                    "再用 read_file 轮询 脚本.log 查看进度。"
                ),
                "command": command,
                "killed": True,
                "partial_stdout": _decode(out).strip()[:1000],
            }, ensure_ascii=False)
        result["returncode"] = proc.returncode
        result["stdout"] = _decode(out).strip()[:4000]
        result["stderr"] = _decode(err).strip()[:2000]
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "command": command}, ensure_ascii=False)
