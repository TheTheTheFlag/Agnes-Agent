#!/usr/bin/env python3
"""投递任务到 agnes-agent /api/chat 并跟随 SSE 直到完成。

用法:
  python3 deliver_task.py --thread <tid> --msgfile <file> --log <logfile>

- 用内部通道头认证（X-Agnes-Internal-Token / X-Agnes-Internal-User: Mirror）
- 遇到审批中断自动 resume(allow=true, mode=always_allow) 继续
- 关键事件（node/tool/final/approval/error/done）逐条落盘 logfile
"""
import argparse
import json
import os
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8081"
TOKEN_PATH = "/home/mirror/Agnes-Agent/data/internal_token"

KEEP_STEPS = ("node", "tool", "final", "approval", "error", "replan", "node_thought", "done")


def headers():
    with open(TOKEN_PATH, encoding="utf-8") as f:
        tok = f.read().strip()
    return {
        "Content-Type": "application/json",
        "X-Agnes-Internal-Token": tok,
        "X-Agnes-Internal-User": "Mirror",
    }


def log(logf, s):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), s)
    print(line, flush=True)
    logf.write(line + "\n")
    logf.flush()


def open_stream(payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/chat", data=data, headers=headers())
    return urllib.request.urlopen(req, timeout=timeout)


def run_stream(logf, payload, timeout):
    done = False
    try:
        with open_stream(payload, timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                evt = line[5:].strip()
                if not evt:
                    continue
                try:
                    e = json.loads(evt)
                except Exception:
                    continue
                step = e.get("step", "")
                if step == "heartbeat":
                    continue
                if step in KEEP_STEPS:
                    log(logf, "%s: %s" % (step, json.dumps(e, ensure_ascii=False)[:700]))
                if step == "approval":
                    log(logf, "APPROVAL_REQUIRED")
                    return "approval", e
                if step == "error":
                    log(logf, "STREAM_ERROR: %s" % (e.get("message") or e))
                    return "error", e
                if step == "done":
                    log(logf, "DONE")
                    return "done", None
        log(logf, "STREAM_ENDED_NO_DONE")
        return "ended", None
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")[:500]
        log(logf, "HTTP_E %s: %s" % (ex.code, body))
        return "error", {"code": ex.code, "body": body}
    except Exception as ex:
        log(logf, "NET_E: %s" % ex)
        return "error", {"err": str(ex)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", required=True)
    ap.add_argument("--msgfile", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.log), exist_ok=True)
    with open(a.msgfile, encoding="utf-8") as mf:
        message = mf.read().strip()

    with open(a.log, "w", encoding="utf-8") as logf:
        log(logf, "START thread=%s msg=%s" % (a.thread, message[:160].replace("\n", " ")))
        payload = {"message": message, "thread_id": a.thread}
        status, info = "running", None
        while status == "running" or status == "approval":
            if status == "approval":
                payload = {"resume": True, "allow": True, "mode": "always_allow", "thread_id": a.thread}
                log(logf, "RESUME allow=true mode=always_allow")
            status, info = run_stream(logf, payload, a.timeout)
        log(logf, "FINAL status=%s info=%s" % (status, json.dumps(info, ensure_ascii=False)[:300]))


if __name__ == "__main__":
    main()