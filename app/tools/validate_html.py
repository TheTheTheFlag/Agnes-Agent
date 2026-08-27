import subprocess
import os
import re
from langchain_core.tools import tool
from app.config import DB_PATH

@tool
def validate_html(file_path: str) -> str:
    """
    验证 HTML 文件的语法和结构完整性。
    检查基本结构（DOCTYPE, html, head, body, script）以及是否存在关键函数。
    如果系统安装了 htmlhint，还会执行额外的语法检查。
    """
    if not os.path.exists(file_path):
        return f"错误：文件不存在: {file_path}"

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return f"无法读取文件: {e}"

    checks = []

    # 检查基本结构
    if "<!DOCTYPE html>" in content or "<!DOCTYPE HTML>" in content:
        checks.append("✓ 包含 DOCTYPE")
    else:
        checks.append("✗ 缺少 DOCTYPE")

    if "<html" in content.lower():
        checks.append("✓ 包含 <html>")
    else:
        checks.append("✗ 缺少 <html>")

    if "<head" in content.lower():
        checks.append("✓ 包含 <head>")
    else:
        checks.append("✗ 缺少 <head>")

    if "<body" in content.lower():
        checks.append("✓ 包含 <body>")
    else:
        checks.append("✗ 缺少 <body>")

    if "<script" in content.lower():
        checks.append("✓ 包含 <script>")
    else:
        checks.append("✗ 缺少 <script>")

    # 检查关键函数（游戏相关）
    key_functions = ["function startGame", "function gameOver", "function updateGame", "function initGame",
                     "function drawGame", "function spawnFood", "function checkCollision"]
    found_functions = []
    for func in key_functions:
        if func in content:
            found_functions.append(func)
    if found_functions:
        checks.append(f"✓ 发现关键函数: {', '.join(found_functions)}")
    else:
        checks.append("✗ 未发现任何游戏关键函数 (建议至少包含 startGame, gameOver, updateGame)")

    # 尝试使用 htmlhint（如果安装）
    htmlhint_result = ""
    try:
        result = subprocess.run(
            ["npx", "htmlhint", file_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            htmlhint_result = "✓ htmlhint 检查通过，无错误"
        else:
            htmlhint_result = f"htmlhint 发现警告/错误:\n{result.stdout.strip()}"
    except Exception:
        htmlhint_result = "htmlhint 未安装或执行失败，跳过 (可安装: npm install -g htmlhint)"

    summary = "HTML 验证结果:\n" + "\n".join(checks) + "\n" + htmlhint_result
    return summary