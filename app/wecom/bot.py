"""app.wecom.bot — 企业微信「智能机器人」长连接接入（消息收发 ↔ Agent）。

连接方式：WebSocket 长连接（BotID + Secret）
  - 无需公网回调地址，无需签名校验与 AES 加解密（企微侧要求见开发者中心「智能机器人开发」）
  - 由官方 SDK 负责认证、心跳保活（30s）与断线重连
  - 企微 → 本服务：aibot_msg_callback（消息）/ aibot_event_callback（事件）
  - 本服务 → 企微：aibot_respond_msg（本条回复，stream 类型）/ aibot_send_msg（主动推送）

多用户：每个账号用自己的企业微信机器人。
  每个用户的 BotID/Secret 存在 accounts.db 的 users.extra（wechat_bot_id / wechat_secret）。
  启动时遍历所有配置了凭证的用户，各建一条长连接；某机器人收到的消息一律以**该用户**身份
  运行 Agent（Agnes / 硅基流动 / Tavily / 画像记忆都按该用户解析）。没有在 DB 配置凭证、
  但 .env 里仍填了 WECHAT_BOT_ID/SECRET 的，作为 Mirror 兜底保留旧行为。

回复策略：收到消息先回一条「正在处理…」占位，Agent 跑完后用同一个 stream.id 推最终答案。
Agent 调用与 /api/chat 完全一致，因此企业微信里同样带多轮上下文、记忆与工具能力。

全局行为配置（.env，可选）：
    WECHAT_BOT_ENABLED       显式开关，0/false 强制关闭（默认有凭证即启用）
    WECHAT_BOT_WELCOME       用户进入会话时的欢迎语
    WECHAT_BOT_THREAD_PREFIX thread_id 前缀（默认 wx），用于与 Web 面板会话隔离
    WECHAT_BOT_TIMEOUT       单轮对话超时秒数（默认 540；企微流式消息要求 10 分钟内 finish）
    WECHAT_BOT_CONCURRENCY   同时处理的消息数上限（默认 2）
    WECHAT_BOT_KBS           可选，逗号分隔的知识库，限定企业微信侧检索范围

会话映射：每个企微会话固定映射到一个 thread_id —— 单聊 wx:single:<userid>，群聊 wx:group:<chatid>。

图片：图片/图文混排回调里的图片会下载并 AES 解密后落到**当前用户**的
`data/users/<user>/uploads/` 目录，再把 `uploads/xxx` 相对路径随用户文字一起交给 Agent
（与 Web 面板上传图片的约定一致），由 Agent 识别图片。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

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
# 不支持的消息类型提示文案（图片已支持：下载解密后交给 Agent 用图片理解技能）
_MEDIA_HINT = {"file": "文件", "video": "视频"}
# SDK 会单独分发的事件类型（其余走 message 兜底）
_SDK_MSGTYPES = {"text", "mixed", "voice", "image", "file"}

# 多用户多实例：clients 按 username 索引
_STATE: Dict[str, Any] = {"clients": {}, "ready": False, "started": False}
_SEEN_MSGIDS: set = set()
_TURN_SEM: Optional[asyncio.Semaphore] = None


# ==================== 配置与日志 ====================

def _int_env(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except Exception:
        return default


def config() -> dict:
    """读取企业微信全局行为配置（非凭证；每次读取 .env 环境变量，便于改配置后重启即生效）。"""
    raw = (os.getenv("WECHAT_BOT_ENABLED") or "").strip().lower()
    return {
        "disabled": raw in ("0", "false", "no", "off"),
        "welcome": os.getenv("WECHAT_BOT_WELCOME")
        or "你好，我是 Agnes 智能助手。直接发消息即可对话，支持多轮上下文与工具调用。",
        "prefix": (os.getenv("WECHAT_BOT_THREAD_PREFIX") or "wx").strip() or "wx",
        "timeout": max(10, _int_env("WECHAT_BOT_TIMEOUT", 540)),
        "concurrency": max(1, _int_env("WECHAT_BOT_CONCURRENCY", 2)),
        "kbs": [k.strip() for k in (os.getenv("WECHAT_BOT_KBS") or "").split(",") if k.strip()],
    }


def _db_wecom_users() -> List[Tuple[str, str, str]]:
    """收集 accounts.db 里配置了企业微信长连接凭证的用户。

    返回 [(username, bot_id, secret)]：每个用户用自己的机器人，消息按该用户身份处理。
    """
    out: List[Tuple[str, str, str]] = []
    try:
        from app.server import accounts
        for row in accounts.list_users_with_secrets():
            extra = row.get("extra") or {}
            bid = str(extra.get("wechat_bot_id") or "").strip()
            sec = str(extra.get("wechat_secret") or "").strip()
            u = row.get("username")
            if u and bid and sec:
                out.append((u, bid, sec))
    except Exception as e:
        _log(f"读取企微账号配置失败：{e}", "warn")
    return out


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


def _extract_images(body: dict) -> List[dict]:
    """取回调里的图片：纯图片消息 或 图文混排中的 image 项。每项 {url, aeskey}。"""
    msgtype = (body.get("msgtype") or "").strip().lower()
    if msgtype == "image":
        img = body.get("image") or {}
        return [img] if img.get("url") else []
    if msgtype == "mixed":
        out = []
        for item in ((body.get("mixed") or {}).get("msg_item") or []):
            if (item.get("msgtype") or "").strip().lower() == "image":
                img = item.get("image") or {}
                if img.get("url"):
                    out.append(img)
        return out
    return []


def _parse_filename(content_disposition: str) -> Optional[str]:
    """从 Content-Disposition 里取文件名（兼容 RFC 5987 filename*=UTF-8''）。"""
    if not content_disposition:
        return None
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", content_disposition, re.IGNORECASE)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename="?([^";\s]+)"?', content_disposition, re.IGNORECASE)
    return unquote(m.group(1)) if m else None


def _download_sync(url: str, attempts: int = 3) -> Tuple[bytes, Optional[str]]:
    """同步下载图片（在线程里跑，避免阻塞事件循环）。

    不用 SDK 内置的 aiohttp 下载：实测它对腾讯云 COS 图片会在 10s 超时（错误信息为空）。
    requests（urllib3）与 curl 行为一致，稳定可靠；这里给 60s 读取超时 + 3 次重试。
    """
    import requests

    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            resp = requests.get(url, timeout=(10, 60))
            resp.raise_for_status()
            return resp.content, _parse_filename(resp.headers.get("Content-Disposition", ""))
        except Exception as e:
            last = e
            _log(f"图片下载第 {i + 1}/{attempts} 次失败：{type(e).__name__}: {e!r}", "warn")
            if i < attempts - 1:
                time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def _guess_ext(fname: Optional[str], data: bytes) -> str:
    """图片扩展名：优先用回调文件名，否则按魔数判断。"""
    if fname:
        ext = os.path.splitext(fname)[1].lower()
        if re.fullmatch(r"\.[a-z0-9]{1,5}", ext or ""):
            return ext
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".jpg"


async def _save_images(client, images: List[dict], msgid: str) -> List[Tuple[str, str, int]]:
    """下载并按企微 aeskey 解密图片，落到**当前用户**的 uploads/ 目录（多用户：默认 Mirror）。
    返回 [(展示名, 相对路径 uploads/xxx, 字节数)]，失败的图片跳过并记日志。
    相对路径与 Web 上传约定一致，agent 侧按当前用户 uploads 目录解析。"""
    try:
        from app.server.api.upload import UPLOAD_DIR
    except Exception:
        from app.config import UPLOADS_DIR
        UPLOAD_DIR = UPLOADS_DIR
    os.makedirs(str(UPLOAD_DIR), exist_ok=True)

    refs: List[Tuple[str, str, int]] = []
    stem = (re.sub(r"[^A-Za-z0-9]", "", msgid or "")[:16]) or uuid.uuid4().hex[:12]
    for i, img in enumerate(images):
        url = (img or {}).get("url")
        aeskey = (img or {}).get("aeskey")
        if not url:
            continue
        try:
            raw, fname = await asyncio.to_thread(_download_sync, url)
        except Exception as e:
            _log(f"图片下载失败：{type(e).__name__}: {e!r}", "warn")
            # 兜底：SDK 自带下载（aiohttp，可能超时）——仅在前者失败时尝试
            try:
                data, fname = await client.download_file(url, aeskey)
            except Exception as e2:
                _log(f"图片下载失败（SDK 兜底）：{type(e2).__name__}: {e2!r}", "warn")
                continue
        else:
            try:
                if aeskey:
                    from aibot.crypto_utils import decrypt_file
                    data = decrypt_file(raw, aeskey)
                else:
                    data = raw
            except Exception as e:
                _log(f"图片解密失败：{type(e).__name__}: {e!r}", "warn")
                continue
        if not data:
            _log("图片下载结果为空，跳过", "warn")
            continue
        name = f"wecom_{stem}_{i}{_guess_ext(fname, data)}"
        try:
            with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
                f.write(data)
        except Exception as e:
            _log(f"图片落盘失败：{e}", "warn")
            continue
        refs.append((name, f"uploads/{name}", len(data)))
    return refs


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

def _run_turn(username: str, thread_id: str, text: str) -> str:
    """同步执行一轮对话（在线程池里跑）。与 /api/chat 的 graph.stream 用法一致。

    显式以该消息所属用户身份运行：Agnes / 硅基流动 / Tavily / 企微均按该用户解析。
    """
    from app.userctx import set_current_user, reset_current_user
    from app.server import config as _srv_cfg

    graph = getattr(_srv_cfg, "_GRAPH", None)
    if graph is None:
        return "（Agent 尚未就绪，请稍后再试）"

    cfg: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    kbs = config()["kbs"]
    if kbs:
        cfg["configurable"]["selected_kbs"] = kbs

    tok = set_current_user(username or "Mirror")
    try:
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
    finally:
        reset_current_user(tok)


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


def _client_for(username: str):
    """取某用户的机器人客户端（该机器人收到的消息用此客户端回复）。"""
    return _STATE.get("clients", {}).get(username)


async def _handle_message(frame: dict, username: str):
    """aibot_msg_callback：用户消息 → 一轮 Agent 对话 → 回复（以 username 身份）。

    注意：SDK 的事件监听器只收到 frame（不含 client），client 按 username 从 _STATE 取。
    """
    client = _client_for(username)
    if client is None:
        _log("长连接客户端未就绪，忽略本条消息", "warn")
        return
    from app.userctx import set_current_user, reset_current_user
    _utok = set_current_user(username or "Mirror")
    try:
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
        images = _extract_images(body)

        # 图片：下载解密后落到当前用户 uploads/，把路径连同用户文字交给 Agent
        if images:
            refs = await _save_images(client, images, msgid)
            if refs:
                img_lines = "\n".join(f"![{name}]({path})" for name, path, _ in refs)
                instr = (
                    "（用户发来图片，已保存到当前用户工作区的 uploads 目录下，请识别图片内容后再回答）"
                )
                text = img_lines + "\n" + instr + (("\n\n" + text) if text else "")
                _log(f"收到图片 {len(refs)} 张，已保存：" + ", ".join(p for _, p, _ in refs))
            else:
                # 图片没下下来：直接回失败，且不再跑 Agent ——
                # 否则 Agent 会去翻 uploads/ 里的历史图片，拿旧图乱答（曾实际发生）。
                _log("图片接收失败，未保存任何图片，直接回失败提示", "warn")
                try:
                    await client.reply_stream(
                        frame, generate_req_id("stream"),
                        "图片接收失败（下载或解密出错），请重发一次或改用文字描述。", finish=True,
                    )
                except Exception as e:
                    _log(f"图片失败提示发送失败：{e}", "warn")
                return

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

        _log(f"[{username}] 收到消息 chat={body.get('chattype')} thread={thread_id} user={str(userid)[:16]} len={len(text)}")
        stream_id = generate_req_id("stream")
        try:
            await client.reply_stream(frame, stream_id, "🤖 正在处理，请稍候…", finish=False)
        except Exception as e:
            _log(f"占位消息发送失败：{e}", "warn")

        try:
            if _TURN_SEM is not None:
                async with _TURN_SEM:
                    answer = await asyncio.wait_for(
                        asyncio.to_thread(_run_turn, username, thread_id, text), timeout=cfg["timeout"]
                    )
            else:
                answer = await asyncio.wait_for(
                    asyncio.to_thread(_run_turn, username, thread_id, text), timeout=cfg["timeout"]
                )
        except asyncio.TimeoutError:
            answer = f"⚠️ 处理超时（超过 {cfg['timeout']} 秒）。任务可能仍在后台继续，稍后可再发消息追问。"
        except Exception as e:
            _log(f"处理失败：{e}", "error")
            answer = f"⚠️ 处理失败：{e}"

        try:
            await _finish_reply(client, frame, stream_id, answer)
            _log(f"[{username}] 已回复 thread={thread_id} len={len(answer)}")
        except Exception as e:
            _log(f"回复发送失败：{e}", "error")
    finally:
        reset_current_user(_utok)


async def _handle_event(frame: dict, username: str):
    """aibot_event_callback：进入会话 → 欢迎语（需在 5 秒内回复）。"""
    client = _client_for(username)
    if client is None:
        return
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


async def _handle_disconnected_event(frame: dict, username: str):
    """被新连接踢出：企微同一 BotID 只允许一条长连接。
    官方 SDK 收到此事件不会自动重连，这里明确报错，避免消息静默丢失。"""
    _log(
        f"[{username}] 长连接被新连接接管并断开（同一 BotID 只允许一条长连接）："
        "请确认没有第二个实例或其他机器人在使用同一个 BotID",
        "error",
    )


async def _handle_any_message(frame: dict, username: str):
    """兜底：SDK 未单独分发的高类型（如 video）也给出提示，避免用户消息石沉大海。"""
    body = frame.get("body") or {}
    if (body.get("msgtype") or "").strip().lower() in _SDK_MSGTYPES:
        return  # 已由对应的 message.<type> 处理，避免重复回复
    await _handle_message(frame, username)


def _register(client, username: str):
    for name in ("message.text", "message.mixed", "message.voice",
                 "message.image", "message.file"):
        client.on(name, lambda frame, _n=username: _handle_message(frame, _n))
    client.on("message", lambda frame, _n=username: _handle_any_message(frame, _n))
    client.on("event.enter_chat", lambda frame, _n=username: _handle_event(frame, _n))
    client.on("event.disconnected_event",
              lambda frame, _n=username: _handle_disconnected_event(frame, _n))
    client.on("authenticated", lambda: _log(f"[{username}] 长连接认证成功，已开始接收消息"))
    client.on("disconnected", lambda reason: _log(f"[{username}] 长连接断开：{reason}", "warn"))
    client.on("reconnecting", lambda attempt: _log(f"[{username}] 长连接重连中（第 {attempt} 次）", "warn"))
    client.on("error", lambda err: _log(f"[{username}] 长连接错误：{err}", "error"))


# ==================== 生命周期 ====================

async def _run_client(username: str, client):
    """单用户的企微长连接：注册回调节点后阻塞在 connect()（SDK 内部重连循环）。"""
    try:
        _register(client, username)
        await client.connect()  # 阻塞：握手成功后由 authenticated 事件通知
        _STATE.setdefault("clients", {})[username] = client
        _STATE["ready"] = True
        _log(f"[{username}] 长连接已发起（bot_id 已配置，thread 前缀 {config()['prefix']}）")
    except Exception as e:
        _STATE.get("clients", {}).pop(username, None)
        _log(f"[{username}] 企业微信长连接启动失败：{e}", "error")


def _wecom_credentials() -> List[Tuple[str, str, str]]:
    """企微凭证清单：优先每个用户自己的（accounts.db extra）；为空时回退 .env → Mirror。"""
    creds = _db_wecom_users()
    if creds:
        return creds
    bid = (os.getenv("WECHAT_BOT_ID") or "").strip()
    sec = (os.getenv("WECHAT_BOT_SECRET") or "").strip()
    if bid and sec:
        from app.userctx import DEFAULT_USER
        return [(DEFAULT_USER, bid, sec)]
    return []


async def start_wecom_bot():
    """由 FastAPI lifespan 以 asyncio 任务方式调用：为每个配置了凭证的用户建连。
    任何失败只记日志，不影响 HTTP 服务本身。"""
    global _TURN_SEM

    cfg = config()
    if _STATE["started"]:
        return
    _STATE["started"] = True

    if cfg["disabled"]:
        _log("企业微信接入已在 .env 关闭（WECHAT_BOT_ENABLED=0），跳过启动")
        return
    if WSClient is None:
        _log("未安装 wecom-aibot-python-sdk，跳过启动：pip install wecom-aibot-python-sdk", "error")
        return

    creds = _wecom_credentials()
    if not creds:
        _log("没有用户配置企业微信 BotID/Secret（accounts.db 或 .env），跳过启动")
        return

    _TURN_SEM = asyncio.Semaphore(cfg["concurrency"])
    started = 0
    for username, bid, sec in creds:
        try:
            client = WSClient(WSClientOptions(
                bot_id=bid,
                secret=sec,
                max_reconnect_attempts=-1,  # 无限重连：常驻服务不能被 10 次上限打断
            ))
        except Exception as e:
            _log(f"[{username}] 创建客户端失败：{e}", "error")
            continue
        asyncio.get_running_loop().create_task(_run_client(username, client))
        started += 1
    if started:
        _log(f"企业微信已启动 {started} 个机器人（各自用自己的 BotID/Secret）")
    else:
        _STATE["started"] = False
        _log("企业微信没有可启动的机器人（配置无效？）", "error")


def stop_wecom_bot():
    """由 lifespan 收尾调用：断开所有用户的长连接。"""
    clients = _STATE.get("clients") or {}
    _STATE["clients"] = {}
    _STATE["ready"] = False
    _STATE["started"] = False
    for username, client in clients.items():
        try:
            client.disconnect()
            _log(f"[{username}] 长连接已断开")
        except Exception as e:
            _log(f"[{username}] 断开失败：{e}", "warn")


def status() -> dict:
    """接入状态（供日志/排查使用）。"""
    cfg = config()
    clients = _STATE.get("clients") or {}
    online = [u for u, c in clients.items() if getattr(c, "is_connected", False)]
    return {
        "users": sorted(clients.keys()),
        "online": sorted(online),
        "configured": _wecom_credentials(),
        "disabled": cfg["disabled"],
        "started": bool(_STATE.get("started")),
        "connected": len(online),
        "thread_prefix": cfg["prefix"],
    }
