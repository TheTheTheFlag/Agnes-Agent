import subprocess, os, re, json
from langchain_core.tools import tool
from app.config import DB_PATH

@tool
def system_command(command: str) -> str:
    """执行系统命令，返回结构化 JSON"""
    result = {"command": command, "returncode": 0, "stdout": "", "stderr": "", "files": []}
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace')
        result["returncode"] = proc.returncode
        result["stdout"] = proc.stdout.strip()
        result["stderr"] = proc.stderr.strip()

        if "cat >" in command:
            match = re.search(r'cat >\s+([\w\-\./]+)', command)
            if match and os.path.exists(match.group(1)):
                f = match.group(1)
                result["files"].append({"name": f, "size": os.path.getsize(f)})

        if "ls -la" in command and proc.returncode == 0:
            for line in proc.stdout.split('\n'):
                parts = line.split()
                if len(parts) >= 9 and not line.startswith('total'):
                    result["files"].append({"name": parts[-1], "size": int(parts[4])})

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})