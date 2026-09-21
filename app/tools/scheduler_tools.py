"""
scheduler_tools.py — 定时任务管理工具（所有用户可用）。

操作**当前用户自己**的定时任务：表存放在各用户自己的 memory.db，
工具层通过 current_user() 定位，服务端同源校验 → 天然隔离，
看不到也改不了别的用户的任务。
"""
import json

from langchain_core.tools import tool


def _fmt(d, trunc: int = 2000) -> str:
    try:
        s = json.dumps(d, ensure_ascii=False)
    except Exception:
        s = str(d)
    return s[:trunc]


@tool
def list_scheduled_tasks() -> str:
    """列出当前用户的所有定时任务（含 cron/间隔/每日，附上次执行结果）。

    返回 JSON 数组，字段: id, name, schedule_type, cron_expr, interval_seconds,
    daily_time, prompt, thread_id, enabled, last_run_at, last_result。
    只能看到自己的任务。
    """
    try:
        from app.scheduler import list_tasks
        return _fmt({"tasks": list_tasks()})
    except Exception as e:
        return _fmt({"ok": False, "error": str(e)})


@tool
def create_scheduled_task(
    name: str,
    prompt: str,
    schedule_type: str = "cron",
    cron_expr: str = "",
    interval_seconds: int = 0,
    daily_time: str = "",
    thread_id: str = "",
) -> str:
    """为当前用户新建一个定时任务（只会创建自己名下的任务）。

    参数:
      name: 任务名（必填）
      prompt: 到点要执行的指令/任务内容（必填）
      schedule_type: 'cron'(默认，标准 5 段表达式) / 'interval'(每 N 秒，旧格式) / 'daily'(每日 HH:MM)
      cron_expr: schedule_type=cron 时的 5 段表达式，如 "0 9 * * *"（分 时 日 月 周）
      interval_seconds: schedule_type=interval 时的时间间隔（秒）
      daily_time: schedule_type=daily 时的 "HH:MM"
      thread_id: 可选会话线程 id，留空则每次执行用新线程

    返回新建任务的 JSON（含 id）。
    """
    try:
        from app.scheduler import clean_create, get_task, insert_task
        task, err = clean_create({
            "name": name,
            "prompt": prompt,
            "schedule_type": schedule_type,
            "cron_expr": cron_expr,
            "interval_seconds": interval_seconds,
            "daily_time": daily_time,
            "thread_id": thread_id or None,
        })
        if err:
            return _fmt({"ok": False, "error": err})
        insert_task(task)
        return _fmt({"ok": True, "task": get_task(task["id"])})
    except Exception as e:
        return _fmt({"ok": False, "error": str(e)})


@tool
def update_scheduled_task(
    task_id: str,
    name: str = "",
    prompt: str = "",
    schedule_type: str = "",
    cron_expr: str = "",
    interval_seconds: int = 0,
    daily_time: str = "",
    thread_id: str = "",
    enabled: bool = None,
) -> str:
    """更新当前用户的一个定时任务（只改传入的非空字段，只会更新自己名下的任务）。

    参数:
      task_id: 目标任务 id（用 list_scheduled_tasks 查询）
      name / prompt: 新名字 / 新指令
      schedule_type: 'cron' / 'interval' / 'daily'
      cron_expr: schedule_type=cron 时必填，5 段表达式
      interval_seconds: schedule_type=interval 时的秒数
      daily_time: schedule_type=daily 时的 "HH:MM"
      thread_id: 会话线程 id
      enabled: true 启用 / false 停用

    返回更新后的任务 JSON。
    """
    try:
        from app.scheduler import clean_update, get_task, update_task
        existing = get_task(task_id)
        if not existing:
            return _fmt({"ok": False, "error": "任务不存在"})
        payload = {}
        for k, v in (("name", name), ("prompt", prompt), ("schedule_type", schedule_type),
                     ("cron_expr", cron_expr), ("daily_time", daily_time),
                     ("thread_id", thread_id)):
            if str(v).strip():
                payload[k] = v
        if interval_seconds:
            payload["interval_seconds"] = int(interval_seconds)
        if enabled is not None:
            payload["enabled"] = bool(enabled)
        fields, err = clean_update(existing, payload)
        if err:
            return _fmt({"ok": False, "error": err})
        update_task(task_id, **fields)
        return _fmt({"ok": True, "task": get_task(task_id)})
    except Exception as e:
        return _fmt({"ok": False, "error": str(e)})


@tool
def delete_scheduled_task(task_id: str) -> str:
    """删除当前用户的一个定时任务（只能删除自己名下的任务）。"""
    try:
        from app.scheduler import delete_task, get_task
        if not get_task(task_id):
            return _fmt({"ok": False, "error": "任务不存在"})
        delete_task(task_id)
        return _fmt({"ok": True, "deleted": task_id})
    except Exception as e:
        return _fmt({"ok": False, "error": str(e)})


@tool
def run_scheduled_task_now(task_id: str) -> str:
    """立即执行当前用户的一个定时任务（同步等待执行完成，可能耗时较长）。

    注意: 只会执行自己名下的任务；停用的任务需先用 update_scheduled_task(enabled=true) 启用。
    """
    try:
        from app.scheduler import run_task_now
        return _fmt(run_task_now(task_id))
    except Exception as e:
        return _fmt({"ok": False, "error": str(e)})