"""
file_ops.py — Python 包装的文件/目录能力工具集。

替代直接执行 shell 命令（system_command 在跨平台适配差）。
所有工具默认限制在项目根目录（BASE_DIR）内，防止越权读写。
"""
import os
import glob as _glob
import json
from typing import List, Optional
from langchain_core.tools import tool

from app.config import BASE_DIR


def _resolve_path(path: str) -> str:
    """把相对/绝对路径解析到 BASE_DIR 内；越界则抛错。"""
    p = os.path.abspath(os.path.join(BASE_DIR, path))
    if not p.startswith(BASE_DIR):
        raise ValueError(f"路径越界（仅允许项目目录内）: {path}")
    return p


@tool
def ls(directory: str = ".", recursive: bool = False) -> str:
    """列出目录中的文件与子目录（默认项目根目录）。
    参数:
      directory: 相对项目根的目录，如 '.' 或 'app/graph'（越界会被拒绝）
      recursive: 是否递归列出
    返回: JSON 数组 [{name, path, size, type}]"""
    d = _resolve_path(directory)
    if not os.path.isdir(d):
        return json.dumps({"error": f"目录不存在: {directory}"}, ensure_ascii=False)
    out = []
    for root, dirs, files in os.walk(d):
        rel = os.path.relpath(root, BASE_DIR)
        rel = '' if rel == '.' else rel
        for name in sorted(dirs + files):
            full = os.path.join(root, name)
            relp = os.path.join(rel, name).replace('\\', '/')
            if os.path.isfile(full):
                out.append({"name": name, "path": relp, "size": os.path.getsize(full), "type": "file"})
            else:
                out.append({"name": name, "path": relp, "size": 0, "type": "dir"})
        if not recursive:
            break
    return json.dumps(out[:500], ensure_ascii=False)


@tool
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """读取文本文件内容。
    参数:
      path: 相对项目根的路径
      offset: 起始行号（0 起）
      limit: 最多返回行数
    返回: 文件内容（含行号），支持分页"""
    p = _resolve_path(path)
    if not os.path.isfile(p):
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        total = len(lines)
        chunk = lines[offset:offset + limit]
        body = ''.join(f"{offset + i + 1:6d} | {ln.rstrip()}" for i, ln in enumerate(chunk))
        return json.dumps({"path": path, "total_lines": total, "content": body}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def write_file(path: str, content: str) -> str:
    """创建新文件或覆盖已有文件。
    参数:
      path: 相对项目根的路径（父目录不存在会自动创建）
      content: 文件内容（UTF-8）
    返回: 写入结果（文件路径 + 大小）"""
    p = _resolve_path(path)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return json.dumps({"ok": True, "path": path, "size": os.path.getsize(p)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """在文件中执行精确的字符串替换（第一次出现处）。
    参数:
      path: 相对项目根的路径
      old_string: 要替换的原文（必须精确匹配，唯一）
      new_string: 替换后的内容
    返回: 替换结果"""
    p = _resolve_path(path)
    if not os.path.isfile(p):
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    try:
        with open(p, 'r', encoding='utf-8') as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return json.dumps({"error": "未找到要替换的内容（精确匹配）"}, ensure_ascii=False)
        if count > 1:
            return json.dumps({"error": f"old_string 出现 {count} 次，请提供更多上下文使匹配唯一"}, ensure_ascii=False)
        content = content.replace(old_string, new_string)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return json.dumps({"ok": True, "path": path, "replaced": 1, "size": os.path.getsize(p)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def delete_file(path: str) -> str:
    """递归删除文件或目录。
    参数:
      path: 相对项目根的路径（文件或目录）
    返回: 删除结果"""
    p = _resolve_path(path)
    if not os.path.exists(p):
        return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)
    try:
        if os.path.isdir(p):
            import shutil
            shutil.rmtree(p)
        else:
            os.remove(p)
        return json.dumps({"ok": True, "deleted": path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool
def glob_files(pattern: str) -> str:
    """按 glob 模式查找文件（如 '**/*.py'、'data/*.db'）。
    参数:
      pattern: glob 模式，相对项目根
    返回: 匹配的文件路径列表"""
    full = os.path.join(BASE_DIR, pattern)
    matches = [os.path.relpath(p, BASE_DIR).replace('\\', '/') for p in _glob.glob(full, recursive=True) if os.path.isfile(p)]
    return json.dumps({"matches": matches[:200], "count": len(matches)}, ensure_ascii=False)


@tool
def grep_files(pattern: str, path: str = ".", max_results: int = 50) -> str:
    """在文件中搜索内容（正则）。
    参数:
      pattern: 正则表达式
      path: 相对项目根的目录
      max_results: 最大结果数
    返回: [{file, line_no, text}]"""
    import re
    d = _resolve_path(path)
    if not os.path.isdir(d):
        return json.dumps({"error": f"目录不存在: {path}"}, ensure_ascii=False)
    results = []
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return json.dumps({"error": f"正则无效: {e}"}, ensure_ascii=False)
    for root, _, files in os.walk(d):
        for fn in files:
            if fn.startswith('.'):
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            rel = os.path.relpath(fp, BASE_DIR).replace('\\', '/')
                            results.append({"file": rel, "line_no": i, "text": line.strip()[:120]})
                            if len(results) >= max_results:
                                return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
            except Exception:
                continue
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)


__all__ = ["ls", "read_file", "write_file", "edit_file", "delete_file", "glob_files", "grep_files"]
