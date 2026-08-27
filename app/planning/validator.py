import sys, os, json, subprocess, re
from typing import Dict, Any
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.graph.state import State
from app.server import add_log_entry, add_event
from app.config import DB_PATH


def create_validator_node():
    def validator_node(state: State):
        print("[Validator] 进入")
        from app.memory import MemoryManager
        # 防御性：thread_id 在 LangGraph 条件边传递时可能丢失
        thread_id = state.get("thread_id") or state.get("_thread_id") or "default"
        mm = MemoryManager(db_path=DB_PATH, thread_id=thread_id)
        plan_meta = mm.get_task_plan_by_thread(thread_id)
        if not plan_meta:
            state["_validation_passed"] = False
            state["_validation_message"] = "无任务计划"
            return state

        subtasks = mm.get_subtasks(plan_meta["id"])
        all_artifacts = []
        for sub in subtasks:
            if sub.get("artifacts"):
                all_artifacts.extend(sub["artifacts"])
        all_artifacts = list(set(all_artifacts))

        if not all_artifacts:
            state["_validation_passed"] = False
            state["_validation_message"] = "无产出文件"
            return state

        missing = [f for f in all_artifacts if not os.path.exists(f)]
        if missing:
            state["_validation_passed"] = False
            state["_validation_message"] = f"文件缺失: {', '.join(missing)}"
            return state

        errors, warnings = [], []
        for file in all_artifacts:
            result = validate_file(file)
            if result["level"] == "error":
                errors.append(result["message"])
            elif result["level"] == "warning":
                warnings.append(result["message"])

        state["_validation_passed"] = len(errors) == 0
        msg = []
        if errors:
            msg.append("❌ 错误:\n" + "\n".join(errors))
        if warnings:
            msg.append("⚠️ 警告:\n" + "\n".join(warnings))
        if state["_validation_passed"] and not warnings:
            msg.append("✅ 全部通过")
        state["_validation_message"] = "\n\n".join(msg)

        print(f"[Validator] {'通过' if state['_validation_passed'] else '失败'}")
        add_log_entry("info", f"验证: {'通过' if state['_validation_passed'] else '失败'}", {"passed": state["_validation_passed"]})
        add_event("validator", {"passed": state["_validation_passed"]})
        return state
    return validator_node

def validate_file(file_path: str) -> Dict[str, str]:
    ext = os.path.splitext(file_path)[1].lower()
    size = os.path.getsize(file_path)

    # 二进制文件跳过
    binary = ['.png','.jpg','.jpeg','.gif','.bmp','.ico','.webp','.mp4','.avi','.mov','.mkv',
              '.mp3','.wav','.flac','.zip','.tar','.gz','.bz2','.7z','.exe','.dll','.so','.pyc']
    if ext in binary:
        return {"level": "skip", "message": f"{file_path}: 二进制文件，跳过"}

    if size == 0:
        return {"level": "error", "message": f"{file_path}: 文件为空"}

    # HTML
    if ext == '.html':
        try:
            result = subprocess.run(["python", "-c", f"import html.parser; html.parser.HTMLParser().feed(open('{file_path}').read())"],
                                   capture_output=True, text=True, timeout=10)
            return {"level": "pass" if result.returncode == 0 else "error",
                    "message": f"{file_path}: {'通过' if result.returncode == 0 else '语法错误 - ' + result.stderr[:200]}"}
        except Exception as e:
            return {"level": "warning", "message": f"{file_path}: 验证异常 - {e}"}

    # JavaScript
    if ext == '.js':
        try:
            result = subprocess.run(["node", "--check", file_path], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return {"level": "pass", "message": f"{file_path}: JS 语法正确"}
            return {"level": "error", "message": f"{file_path}: JS 语法错误 - {result.stderr[:200]}"}
        except FileNotFoundError:
            with open(file_path, 'r') as f:
                content = f.read()
            if content.count('{') == content.count('}') and content.count('[') == content.count(']'):
                return {"level": "warning", "message": f"{file_path}: 括号匹配（Node.js 未安装）"}
            return {"level": "error", "message": f"{file_path}: 括号不匹配"}

    # Python
    if ext == '.py':
        try:
            import py_compile
            py_compile.compile(file_path, doraise=True)
            return {"level": "pass", "message": f"{file_path}: Python 语法正确"}
        except py_compile.PyCompileError as e:
            return {"level": "error", "message": f"{file_path}: Python 语法错误 - {str(e)[:200]}"}

    # JSON
    if ext == '.json':
        try:
            with open(file_path, 'r') as f:
                json.load(f)
            return {"level": "pass", "message": f"{file_path}: JSON 格式正确"}
        except json.JSONDecodeError as e:
            return {"level": "error", "message": f"{file_path}: JSON 格式错误 - {str(e)[:200]}"}

    # Shell
    if ext in ['.sh', '.bash']:
        try:
            result = subprocess.run(["bash", "-n", file_path], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return {"level": "pass", "message": f"{file_path}: Shell 语法正确"}
            return {"level": "warning", "message": f"{file_path}: Shell 警告 - {result.stderr[:200]}"}
        except FileNotFoundError:
            return {"level": "skip", "message": f"{file_path}: bash 未安装"}

    # CSS
    if ext == '.css':
        with open(file_path, 'r') as f:
            content = f.read()
        return {"level": "pass" if content.count('{') == content.count('}') else "warning",
                "message": f"{file_path}: {'括号匹配' if content.count('{') == content.count('}') else '括号不匹配'}"}

    # 文档
    if ext in ['.md', '.rst', '.txt']:
        with open(file_path, 'r') as f:
            content = f.read()
        return {"level": "pass" if len(content.split('\n')) >= 3 else "warning",
                "message": f"{file_path}: {'通过' if len(content.split('\n')) >= 3 else '内容过短'}"}

    # 未知类型
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return {"level": "pass" if content.strip() else "warning",
                "message": f"{file_path}: {'通过' if content.strip() else '文件为空'}"}
    except UnicodeDecodeError:
        return {"level": "skip", "message": f"{file_path}: 可能是二进制文件"}