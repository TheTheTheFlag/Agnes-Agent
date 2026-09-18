"""app.wecom.bot — 企业微信「智能机器人」长连接接入（消息收发 ↔ Agent）。

连接方式：WebSocket 长连接（BotID + Secret）
  - 无需公网回调地址，无需签名校验与 AES 加解密（企微侧要求见开发者中心「智能机器人开发」）
  - 由官方 SDK 负责认证、心跳保活（30s）与断线重连
  - 企微 → 本服务：aibot_msg_callback（消息）/ aibot_event_callback（事件）
  - 本服务 → 企微：aibot_respond_msg（本条回复，stream 类型）/ aibot_send_msg（主动推送）

回复策略：收到消息先回一条「正在处理…」占位，Agent 跑完后用同一个 stream.id 推最终答案。
Agent 调用与 /api/chat 完全一致，因此企业微信里同样带多轮上下文、记忆与工具能力。

配置（.env，全部可选；填了 BotID + Secret 即自动启用）：
    WECHAT_BOT_ID            机器人 BotID（管理后台 → 机器人编辑页 → API 模式 → 长连接）
    WECHAT_BOT_SECRET        长连接专用 Secret
    WECHAT_BOT_ENABLED       显式开关，0/false 强制关闭（默认有凭证即启用）
    WECHAT_BOT_WELCOME       用户进入会话时的欢迎语
    WECHAT_BOT_THREAD_PREFIX thread_id 前缀（默认 wx），用于与 Web 面板会话隔离
    WECHAT_BOT_TIMEOUT       单轮对话超时秒数（默认 540；企微流式消息要求 10 分钟内 finish）
    WECHAT_BOT_CONCURRENCY   同时处理的消息数上限（默认 2）
    WECHAT_BOT_KBS           可选，逗号分隔的知识库，限定企业微信侧检索范围

会话映射：每个企微会话固定映射到一个 thread_id —— 单聊 wx:single:<userid>，群聊 wx:group:<chatid>。
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

try:
    from aibot import WSClient, WSClientOptions, generate_req_id
except Exception:  # 未安装 SDK 时不阻断服务启动
    WSClient = None  # type: ignore
    WSClientOptions = None  # type: ignore
    generate_req_id = None  # type: ignore

# 企微流式消息正文上限（UTF-8 字节，官方文档：20480）
_STREAM_MAX_BYTES = 20480
# 群里消息形如 "@机器人 你好"，去掉开头的 @提及
_MENTION_RE = re.compile(r"^\s*@\S+\s*")
# 不支持的消息类型提示文案
_MEDIA_HINT = {"image": "图片", "file": "文件", "video": "视频"}
# SDK 会单独分发的事件类型（其余走 message 兜底）
_SDK_MSGTYPES = {"text", "mixed", "voice", "image", "file"}

_STATE: Dict[str, Any] = {"client": None, "ready": False, "started": False}
_SEEN_MSGIDS: set = set()
_TURN_SEM: Optional[asyncio.Semaphore] = None


# ==================== 配置与日志 ====================

def _int_env(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


def config() -> dict:
    """读取企业微信接入配置（每次读取 .env 环境变量，便于改配置后重启即生效）。"""
    raw = (os.getenv("WECHAT_BOT_ENABLED") or "").strip().lower()
    return {
        "bot_id": (os.getenv("WECHAT_BOT_ID") or "").strip(),
        "secret": (os.getenv("WECHAT_BOT_SECRET") or "").strip(),
        "disabled": raw in ("0", "false", "no", "off"),
        "welcome": os.getenv("WECHAT_BOT_WELCOME")
        or "你好，我是 Agnes 智能助手。直接发消息即可对话，支持多轮上下文与工具调用。",
        "prefix": (os.getenv("WECHAT_BOT_THREAD_PREFIX") or "wx").strip() or "wx",
        "timeout": max(10, _int_env("WECHAT_BOT_TIMEOUT", 540)),
        "concurrency": max(1, _int_env("WECHAT_BOT_CONCURRENCY", 2)),
        "kbs": [k.strip() for k in (os.getenv("WECHAT_BOT_KBS") or "").split(",") if k.strip()],
    }


def _log(msg: str, level: str = "info"):
    """同时写服务端日志与调试面板事件流（面板可在「日志」里看到企业微信收发情况）。"""
    try:
        from app.server import add_log_entry
        add_log_entry(level, f"[wecom] {msg}")
    except Exception:
        pass
    try:
        print(f"[wecom] {msg}", flush=True)
    except Exception:
        pass


# ==================== 消息解析 ====================

def _as_text(content: Any) -> str:
    """LLM 返回的 content 可能是 str，也可能是 content block 列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def _thread_id(body: dict, prefix: str) -> str:
    chattype = (body.get("chattype") or "single").strip().lower()
    if chattype == "group":
        key = body.get("chatid") or "unknown-group"
    else:
        key = (body.get("from") or {}).get("userid") or body.get("chatid") or "unknown-user"
    return f"{prefix}:{chattype}:{key}"


def _extract_text(body: dict) -> Optional[str]:
    """从回调 body 取用户文本。语音用企微已转写的 content，图文混排取其中的文本项。
    纯图片/文件/视频返回 None（当前阶段不支持）。"""
    msgtype = (body.get("msgtype") or "").strip().lower()
    text = ""
    if msgtype == "text":
        text = (body.get("text") or {}).get("content") or ""
    elif msgtype == "voice":
        text = (body.get("voice") or {}).get("content") or ""
    elif msgtype == "mixed":
        for item in ((body.get("mixed") or {}).get("msg_item") or []):
            if (item.get("msgtype") or "").strip().lower() == "text":
                text += ((item.get("text") or {}).get("content") or "") + "\n"
    else:
        return None

    text = _MENTION_RE.sub("", text).strip()
    if not text:
        return None

    quote = body.get("quote") or {}
    quote_text = ""
    if isinstance(quote, dict) and (quote.get("msgtype") or "").strip().lower() == "text":
        quote_text = ((quote.get("text") or {}).get("content") or "").strip()
    if quote_text:
        text = f"（引用消息：{quote_text}）\n{text}"
    return text


def _hard_cut(s: str, limit: int) -> tuple:
    """按 UTF-8 字节数把 s 切成 (不超过 limit 字节的前缀, 剩余)。"""
    if len(s.encode("utf-8")) <= limit:
        return s, ""
    cut = limit
    while cut > 0:
        try:
            head = s.encode("utf-8")[:cut].decode("utf-8")
            return head, s[len(head):]
        except UnicodeDecodeError:
            cut -= 1
    return "", s


def _split_chunks(text: str, limit: int = _STREAM_MAX_BYTES) -> List[str]:
    """长回复按字节上限分段（优先在换行处断开），首段用 stream 收尾，其余补发 markdown。"""
    if len(text.encode("utf-8")) <= limit:
        return [text]
    chunks: List[str] = []
    buf = ""
    for line in text.split("\n"):
        cand = f"{buf}\n{line}" if buf else line
        if len(cand.encode("utf-8")) <= limit:
            buf = cand
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        # 单行本身超限：反复硬切，保证每一段都不超过 limit
        while len(line.encode("utf-8")) > limit:
            head, line = _hard_cut(line, limit)
            if not head:
                break
            chunks.append(head)
        buf = line
    if buf:
        chunks.append(buf)
    return chunks or [text]


# ==================== Agent 调用 ====================

def _run_turn(thread_id: str, text: str) -> str:
    """同步执行一轮对话（在线程池里跑）。与 /api/chat 的 graph.stream 用法一致。"""
    from app.server import config as _srv_cfg

    graph = getattr(_srv_cfg, "_GRAPH", None)
    if graph is None:
        return "（Agent 尚未就绪，请稍后再试）"

    cfg: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    kbs = config()["kbs"]
    if kbs:
        cfg["configurable"]["selected_kbs"] = kbs

    inputs = {"messages": [("user", text)]}
    outputs: List[str] = []
    interrupted = False
    for ev in graph.stream(inputs, cfg, stream_mode="updates", recursion_limit=40):
        for node, upd in (ev or {}).items():
            if node == "__interrupt__":
                interrupted = True
                break
            if isinstance(upd, dict) and isinstance(upd.get("messages"), list):
                for m in reversed(upd["messages"]):
                    if getattr(m, "type", None) == "ai":
                        content = _as_text(getattr(m, "content", "") or "")
                        if content:
                            outputs.append(content)
                        break
        if interrupted:
            break

    if interrupted:
        return "⚠️ 本轮触发了工具调用审批（安全检查）。企业微信侧暂不支持审批，请在 Web 调试面板打开该会话并确认后继续。"
    return outputs[-1] if outputs else "（本轮没有产生文本回复）"


# ==================== 收发处理 ====================

async def _finish_reply(client, frame: dict, stream_id: str, text: str):
    """用同一个 stream.id 收尾；超长时用 markdown 消息补发后续分段。"""
    chunks = _split_chunks(text or "（无内容）")
    await client.reply_stream(frame, stream_id, chunks[0], finish=True)
    for extra in chunks[1:]:
        try:
            await client.reply(frame, {"msgtype": "markdown", "markdown": {"content": extra}})
        except Exception as e:
            _log(f"分段补发失败：{e}", "warn")


async def _handle_message(client, frame: dict):
    """aibot_msg_callback：用户消息 → 一轮 Agent 对话 → 回复。"""
    body = frame.get("body") or {}

    msgid = body.get("msgid") or ""
    if msgid:
        if msgid in _SEEN_MSGIDS:
            return  # 企微可能重复回调，按 msgid 排重
        _SEEN_MSGIDS.add(msgid)
        if len(_SEEN_MSGIDS) > 5000:
            _SEEN_MSGIDS.clear()

    cfg = config()
    thread_id = _thread_id(body, cfg["prefix"])
    userid = (body.get("from") or {}).get("userid") or ""
    text = _extract_text(body)

    if text is None:
        msgtype = (body.get("msgtype") or "").strip().lower()
        hint = _MEDIA_HINT.get(msgtype, msgtype or "该类型")
        try:
            await client.reply_stream(
                frame, generate_req_id("stream"),
                f"暂不支持{hint}消息，请直接发送文字（语音会自动转写为文本）。", finish=True,
            )
        except Exception as e:
            _log(f"类型提示发送失败：{e}", "warn")
        return

    _log(f"收到消息 chat={body.get('chattype')} thread={thread_id} user={str(userid)[:16]} len={len(text)}")
    stream_id = generate_req_id("stream")
    try:
        await client.reply_stream(frame, stream_id, "🤖 正在处理，请稍候…", finish=False)
    except Exception as e:
        _log(f"占位消息发送失败：{e}", "warn")

    try:
        if _TURN_SEM is not None:
            async with _TURN_SEM:
                answer = await asyncio.wait_for(
                    asyncio.to_thread(_run_turn, thread_id, text), timeout=cfg["timeout"]
                )
        else:
            answer = await asyncio.wait_for(
                asyncio.to_thread(_run_turn, thread_id, text), timeout=cfg["timeout"]
            )
    except asyncio.TimeoutError:
        answer = f"⚠️ 处理超时（超过 {cfg['timeout']} 秒）。任务可能仍在后台继续，稍后可再发消息追问。"
    except Exception as e:
        _log(f"处理失败：{e}", "error")
        answer = f"⚠️ 处理失败：{e}"

    try:
        await _finish_reply(client, frame, stream_id, answer)
        _log(f"已回复 thread={thread_id} len={len(answer)}")
    except Exception as e:
        _log(f"回复发送失败：{e}", "error")


async def _handle_event(client, frame: dict):
    """aibot_event_callback：进入会话 → 欢迎语（需在 5 秒内回复）。"""
    body = frame.get("body") or {}
    event = body.get("event") or {}
    eventtype = event.get("eventtype") if isinstance(event, dict) else None
    if eventtype == "enter_chat":
        try:
            await client.reply_welcome(
                frame, {"msgtype": "text", "text": {"content": config()["welcome"]}}
            )
        except Exception as e:
            _log(f"欢迎语发送失败：{e}", "warn")
    else:
        _log(f"未处理事件：{eventtype}", "debug")


async def _handle_any_message(client, frame: dict):
    """兜底：SDK 未单独分发的高类型（如 video）也给出提示，避免用户消息石沉大海。"""
    body = frame.get("body") or {}
    if (body.get("msgtype") or "").strip().lower() in _SDK_MSGTYPES:
        return  # 已由对应的 message.<type> 处理，避免重复回复
    await _handle_message(client, frame)


def _register(client):
    for name in ("message.text", "message.mixed", "message.voice",
                 "message.image", "message.file"):
        client.on(name, _handle_message)
    client.on("message", _handle_any_message)
    client.on("event.enter_chat", _handle_event)
    client.on("authenticated", lambda: _log("长连接认证成功，已开始接收消息"))
    client.on("disconnected", lambda reason: _log(f"长连接断开：{reason}", "warn"))
    client.on("reconnecting", lambda attempt: _log(f"长连接重连中（第 {attempt} 次）", "warn"))
    client.on("error", lambda err: _log(f"长连接错误：{err}", "error"))


# ==================== 生命周期 ====================

async def start_wecom_bot():
    """由 FastAPI lifespan 以 asyncio 任务方式调用：建连并开始收消息。
    任何失败只记日志，不影响 HTTP 服务本身。"""
    global _TURN_SEM

    cfg = config()
    if _STATE["started"]:
        return
    _STATE["started"] = True

    if cfg["disabled"]:
        _log("企业微信接入已在 .env 关闭（WECHAT_BOT_ENABLED=0），跳过启动")
        return
    if not cfg["bot_id"] or not cfg["secret"]:
        _log("未配置 WECHAT_BOT_ID / WECHAT_BOT_SECRET，跳过启动（企业微信接入未生效）")
        return
    if WSClient is None:
        _log("未安装 wecom-aibot-python-sdk，跳过启动：pip install wecom-aibot-python-sdk", "error")
        return

    _TURN_SEM = asyncio.Semaphore(cfg["concurrency"])
    try:
        client = WSClient(WSClientOptions(
            bot_id=cfg["bot_id"],
            secret=cfg["secret"],
            max_reconnect_attempts=-1,  # 无限重连：常驻服务不能被 10 次上限打断
        ))
        _register(client)
        await client.connect()  # 返回即握手完成，认证结果由 authenticated 事件通知
        _STATE["client"] = client
        _STATE["ready"] = True
        _log(f"长连接已发起（bot_id={cfg['bot_id'][:8]}…，thread 前缀 {cfg['prefix']}）")
    except Exception as e:
        _STATE["ready"] = False
        _log(f"启动失败：{e}", "error")


def stop_wecom_bot():
    """由 lifespan 收尾调用：断开长连接。"""
    client = _STATE.get("client")
    _STATE["client"] = None
    _STATE["ready"] = False
    _STATE["started"] = False
    if client is not None:
        try:
            client.disconnect()
            _log("长连接已断开")
        except Exception as e:
            _log(f"断开失败：{e}", "warn")


def status() -> dict:
    """接入状态（供日志/排查使用）。"""
    cfg = config()
    client = _STATE.get("client")
    return {
        "configured": bool(cfg["bot_id"] and cfg["secret"]),
        "disabled": cfg["disabled"],
        "started": bool(_STATE.get("started")),
        "connected": bool(getattr(client, "is_connected", False)) if client else False,
        "thread_prefix": cfg["prefix"],
    }
