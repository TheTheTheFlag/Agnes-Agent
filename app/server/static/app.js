/* ============================================================
   Agnes Agent — 对话面板前端逻辑
   全部能力：流式对话 / 工具卡片 / 审批 / 会话管理 / 模型管理 /
   调试抽屉（State·提示词·日志·事件·追踪·记忆·MemoryDB·工具·
   定时任务·模型·交付物·Git）
   ============================================================ */
"use strict";

/* ==================== 全局状态 ==================== */
const State = {
  threadId: null,
  model: { provider: "", model: "" },
  streaming: false,
  chatAbort: null,
  currentAssistantEl: null,   // 当前流式输出的助手气泡
  streamBuffer: "",           // token 累积缓冲
  renderTimer: null,
  pendingToolCard: null,      // tool_chunk 阶段的目标卡片
  approvalCard: null,         // 当前审批卡片
  drawerTab: null,
  sse: null,
};

/* ==================== 工具函数 ==================== */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function decodeEntities(s) {
  const el = document.createElement("textarea");
  el.innerHTML = s;
  return el.value;
}

function truncate(s, max) {
  s = String(s == null ? "" : s);
  return s.length > max ? s.slice(0, max) + "…" : s;
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return String(ts).slice(5, 19);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtClock(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtAgo(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return "";
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const day = Math.floor(h / 24);
  if (day < 30) return `${day} 天前`;
  return d.toLocaleDateString();
}

let toastTimer = null;
function toast(msg, type) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (type ? " " + type : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制到剪贴板", "success");
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制到剪贴板", "success");
  }
}

function copyCode(btn) {
  const code = btn.parentElement.querySelector("code");
  copyText(code ? code.textContent : "");
}

/* ==================== API 封装 ==================== */
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

/* ==================== Markdown 渲染（防 XSS + 代码高亮） ==================== */
const KW = new Set([
  "def", "return", "import", "from", "class", "if", "elif", "else", "for", "while", "in", "not", "and", "or", "is",
  "None", "True", "False", "try", "except", "finally", "raise", "with", "as", "lambda", "pass", "break", "continue",
  "async", "await", "global", "nonlocal", "yield", "del", "assert",
  "const", "let", "var", "function", "new", "typeof", "instanceof", "null", "undefined", "this", "export", "default",
  "extends", "super", "switch", "case", "do", "package", "private", "public", "static", "void", "delete", "enum",
  "implements", "interface", "module", "require", "console",
  "int", "float", "str", "bool", "char", "double", "long", "short", "unsigned", "signed", "struct", "union",
  "include", "define", "printf", "scanf", "malloc", "free", "sizeof",
  "select", "from", "where", "insert", "into", "values", "update", "set", "delete", "create", "table", "join",
  "left", "right", "inner", "outer", "on", "group", "order", "by", "having", "limit", "like", "between",
  "distinct", "count", "sum", "avg", "min", "max", "desc", "asc", "primary", "key", "references", "foreign",
  "begin", "end", "then", "elsif", "unless", "each", "do", "local", "endfunction", "endif", "endfor",
]);

function highlightCode(code) {
  if (!code) return "";
  const src = escapeHtml(code);
  const stash = [];
  let s = src;
  s = s.replace(/\/\/[^\n]*|#[^\n]*|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g, (m) => {
    stash.push(m);
    return "\u0000" + (stash.length - 1) + "\u0000";
  });
  s = s.replace(/\b([A-Za-z_][A-Za-z0-9_]*)\b/g, (m) => KW.has(m) ? `<span class="tok-kw">${m}</span>` : m);
  s = s.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
  s = s.replace(/\b([A-Za-z_][A-Za-z0-9_]*)(?=\()/g, (m) => `<span class="tok-fn">${m}</span>`);
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const t = stash[+i];
    if (t.startsWith("//") || t.startsWith("#")) return `<span class="tok-com">${t}</span>`;
    return `<span class="tok-str">${t}</span>`;
  });
  return s;
}

function renderMarkdown(text) {
  if (!text) return "";
  // 先整体转义（防 XSS），再交给 marked 渲染 markdown 语法
  let html = marked.parse(escapeHtml(text));
  // 代码块后处理：语言标签 + 复制按钮 + 语法高亮
  html = html.replace(/<pre><code class="language-([^"]+)">([\s\S]*?)<\/code><\/pre>/g, (_, lang, body) => {
    const src = decodeEntities(body);
    return `<pre><span class="code-lang">${escapeHtml(lang)}</span><button class="code-copy" onclick="copyCode(this)">复制</button><code>${highlightCode(src)}</code></pre>`;
  });
  html = html.replace(/<pre><code>([\s\S]*?)<\/code><\/pre>/g, (_, body) => {
    const src = decodeEntities(body);
    return `<pre><button class="code-copy" onclick="copyCode(this)">复制</button><code>${highlightCode(src)}</code></pre>`;
  });
  return html;
}

/* ==================== 消息区容器 ==================== */
const messagesEl = $("#messages");
function messagesInner() {
  let inner = $(".messages-inner");
  if (!inner) {
    inner = document.createElement("div");
    inner.className = "messages-inner";
    messagesEl.appendChild(inner);
  }
  return inner;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderWelcome() {
  const inner = messagesInner();
  inner.innerHTML = `
    <div class="welcome">
      <div class="w-logo">
        <svg viewBox="0 0 32 32" width="30" height="30"><path d="M10 21l6-10 6 10" stroke="white" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </div>
      <h2>你好，我是 Agnes</h2>
      <p>一个基于 LangGraph 的智能体：能规划任务、调用工具、联网搜索，并拥有 5 层记忆。试试下面的问题，或直接输入你的想法。</p>
      <div class="welcome-suggest">
        <button data-s="规划一下，帮我写一个贪吃蛇小游戏">写一个贪吃蛇游戏</button>
        <button data-s="搜索一下最近的 AI 新闻">搜索最近 AI 新闻</button>
        <button data-s="查看我的命令历史，总结常用的命令">总结我的常用命令</button>
        <button data-s="帮我写一个 Python 快速排序并解释">写快速排序</button>
      </div>
    </div>`;
  $$(".welcome-suggest button", inner).forEach((b) => {
    b.addEventListener("click", () => {
      $("#chatInput").value = b.dataset.s;
      sendMessage(b.dataset.s);
    });
  });
}

/* ==================== 用户/助手消息渲染 ==================== */
function addUserBubble(text) {
  const inner = messagesInner();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `<div class="msg-body"><div class="msg-text">${escapeHtml(text)}</div></div>`;
  inner.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function addAssistantBubble(metaText) {
  const inner = messagesInner();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant pending";
  const now = new Date().toISOString();
  wrap.innerHTML = `
    <div class="msg-body">
      <div class="msg-head">
        <span class="msg-avatar">A</span>
        <span class="msg-label">Agnes</span>
        <span class="msg-time">${fmtClock(now)}</span>
      </div>
      <div class="msg-text"><span class="typing-dots"><span></span><span></span><span></span></span></div>
      <div class="msg-actions">
        <button class="msg-action" data-act="copy">复制</button>
      </div>
    </div>`;
  inner.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function addErrorBubble(text) {
  const inner = messagesInner();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant error";
  wrap.innerHTML = `<div class="msg-body"><div class="msg-text">⚠️ ${escapeHtml(text)}</div></div>`;
  inner.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

/* ==================== 工具卡片 ==================== */
const TOOL_ICONS = {
  search_my_memory: "🧠", list_my_recent_tasks: "📋", get_command_history: "💻",
  tavily_search: "🌐", execute_command: "⚡", system_command: "🖥️", file_ops: "📄",
  validate_html: "✅", update_user_info: "👤", update_user_preference: "⭐",
  request_planning: "🗺️", default: "⚙️",
};

function toolIcon(name) {
  return TOOL_ICONS[name] || TOOL_ICONS.default;
}

function createToolCard(name) {
  const wrap = document.createElement("div");
  wrap.className = "tool-card running";
  wrap.dataset.tool = name || "";
  wrap.innerHTML = `
    <div class="tool-head">
      <span class="tool-ico">${toolIcon(name)}</span>
      <span class="tool-name">${escapeHtml(name || "tool")}</span>
      <span class="tool-status">
        <span class="tool-badge running">运行中</span>
        <span class="tool-time"></span>
        <span class="tool-arrow">▶</span>
      </span>
    </div>
    <div class="tool-body">
      <div class="tool-sec"><div class="tool-sec-label">参数</div><pre class="tool-args">…</pre></div>
      <div class="tool-sec"><div class="tool-sec-label">结果</div><pre class="tool-result">…</pre></div>
    </div>`;
  $(".tool-head", wrap).addEventListener("click", () => wrap.classList.toggle("open"));
  return wrap;
}

function attachToolCard(name) {
  // 附加到当前助手气泡内
  let host = State.currentAssistantEl;
  if (!host) host = addAssistantBubble("");
  const card = createToolCard(name);
  $(".msg-text", host).appendChild(card);
  State.pendingToolCard = card;
  scrollToBottom();
  return card;
}

function updateToolCard(payload) {
  const card = State.pendingToolCard;
  if (!card) return;
  const step = payload.step;
  if (step === "tool_chunk") {
    const p = $(".tool-args", card);
    if (p) {
      p.textContent = p.textContent === "…" ? "" : p.textContent;
      p.textContent += payload.args || "";
    }
  } else if (step === "tool") {
    card.classList.remove("running");
    card.classList.add(payload.phase === "end" ? "success" : "error");
    const badge = $(".tool-badge", card);
    if (badge) {
      badge.textContent = payload.phase === "end" ? "✓ 完成" : "失败";
      badge.className = "tool-badge " + (payload.phase === "end" ? "ok" : "fail");
    }
    const out = $(".tool-result", card);
    if (out && payload.output_preview != null) out.textContent = payload.output_preview || "(无输出)";
    const args = $(".tool-args", card);
    if (args && args.textContent === "…") args.textContent = "(无参数)";
    card.classList.add("open");
    State.pendingToolCard = null;
    scrollToBottom();
  }
}

/* ==================== 审批卡片 ==================== */
function renderApprovalCard(data) {
  const host = State.currentAssistantEl || addAssistantBubble("");
  const card = document.createElement("div");
  card.className = "approval-card";
  card.innerHTML = `
    <div class="approval-title">🔐 需要你的确认</div>
    <div class="approval-question">${escapeHtml(data.question || "是否允许执行该操作？")}</div>
    ${data.command ? `<div class="approval-cmd">${escapeHtml(data.command)}</div>` : ""}
    <div class="approval-actions">
      <button class="btn-approve">允许执行</button>
      <button class="btn-deny">拒绝</button>
    </div>`;
  $(".btn-approve", card).addEventListener("click", () => respondApproval(true, data.mode));
  $(".btn-deny", card).addEventListener("click", () => respondApproval(false, data.mode));
  $(".msg-text", host).appendChild(card);
  State.approvalCard = card;
  scrollToBottom();
}

function respondApproval(allow, mode) {
  const card = State.approvalCard;
  if (card) {
    const btns = $$("button", card);
    btns.forEach((b) => (b.disabled = true));
  }
  State.approvalCard = null;
  sendMessage("", { resume: true, allow, mode });
}

/* ==================== 流式渲染调度 ==================== */
function scheduleStreamRender() {
  if (State.renderTimer) return;
  State.renderTimer = setTimeout(() => {
    State.renderTimer = null;
    flushStreamRender();
  }, 80);
}

function flushStreamRender() {
  if (!State.currentAssistantEl) return;
  const host = State.currentAssistantEl;
  host.classList.remove("pending");
  const box = $(".msg-text", host);
  if (!box) return;
  // 保留工具卡片/审批卡片（它们也挂在 msg-text 下），只更新文本节点
  const textNode = $("#stream-text", host);
  if (textNode) {
    textNode.innerHTML = renderMarkdown(State.streamBuffer);
  }
  scrollToBottom();
}

function appendStreamToken(text) {
  if (!State.currentAssistantEl) {
    State.currentAssistantEl = addAssistantBubble("");
  }
  const host = State.currentAssistantEl;
  if (!host.classList.contains("streaming")) {
    host.classList.remove("pending");
    host.classList.add("streaming");
    // 移除打字动画，建立流式文本节点
    const box = $(".msg-text", host);
    if (box) {
      box.querySelectorAll(":scope > :not(.tool-card):not(.approval-card)").forEach((n) => n.remove());
      const tn = document.createElement("div");
      tn.id = "stream-text";
      box.prepend(tn);
    }
  }
  State.streamBuffer += text;
  scheduleStreamRender();
}

function endStreaming(finalText) {
  if (State.renderTimer) {
    clearTimeout(State.renderTimer);
    State.renderTimer = null;
  }
  const host = State.currentAssistantEl;
  if (host) {
    host.classList.remove("streaming", "pending");
    if (!State.streamBuffer && finalText) State.streamBuffer = finalText;
    flushStreamRender();
    const tn = $("#stream-text", host);
    if (tn && State.streamBuffer) tn.innerHTML = renderMarkdown(State.streamBuffer);
    // 没有文本内容且没有任何卡片 → 移除空气泡
    const hasCards = $(".tool-card, .approval-card", host);
    if (!State.streamBuffer && !hasCards) host.remove();
  }
  State.currentAssistantEl = null;
  State.streamBuffer = "";
  State.pendingToolCard = null;
  scrollToBottom();
}

/* ==================== 对话流（SSE /api/chat） ==================== */
function handleChatEvent(evt) {
  const step = evt.step;
  if (step === "node") {
    // 节点流转：chatbot start 表示 LLM 开始生成
    if (evt.phase === "start" && evt.name === "chatbot" && !State.currentAssistantEl) {
      State.currentAssistantEl = addAssistantBubble("");
    }
    setLiveBadge(true);
  } else if (step === "token") {
    appendStreamToken(evt.text || "");
  } else if (step === "tool_chunk" || step === "tool") {
    // tool_chunk：工具开始（带参数增量）；tool：工具结束（带结果预览）
    if (evt.name && (!State.pendingToolCard || State.pendingToolCard.dataset.tool !== evt.name)) {
      State.pendingToolCard = attachToolCard(evt.name);
    }
    updateToolCard(evt);
  } else if (step === "approval") {
    renderApprovalCard(evt.data || {});
  } else if (step === "final") {
    if (evt.text && !State.streamBuffer) {
      if (!State.currentAssistantEl) State.currentAssistantEl = addAssistantBubble("");
      State.streamBuffer = evt.text;
      endStreaming();
    }
  } else if (step === "done") {
    endStreaming();
    setLiveBadge(false);
  } else if (step === "error") {
    endStreaming();
    setLiveBadge(false);
    addErrorBubble(evt.message || "未知错误");
  } else if (step === "heartbeat" || step === "__close__") {
    // 忽略
  }
}

async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch (e) { /* 忽略坏帧 */ }
    }
  }
}

async function sendMessage(text, opts) {
  opts = opts || {};
  const isResume = !!opts.resume;

  if (!isResume) {
    text = (text == null ? "" : String(text)).trim();
    if (!text || State.streaming) return;
    addUserBubble(text);
    $("#chatInput").value = "";
    autosizeInput();
    hideCmdMenu();
  } else {
    // resume 模式：复用当前 assistant 气泡继续流式
    if (!State.currentAssistantEl) State.currentAssistantEl = addAssistantBubble("");
    State.currentAssistantEl.classList.add("streaming");
  }

  State.streaming = true;
  setLiveBadge(true);
  updateComposer();
  State.chatAbort = new AbortController();

  const body = isResume
    ? { resume: true, allow: !!opts.allow, mode: opts.mode || "per_ask", thread_id: State.threadId }
    : { message: text, thread_id: State.threadId };

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: State.chatAbort.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    if (!resp.body) throw new Error("响应无流");
    await readSSE(resp, handleChatEvent);
  } catch (e) {
    if (e.name === "AbortError") {
      endStreaming();
    } else {
      endStreaming();
      addErrorBubble(e.message || "请求失败");
    }
  } finally {
    State.streaming = false;
    State.chatAbort = null;
    setLiveBadge(false);
    updateComposer();
    loadThreads(); // 刷新会话列表（标题/时间）
    refreshTopbar();
  }
}

function stopChat() {
  if (State.chatAbort) State.chatAbort.abort();
  endStreaming();
  setLiveBadge(false);
  State.streaming = false;
  updateComposer();
}

/* ==================== 历史消息加载 ==================== */
async function loadHistory(tid) {
  const inner = messagesInner();
  inner.innerHTML = "";
  try {
    const data = await apiGet(`/api/messages?thread_id=${encodeURIComponent(tid)}&limit=500`);
    const msgs = data.messages || [];
    if (!msgs.length) {
      renderWelcome();
      return;
    }
    // 第一条 user 消息做标题
    const firstUser = msgs.find((m) => m.role === "user");
    if (firstUser) updateChatTitle(firstUser.content);
    renderHistory(msgs);
  } catch (e) {
    renderWelcome();
  }
  scrollToBottom();
}

function renderHistory(msgs) {
  const inner = messagesInner();
  let assistantWrap = null;
  let pendingToolCalls = [];
  let pendingToolIds = {};

  for (const m of msgs) {
    if (m.role === "user") {
      const wrap = document.createElement("div");
      wrap.className = "msg user";
      wrap.innerHTML = `<div class="msg-body"><div class="msg-text">${escapeHtml(m.content)}</div></div>`;
      inner.appendChild(wrap);
      assistantWrap = null;
    } else if (m.role === "assistant") {
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
      const content = m.content || "";
      const hasCalls = Array.isArray(m.tool_calls) && m.tool_calls.length;
      wrap.innerHTML = `
        <div class="msg-body">
          <div class="msg-head">
            <span class="msg-avatar">A</span>
            <span class="msg-label">Agnes</span>
            <span class="msg-time">${fmtClock(m.timestamp)}</span>
          </div>
          <div class="msg-text">${content ? renderMarkdown(content) : ""}</div>
          <div class="msg-actions">
            <button class="msg-action" data-act="copy">复制</button>
          </div>
        </div>`;
      inner.appendChild(wrap);
      assistantWrap = wrap;
      // 收集 tool_calls
      if (hasCalls) {
        pendingToolCalls = m.tool_calls.slice();
        pendingToolIds = {};
        m.tool_calls.forEach((tc) => {
          pendingToolIds[tc.id] = { name: tc.name || "tool", args: tc.arguments || tc.args || "", done: false };
        });
        const box = $(".msg-text", wrap);
        pendingToolCalls.forEach((tc) => {
          const card = createToolCard(tc.name || "tool");
          card.classList.add("success");
          const badge = $(".tool-badge", card);
          if (badge) { badge.textContent = "✓ 完成"; badge.className = "tool-badge ok"; }
          const p = $(".tool-args", card);
          if (p) p.textContent = prettyArgs(tc.arguments || tc.args || "");
          box.appendChild(card);
        });
      }
    } else if (m.role === "tool") {
      // 填充对应 tool_call 的结果
      const rec = pendingToolIds[m.tool_call_id];
      if (rec && assistantWrap) {
        const card = $$(".tool-card", assistantWrap).find((c) => c.dataset.tool === rec.name);
        if (card) {
          const out = $(".tool-result", card);
          if (out) out.textContent = String(m.content || "").slice(0, 300);
          if (m.duration_ms != null) {
            const t = $(".tool-time", card);
            if (t) t.textContent = `${m.duration_ms}ms`;
          }
        }
        rec.done = true;
      }
    }
  }
  // 绑定复制按钮
  $$(".msg-action", inner).forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = btn.closest(".msg-body");
      const txt = body.querySelector(".msg-text").innerText || "";
      copyText(txt);
    });
  });
}

function prettyArgs(args) {
  if (!args) return "";
  if (typeof args === "string") {
    try { args = JSON.parse(args); } catch (e) { return args; }
  }
  try { return JSON.stringify(args, null, 2); } catch (e) { return String(args); }
}

/* ==================== 会话管理（边栏） ==================== */
const threadListEl = $("#threadList");

async function loadThreads() {
  try {
    const data = await apiGet("/api/threads");
    const threads = data.threads || [];
    if (data.current_thread_id && !State.threadId) {
      State.threadId = data.current_thread_id;
    }
    renderThreads(threads);
    return data;
  } catch (e) {
    return { threads: [] };
  }
}

function renderThreads(threads) {
  const kw = $("#threadSearch").value.trim().toLowerCase();
  threadListEl.innerHTML = "";
  const visible = threads.filter((t) => !kw || String(t.thread_id).toLowerCase().includes(kw));

  if (!visible.length) {
    threadListEl.innerHTML = `<div class="thread-empty">${threads.length ? "没有匹配的会话" : "还没有会话，点击「新建会话」开始"}</div>`;
    return;
  }

  visible.forEach((t) => {
    const item = document.createElement("div");
    item.className = "thread-item" + (t.thread_id === State.threadId ? " active" : "");
    const last = t.last ? fmtAgo(t.last) : "从未对话";
    item.innerHTML = `
      <div class="t-main">
        <div class="t-title">${escapeHtml(t.thread_id.slice(0, 13))}</div>
        <div class="t-sub">${t.count} 条消息 · ${last}</div>
      </div>
      <button class="t-del" title="删除会话">×</button>`;
    item.addEventListener("click", (e) => {
      if (e.target.classList.contains("t-del")) return;
      selectThread(t.thread_id);
    });
    $(".t-del", item).addEventListener("click", (e) => {
      e.stopPropagation();
      if (confirm(`确定删除会话 ${t.thread_id.slice(0, 13)}… ？此操作不可恢复。`)) {
        deleteThread(t.thread_id);
      }
    });
    threadListEl.appendChild(item);
  });
}

async function selectThread(tid) {
  if (State.streaming) stopChat();
  State.threadId = tid;
  await loadHistory(tid);
  refreshTopbar();
  renderThreads((await loadThreads()).threads);
}

async function newThread() {
  try {
    const data = await apiPost("/api/command", { command: "/new" });
    State.threadId = data.thread_id || State.threadId;
    messagesInner().innerHTML = "";
    renderWelcome();
    updateChatTitle("新对话");
    refreshTopbar();
    loadThreads();
    toast("已开启新对话", "success");
  } catch (e) {
    toast("新建失败: " + e.message, "error");
  }
}

async function deleteThread(tid) {
  try {
    await apiPost("/api/command", { command: `/delete ${tid}` });
    toast("会话已删除", "success");
    if (State.threadId === tid) {
      State.threadId = null;
      messagesInner().innerHTML = "";
      renderWelcome();
      updateChatTitle("新对话");
    }
    loadThreads();
  } catch (e) {
    toast("删除失败: " + e.message, "error");
  }
}

async function runCommand(cmd) {
  try {
    const data = await apiPost("/api/command", { command: cmd });
    if (data.error) {
      toast("命令错误: " + data.error, "error");
      return;
    }
    if (cmd.trim() === "/new" || cmd.startsWith("/new ")) {
      State.threadId = data.thread_id || State.threadId;
      messagesInner().innerHTML = "";
      renderWelcome();
      updateChatTitle("新对话");
      loadThreads();
      refreshTopbar();
      toast("已开启新对话", "success");
    } else if (cmd.startsWith("/resume")) {
      if (data.thread_id) {
        State.threadId = data.thread_id;
        await loadHistory(State.threadId);
        refreshTopbar();
        loadThreads();
        toast("已切换到会话 " + data.thread_id.slice(0, 13), "success");
      } else {
        showCandidateThreads(data.candidates);
      }
    } else if (cmd.startsWith("/delete")) {
      toast(data.result || "已删除", "success");
      loadThreads();
      if (!State.threadId) { messagesInner().innerHTML = ""; renderWelcome(); }
    } else if (cmd.trim() === "/threads") {
      showCandidateThreads(data.threads);
    } else {
      if (data.result) toast(truncate(data.result, 120), "success");
    }
    refreshTopbar();
  } catch (e) {
    toast("命令失败: " + e.message, "error");
  }
}

function showCandidateThreads(list) {
  if (!list || !list.length) { toast("没有可用的会话", "error"); return; }
  const lines = list.slice(0, 8).map((t) => `  ${t.thread_id}  (${t.count || 0} 条)`);
  toast(`可选会话：\n${lines.join("\n")}\n使用 /resume &lt;id&gt; 切换`, "");
}

/* ==================== 顶栏 ==================== */
function updateChatTitle(text) {
  const t = $("#chatTitle");
  const s = String(text || "").replace(/\s+/g, " ").trim();
  t.textContent = s ? truncate(s, 40) : "新对话";
  t.title = s || "";
}

function refreshTopbar() {
  const meta = $("#chatMeta");
  const model = State.model.model || "…";
  const tid = State.threadId ? State.threadId.slice(0, 8) : "—";
  meta.textContent = `模型 ${model} · ${tid}…`;
  const pill = $("#sidebarModel");
  pill.innerHTML = `<span class="mp-dot"></span><span class="mp-name">${escapeHtml(model)}</span>`;
}

function setLiveBadge(on) {
  $("#liveBadge").classList.toggle("hidden", !on);
}

/* ==================== 输入区 ==================== */
const chatInput = $("#chatInput");
const SLASH_CMDS = [
  { cmd: "/new", desc: "开新对话（生成新 thread_id）" },
  { cmd: "/resume", desc: "继续指定 thread（不填则列出候选）" },
  { cmd: "/threads", desc: "列出最近会话" },
  { cmd: "/model", desc: "查看当前模型" },
  { cmd: "/system", desc: "切换审批模式 (per_ask / session_allow / always_allow)" },
  { cmd: "/clear", desc: "清空日志/事件" },
  { cmd: "/save", desc: "导出当前会话消息" },
  { cmd: "/delete", desc: "删除指定会话" },
  { cmd: "/help", desc: "显示帮助" },
];

function autosizeInput() {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + "px";
}

function updateComposer() {
  const sending = State.streaming;
  $("#btnSend").classList.toggle("hidden", sending);
  $("#btnStop").classList.toggle("hidden", !sending);
  chatInput.disabled = false;
}

function showCmdMenu() {
  const val = chatInput.value;
  if (!val.startsWith("/")) { hideCmdMenu(); return; }
  const kw = val.slice(1).toLowerCase();
  const matches = SLASH_CMDS.filter((c) => c.cmd.slice(1).startsWith(kw) || c.cmd.includes(kw));
  const menu = $("#cmdMenu");
  if (!matches.length) { hideCmdMenu(); return; }
  menu.innerHTML = matches
    .map((c, i) => `<div class="cmd-item" data-i="${i}"><span class="c-cmd">${c.cmd}</span><span class="c-desc">${c.desc}</span></div>`)
    .join("");
  menu.classList.remove("hidden");
  menu._matches = matches;
  menu._sel = 0;
  updateCmdSel(menu);
}

function updateCmdSel(menu) {
  $$(".cmd-item", menu).forEach((el, i) => el.classList.toggle("sel", i === menu._sel));
}

function hideCmdMenu() {
  $("#cmdMenu").classList.add("hidden");
}

function applyCmdSel() {
  const menu = $("#cmdMenu");
  if (menu.classList.contains("hidden") || !menu._matches) return false;
  const c = menu._matches[menu._sel];
  if (!c) return false;
  if (c.cmd === "/resume" || c.cmd === "/system" || c.cmd === "/delete") {
    chatInput.value = c.cmd + " ";
  } else {
    chatInput.value = c.cmd;
  }
  chatInput.focus();
  const pos = chatInput.value.length;
  chatInput.setSelectionRange(pos, pos);
  showCmdMenu();
  return true;
}

chatInput.addEventListener("input", () => {
  autosizeInput();
  showCmdMenu();
});

chatInput.addEventListener("keydown", (e) => {
  const menu = $("#cmdMenu");
  if (!menu.classList.contains("hidden") && menu._matches) {
    if (e.key === "ArrowDown") { e.preventDefault(); menu._sel = (menu._sel + 1) % menu._matches.length; updateCmdSel(menu); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); menu._sel = (menu._sel - 1 + menu._matches.length) % menu._matches.length; updateCmdSel(menu); return; }
    if (e.key === "Tab") { e.preventDefault(); applyCmdSel(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const val = chatInput.value.trim();
    if (!val) return;
    if (val.startsWith("/")) { runCommand(val); return; }
    sendMessage(val);
  }
});

$("#cmdMenu").addEventListener("mousedown", (e) => {
  const item = e.target.closest(".cmd-item");
  if (!item) return;
  e.preventDefault();
  const menu = $("#cmdMenu");
  menu._sel = +item.dataset.i;
  applyCmdSel();
});

function sendFromComposer() {
  const val = chatInput.value.trim();
  if (!val || State.streaming) return;
  if (val.startsWith("/")) { runCommand(val); return; }
  sendMessage(val);
}

/* ==================== 主题 ==================== */
function applyTheme(t) {
  if (!t) t = localStorage.getItem("agnes-theme") || "auto";
  document.documentElement.dataset.theme = t;
  const isLight = t === "light" || (t === "auto" && matchMedia("(prefers-color-scheme: light)").matches);
  $(".ic-moon").style.display = isLight ? "none" : "";
  $(".ic-sun").style.display = isLight ? "" : "none";
  localStorage.setItem("agnes-theme", t);
}

function cycleTheme() {
  const cur = document.documentElement.dataset.theme || "auto";
  const next = cur === "auto" ? "dark" : cur === "dark" ? "light" : "auto";
  applyTheme(next);
  toast("主题: " + (next === "auto" ? "跟随系统" : next === "dark" ? "暗色" : "亮色"));
}

/* ==================== 调试抽屉 ==================== */
const drawer = $("#drawer");
const drawerBody = $("#drawerBody");
const drawerTabsEl = $("#drawerTabs");

function openDrawer() {
  $("#drawerMask").classList.add("show");
  drawer.classList.remove("hidden");
  requestAnimationFrame(() => drawer.classList.add("show"));
  if (!State.drawerTab) activateTab("state");
}

function closeDrawer() {
  $("#drawerMask").classList.remove("show");
  drawer.classList.remove("show");
  setTimeout(() => drawer.classList.add("hidden"), 240);
}

function activateTab(id) {
  State.drawerTab = id;
  $$(".drawer-tab", drawerTabsEl).forEach((t) => t.classList.toggle("active", t.dataset.id === id));
  const loader = DRAWER_LOADERS[id];
  if (loader) loader(drawerBody);
}

function drawerSection(title, bodyHtml) {
  return `<div class="d-section"><h4>${escapeHtml(title)}</h4>${bodyHtml}</div>`;
}

function drawerErr(e) {
  return `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
}

/* ---- State ---- */
async function renderStateTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const tid = State.threadId ? `?thread_id=${encodeURIComponent(State.threadId)}` : "";
    const data = await apiGet("/api/state" + tid);
    el.innerHTML = drawerSection("LangGraph State", `<pre class="d-pre">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`);
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 提示词 ---- */
async function renderPromptTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const tid = State.threadId ? `?thread_id=${encodeURIComponent(State.threadId)}` : "";
    const data = await apiGet("/api/prompt" + tid);
    el.innerHTML = drawerSection("当前 System Prompt", `<pre class="d-pre">${escapeHtml(data.prompt || "(空)")}</pre>`);
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 日志 ---- */
async function renderLogsTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/logs?limit=300");
    const logs = data.logs || [];
    if (!logs.length) { el.innerHTML = `<div class="d-empty">暂无日志</div>`; return; }
    el.innerHTML = drawerSection("日志（最近 " + logs.length + " 条）", logs.map((l) => `
      <div class="d-log"><span class="ts">${escapeHtml((l.timestamp || "").slice(11, 19))}</span>
      <span class="lv lv-${escapeHtml(l.level || "info")}">${escapeHtml((l.level || "info").toUpperCase())}</span>
      <span class="msg">${escapeHtml(truncate(l.message || "", 160))}</span></div>`).join(""));
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 事件 ---- */
async function renderEventsTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const tid = State.threadId ? `&thread_id=${encodeURIComponent(State.threadId)}` : "";
    const data = await apiGet(`/api/events?limit=200${tid}`);
    const events = data.events || [];
    if (!events.length) { el.innerHTML = `<div class="d-empty">暂无事件</div>`; return; }
    el.innerHTML = drawerSection("事件（最近 " + events.length + " 条）", events.map((ev) => `
      <div class="d-log"><span class="ts">${escapeHtml((ev.timestamp || "").slice(11, 19))}</span>
      <span class="lv lv-info">${escapeHtml((ev.type || "event").toUpperCase())}</span>
      <span class="msg">${escapeHtml(truncate(JSON.stringify(ev.data || ""), 140))}</span></div>`).join(""));
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 追踪 ---- */
const TRACE_NODE_COLOR = {
  chatbot: "var(--accent-2)", planner: "#f59e0b", executor: "#3b82f6",
  validator: "#ef476f", summarizer: "#a663cc", agent: "var(--text-faint)",
};

async function renderTraceTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const tid = State.threadId ? `&thread_id=${encodeURIComponent(State.threadId)}` : "";
    const data = await apiGet(`/api/trace?limit=300${tid}`);
    const events = data.events || [];
    if (!events.length) { el.innerHTML = `<div class="d-empty">暂无追踪记录</div>`; return; }
    // 备份风格元素：按 node_start 分组时间线 + LLM 消息卡（markdown 输出 + 折叠输入）+ 折叠工具卡 + 错误卡
    const ts = (t) => (t || "").slice(11, 19);
    let html = "";
    let groupOpen = false;
    for (const evt of events) {
      const d = evt.data || {};
      const etype = evt.type || "";
      const nodeColor = TRACE_NODE_COLOR[d.node] || "var(--text-faint)";
      if (etype === "node_start") {
        if (groupOpen) html += "</div>";
        groupOpen = true;
        html += `<div class="trace-group" style="--tnode:${nodeColor}">
          <div class="trace-group-head"><span>🚀</span><b>${escapeHtml(d.node || "")}</b><span class="trace-ts">${escapeHtml(ts(evt.ts))}</span></div>`;
      } else if (etype === "node_end") {
        html += `<div class="trace-group-end"><span>🏁</span><span>${escapeHtml(String(d.result || "完成"))}</span></div>`;
      } else if (etype === "llm_call") {
        // ChatGPT 风格消息卡：模型标签 + 折叠输入 + markdown 输出
        const inPreview = String(d.input || "").length > 300 ? String(d.input).slice(0, 300) + "…" : (d.input || "");
        html += `<div class="trace-llm">
          <div class="t-llm-head" onclick="toggleTraceCard(this)">
            <span>🤖</span><b>${escapeHtml(d.model || "模型")}</b>
            <span class="trace-ts">${d.duration_ms != null ? escapeHtml(d.duration_ms + "ms") : ""}</span>
            <span class="t-llm-toggle">${d.input ? "▸ 输入" : "▾"}</span>
          </div>
          ${d.input ? `<div class="t-llm-input">${escapeHtml(inPreview)}</div>` : ""}
          ${d.output ? `<div class="t-llm-output md-body">${renderMarkdown(String(d.output))}</div>` : ""}
        </div>`;
      } else if (etype === "tool_call") {
        // 折叠工具卡：点击展开参数/结果
        const isErr = /错误|error|fail|禁止|拒绝/i.test(String(d.result || ""));
        html += `<div class="trace-tool ${isErr ? "err" : ""}" onclick="toggleTraceCard(this)">
          <div class="trace-tool-head"><span>${isErr ? "❌" : "🔧"}</span><b>${escapeHtml(d.name || "")}</b><span class="trace-ts">▸</span></div>
          <div class="trace-tool-body">
            <div class="trace-tool-sec">参数: ${escapeHtml(String(d.params || ""))}</div>
            <div class="trace-tool-sec">结果: ${escapeHtml(String(d.result || ""))}</div>
          </div>
        </div>`;
      } else if (etype === "error") {
        html += `<div class="trace-error">❌ ${escapeHtml(String(d.message || ""))}</div>`;
      }
    }
    if (groupOpen) html += "</div>";
    el.innerHTML = drawerSection(`追踪（${events.length} 条）`, html) +
      `<div class="d-row"><button class="d-btn danger" id="traceClear">清空追踪</button></div>`;
    $("#traceClear", el).addEventListener("click", async () => {
      await apiPost("/api/trace/clear", { thread_id: State.threadId });
      renderTraceTab(el);
    });
  } catch (e) { el.innerHTML = drawerErr(e); }
}

// 点击 trace 内部折叠元素（LLM 输入面板 / 工具参数结果）切换显示
function toggleTraceCard(head) {
  const card = head.closest ? head.closest(".trace-llm, .trace-tool") : null;
  if (!card) return;
  const body = card.querySelector(".t-llm-input, .trace-tool-body");
  if (!body) return;
  const visible = body.style.display !== "none";
  body.style.display = visible ? "none" : "block";
  const tg = head.querySelector(".t-llm-toggle");
  if (tg) tg.textContent = visible ? "▸" : "▾";
}

/* ---- 5 层记忆 ---- */
async function renderMemoryTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const tid = State.threadId || "default";
    const data = await apiGet(`/api/memory?thread_id=${encodeURIComponent(tid)}`);
    const block = (title, obj) => drawerSection(title, `<pre class="d-pre">${escapeHtml(JSON.stringify(obj, null, 2))}</pre>`);
    el.innerHTML =
      block("L2 · 用户画像 (profile)", data.L2_profile || {}) +
      block("L2 · 用户偏好 (preferences)", data.L2_preferences || {}) +
      block("L3 · 最近任务 (recent_tasks)", data.L3_recent_tasks || []) +
      block("L4 · 命令历史 (command_history)", data.L4_command_history || []) +
      block("L5 · 知识缓存 (knowledge_cache)", data.L5_knowledge_cache || []) +
      block("Prompt 注入预览", data.prompt_injection_preview || "");
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- Memory DB ---- */
let mdbState = { table: "messages", rows: [], columns: [] };

async function loadMdbTables() {
  try { return (await apiGet("/api/memorydb/tables")).tables || []; }
  catch (e) { return []; }
}

async function renderMemoryDBTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  const tables = await loadMdbTables();
  el.innerHTML = `
    <div class="d-card">
      <div class="d-row">
        <label>数据表</label>
        <select class="d-select" id="mdbTable">
          ${tables.map((t) => `<option value="${t.name}">${escapeHtml(t.label)}</option>`).join("")}
        </select>
      </div>
      <div class="d-row">
        <label>过滤 (WHERE)</label>
        <input class="d-input" id="mdbWhere" placeholder="如 thread_id='xxx'（可选）">
      </div>
      <div class="d-row">
        <button class="d-btn primary" id="mdbQuery">查询</button>
        <button class="d-btn" id="mdbInsertBtn">新增行</button>
        <button class="d-btn danger" id="mdbClearTable">清除全表</button>
      </div>
    </div>
    <div id="mdbTableWrap"></div>`;

  const tableSel = $("#mdbTable", el);
  const whereInp = $("#mdbWhere", el);
  const runQuery = () => {
    const table = tableSel.value;
    const where = whereInp.value.trim();
    loadMdbQuery(el, table, where);
  };
  // 仅消息表默认按当前会话过滤；用户画像/用户偏好/命令历史/知识缓存等业务记忆表
  // 不自动加过滤条件（展示全量，避免误以为数据丢失；semantic_cache 无 thread_id 列，
  // 自动过滤反而报错）
  const AUTO_FILTER_TABLES = new Set(["messages"]);
  const applyDefaultWhere = () => {
    whereInp.value = AUTO_FILTER_TABLES.has(tableSel.value) && State.threadId
      ? `thread_id='${State.threadId}'` : "";
  };
  tableSel.addEventListener("change", () => { applyDefaultWhere(); runQuery(); });
  $("#mdbQuery", el).addEventListener("click", runQuery);
  whereInp.addEventListener("keydown", (e) => { if (e.key === "Enter") runQuery(); });
  $("#mdbInsertBtn", el).addEventListener("click", () => showMdbInsert(el, tableSel.value));
  $("#mdbClearTable", el).addEventListener("click", async () => {
    const table = tableSel.value;
    if (!confirm(`确定清空表「${table}」的全部数据？此操作不可恢复！`)) return;
    try {
      const r = await apiPost("/api/memorydb/clear", { table });
      toast(`已清空 ${table}（${r.affected} 行）`, "success");
      runQuery();
    } catch (e) { toast(e.message, "error"); }
  });

  applyDefaultWhere();
  loadMdbQuery(el, tableSel.value, whereInp.value);
}

async function loadMdbQuery(el, table, where) {
  const wrap = $("#mdbTableWrap", el);
  if (!wrap) return;
  wrap.innerHTML = `<div class="d-empty">查询中…</div>`;
  try {
    const params = new URLSearchParams({ table, limit: "50" });
    if (where) params.set("where", where);
    const data = await apiGet("/api/memorydb/query?" + params.toString());
    const rows = data.rows || [];
    const cols = rows.length ? Object.keys(rows[0]) : [];
    if (!rows.length) { wrap.innerHTML = `<div class="d-empty">无数据（共 ${data.total} 行）</div>`; return; }
    mdbState = { table, rows, columns: cols };
    const head = cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("") + "<th></th>";
    const body = rows.map((r, ri) => {
      const cells = cols.map((c) => {
        const v = r[c];
        const s = v == null ? "" : String(v);
        return `<td class="${s.length > 60 ? "cell-long" : ""}" title="${escapeHtml(s.slice(0, 300))}">${escapeHtml(truncate(s, 80))}</td>`;
      }).join("");
      return `<tr>${cells}<td><button class="row-del" data-i="${ri}">删</button></td></tr>`;
    }).join("");
    wrap.innerHTML = `
      <div class="d-table-wrap"><table class="d-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
      <div class="d-row" style="margin-top:8px"><span class="d-empty">共 ${data.total} 行</span></div>`;
    $$(".row-del", wrap).forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = rows[+btn.dataset.i];
        const pkCol = (await loadMdbTables()).find((t) => t.name === table)?.pk || "id";
        if (!confirm(`删除该行（${pkCol}=${row[pkCol]}）？`)) return;
        try {
          await apiPost("/api/memorydb/delete", { table, pk_value: row[pkCol] });
          toast("已删除", "success");
          loadMdbQuery(el, table, where);
        } catch (e) { toast(e.message, "error"); }
      });
    });
  } catch (e) {
    wrap.innerHTML = drawerErr(e);
  }
}

async function showMdbInsert(el, table) {
  try {
    const data = await apiGet(`/api/memorydb/schema?table=${encodeURIComponent(table)}`);
    const cols = (data.columns || []).filter((c) => !c.auto);
    const form = cols.map((c) => `
      <div class="d-row">
        <label>${escapeHtml(c.name)}${c.notnull ? " *" : ""}</label>
        <input class="d-input" data-col="${c.name}" placeholder="${escapeHtml(c.type || "")}">
      </div>`).join("");
    const overlay = document.createElement("div");
    overlay.className = "d-card";
    overlay.innerHTML = `<h4 style="margin-bottom:8px">新增行 · ${escapeHtml(table)}</h4>${form}
      <div class="d-row"><button class="d-btn primary" id="mdbInsertOk">插入</button>
      <button class="d-btn" id="mdbInsertCancel">取消</button></div>`;
    $("#mdbTableWrap", el).prepend(overlay);
    $("#mdbInsertCancel", overlay).addEventListener("click", () => overlay.remove());
    $("#mdbInsertOk", overlay).addEventListener("click", async () => {
      const columns = [], values = [];
      cols.forEach((c) => {
        const inp = overlay.querySelector(`[data-col="${c.name}"]`);
        const v = inp ? inp.value.trim() : "";
        if (v !== "") { columns.push(c.name); values.push(v); }
      });
      try {
        await apiPost("/api/memorydb/insert", { table, columns, values });
        overlay.remove();
        toast("已插入", "success");
        const where = $("#mdbWhere", el) ? $("#mdbWhere", el).value : "";
        loadMdbQuery(el, table, where);
      } catch (e) { toast(e.message, "error"); }
    });
  } catch (e) { toast(e.message, "error"); }
}

/* ---- 工具 ---- */
async function renderToolsTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/tools");
    const tools = data.tools || [];
    if (!tools.length) { el.innerHTML = `<div class="d-empty">暂无工具（${data.error || ""}）</div>`; return; }
    el.innerHTML = drawerSection(`已注册工具（${tools.length}）`, tools.map((t) => {
      const props = (t.args_schema && t.args_schema.properties) || {};
      const params = Object.keys(props).map((k) => `${k}: ${props[k].type || "any"}`).join(", ");
      return `<div class="tool-item"><div class="t-name">${escapeHtml(t.name)}</div>
        <div class="t-desc">${escapeHtml(t.description || "")}</div>
        ${params ? `<div class="t-desc" style="font-family:var(--mono);color:var(--text-faint)">参数: ${escapeHtml(truncate(params, 140))}</div>` : ""}</div>`;
    }).join(""));
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 定时任务 ---- */
async function renderSchedTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/scheduler");
    const tasks = data.tasks || [];
    const list = tasks.length ? tasks.map((t) => `
      <div class="sched-item">
        <div class="s-head">
          <span class="s-name">${escapeHtml(t.name)}</span>
          <span class="s-badge ${t.enabled ? "on" : "off"}">${t.enabled ? "运行中" : "已停用"}</span>
          <span style="flex:1"></span>
          <button class="d-btn sm" data-act="toggle" data-id="${t.id}">${t.enabled ? "停用" : "启用"}</button>
          <button class="d-btn sm danger" data-act="del" data-id="${t.id}">删除</button>
        </div>
        <div class="s-meta">${t.schedule_type === "interval" ? `每 ${t.interval_seconds}s` : `每日 ${t.daily_time}`} · ${t.thread_id || "默认线程"}</div>
        <div class="s-result">${escapeHtml(truncate(t.prompt, 120))}</div>
        ${t.last_result ? `<div class="s-result" style="color:var(--text-faint)">上次: ${escapeHtml(truncate(t.last_result, 140))}</div>` : ""}
      </div>`).join("") : `<div class="d-empty">暂无定时任务</div>`;

    el.innerHTML = `
      <div class="d-card">
        <h4 style="margin-bottom:8px">新建定时任务</h4>
        <div class="d-row"><label>任务名</label><input class="d-input" id="schedName" placeholder="如：每日日报"></div>
        <div class="d-row"><label>类型</label>
          <select class="d-select" id="schedType">
            <option value="interval">间隔执行</option><option value="daily">每日执行</option>
          </select>
        </div>
        <div class="d-row" id="schedIntervalRow"><label>间隔(秒)</label><input class="d-input" id="schedInterval" value="3600"></div>
        <div class="d-row hidden" id="schedDailyRow"><label>时间 (HH:MM)</label><input class="d-input" id="schedDaily" placeholder="09:00"></div>
        <div class="d-row"><label>提示词</label><textarea class="d-input" id="schedPrompt" rows="2" placeholder="任务内容…"></textarea></div>
        <div class="d-row"><button class="d-btn primary" id="schedAdd">创建</button></div>
      </div>
      ${drawerSection("任务列表", list)}`;

    $("#schedType", el).addEventListener("change", () => {
      const v = $("#schedType", el).value;
      $("#schedIntervalRow", el).classList.toggle("hidden", v !== "interval");
      $("#schedDailyRow", el).classList.toggle("hidden", v !== "daily");
    });
    $("#schedAdd", el).addEventListener("click", async () => {
      const name = $("#schedName", el).value.trim();
      const prompt = $("#schedPrompt", el).value.trim();
      const type = $("#schedType", el).value;
      const payload = { name, prompt, schedule_type: type };
      if (type === "interval") payload.interval_seconds = +($("#schedInterval", el).value || 0);
      else payload.daily_time = $("#schedDaily", el).value.trim();
      try {
        await apiPost("/api/scheduler", payload);
        toast("已创建", "success");
        renderSchedTab(el);
      } catch (e) { toast(e.message, "error"); }
    });
    $$("[data-act]", el).forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        try {
          if (btn.dataset.act === "toggle") await apiPost(`/api/scheduler/${id}/toggle`);
          else await fetch(`/api/scheduler/${id}`, { method: "DELETE" });
          toast("已更新", "success");
          renderSchedTab(el);
        } catch (e) { toast(e.message, "error"); }
      });
    });
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 模型管理 ---- */
async function renderModelsTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/models");
    const catalog = data.catalog || {};
    const cur = data.current || {};
    State.model = { provider: cur.provider, model: cur.model };
    refreshTopbar();

    const groups = Object.keys(catalog).map((pid) => {
      const info = catalog[pid];
      const chips = (info.models || []).map((m) => {
        const isCur = pid === cur.provider && m === cur.model;
        return `<button class="model-chip${isCur ? " current" : ""}" data-provider="${pid}" data-model="${escapeHtml(m)}"${isCur ? ' disabled' : ""}>${escapeHtml(m)}${isCur ? " ✓" : ""}</button>`;
      }).join("");
      return `<div class="model-group">
        <div class="mg-head"><span class="mg-label">${escapeHtml(info.label || pid)}</span>
        <span class="mg-builtin">${info.builtin ? "内置" : "自定义"}</span></div>
        ${chips || '<span class="d-empty">无模型</span>'}
        ${info.custom && info.base_url ? `<div class="d-empty" style="font-size:11px">${escapeHtml(info.base_url)}</div>` : ""}
      </div>`;
    }).join("");

    el.innerHTML = `
      <div class="d-card">
        <h4 style="margin-bottom:6px">当前模型</h4>
        <div class="d-row"><span class="d-empty">${escapeHtml(cur.provider)} / ${escapeHtml(cur.model)}</span></div>
        <div class="d-row"><button class="d-btn sm" id="modelRefresh">刷新</button></div>
      </div>
      ${drawerSection("模型目录（点击切换）", groups)}
      <div class="d-card">
        <h4 style="margin-bottom:8px">接入自定义模型</h4>
        <div class="d-row"><label>名称</label><input class="d-input" id="mLabel" placeholder="如：我的网关"></div>
        <div class="d-row"><label>Base URL</label><input class="d-input" id="mUrl" placeholder="https://api.xxx.com/v1"></div>
        <div class="d-row"><label>API Key</label><input class="d-input" id="mKey" placeholder="sk-…（多个用逗号分隔）"></div>
        <div class="d-row"><label>模型列表</label><input class="d-input" id="mModels" placeholder="模型1,模型2"></div>
        <div class="d-row">
          <button class="d-btn" id="mFetch">🔄 获取列表</button>
          <button class="d-btn primary" id="mAdd">✅ 接入</button>
        </div>
      </div>`;

    $("#modelRefresh", el).addEventListener("click", () => renderModelsTab(el));
    $$(".model-chip", el).forEach((chip) => {
      chip.addEventListener("click", async () => {
        if (chip.disabled) return;
        const provider = chip.dataset.provider;
        const model = chip.dataset.model;
        try {
          const r = await apiPost("/api/switch-model", { provider, model });
          State.model = { provider, model };
          refreshTopbar();
          toast(`已切换到 ${model}`, "success");
          renderModelsTab(el);
        } catch (e) { toast(e.message, "error"); }
      });
    });
    $("#mFetch", el).addEventListener("click", async () => {
      const base_url = $("#mUrl", el).value.trim();
      const api_key = $("#mKey", el).value.trim();
      if (!base_url) { toast("请先填写 Base URL", "error"); return; }
      try {
        const r = await apiPost("/api/models/fetch", { base_url, api_key });
        $("#mModels", el).value = (r.models || []).join(",");
        toast(`获取到 ${(r.models || []).length} 个模型`, "success");
      } catch (e) { toast(e.message, "error"); }
    });
    $("#mAdd", el).addEventListener("click", async () => {
      const payload = {
        label: $("#mLabel", el).value.trim(),
        base_url: $("#mUrl", el).value.trim(),
        api_key: $("#mKey", el).value.trim(),
        models: $("#mModels", el).value,
      };
      try {
        await apiPost("/api/models/add", payload);
        toast("已接入", "success");
        renderModelsTab(el);
      } catch (e) { toast(e.message, "error"); }
    });
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 交付物 ---- */
async function renderDelivTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/deliverables");
    const files = data.files || [];
    if (!files.length) { el.innerHTML = `<div class="d-empty">暂无交付物</div>`; return; }
    el.innerHTML = drawerSection("交付物（" + files.length + "）", files.map((f) => `
      <div class="deliv-item">
        <span class="dv-name">${escapeHtml(f.name)}</span>
        <span class="dv-size">${(f.size / 1024).toFixed(1)}KB</span>
        <button class="d-btn sm" data-act="preview" data-name="${escapeHtml(f.name)}">预览</button>
        <a class="d-btn sm" href="/api/deliverables/download?name=${encodeURIComponent(f.name)}" download>下载</a>
      </div>`).join("")) +
      `<div id="delivPreviewBox"></div>`;
    $$("[data-act=preview]", el).forEach((btn) => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.name;
        try {
          const d = await apiGet(`/api/deliverables/preview?name=${encodeURIComponent(name)}`);
          const box = $("#delivPreviewBox", el);
          box.innerHTML = `<div class="d-card"><h4 style="margin-bottom:6px">${escapeHtml(d.name)}</h4><pre class="d-pre">${escapeHtml(d.content || "")}</pre></div>`;
        } catch (e) { toast(e.message, "error"); }
      });
    });
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- Git ---- */
async function renderGitTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const logData = await apiGet("/api/git/log?limit=15");
    const commits = logData.commits || [];
    const logHtml = commits.length ? commits.map((c) => `
      <div class="git-log-item"><span class="g-msg">${escapeHtml(c.message || "")}</span>
      <span class="g-hash">${escapeHtml((c.hash || "").slice(0, 7))}</span>
      <span class="g-date">${escapeHtml(c.date || "")}</span></div>`).join("")
      : `<div class="d-empty">暂无提交</div>`;

    el.innerHTML = `
      <div class="d-card">
        <div class="d-row">
          <label>自动快照</label>
          <span class="d-empty" id="gitAutoLabel">${logData.auto_git ? "已开启" : "已关闭"}</span>
          <button class="d-btn sm" id="gitAutoToggle">${logData.auto_git ? "关闭" : "开启"}</button>
          <button class="d-btn sm primary" id="gitSnapshot">手动快照</button>
        </div>
      </div>
      ${drawerSection("提交历史", logHtml)}
      <div class="d-card">
        <h4 style="margin-bottom:8px">工作区状态</h4>
        <pre class="d-pre" id="gitStatus">加载中…</pre>
      </div>
      <div class="d-card">
        <h4 style="margin-bottom:8px">未提交 Diff</h4>
        <pre class="d-pre" id="gitDiff">加载中…</pre>
      </div>`;

    $("#gitAutoToggle", el).addEventListener("click", async () => {
      const next = !logData.auto_git;
      await apiPost("/api/git/auto", { enabled: next });
      renderGitTab(el);
    });
    $("#gitSnapshot", el).addEventListener("click", async () => {
      try {
        const r = await apiPost("/api/git/snapshot", { reason: "控制面板手动快照" });
        toast(r.message || "已快照", "success");
        renderGitTab(el);
      } catch (e) { toast(e.message, "error"); }
    });
    try {
      const st = await apiGet("/api/git/status");
      $("#gitStatus", el).textContent = st.stdout || st.error || "(干净)";
    } catch (e) { $("#gitStatus", el).textContent = e.message; }
    try {
      const df = await apiGet("/api/git/diff");
      $("#gitDiff", el).textContent = (df.diff || "(无差异)").slice(0, 3000);
    } catch (e) { $("#gitDiff", el).textContent = e.message; }
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 抽屉 tab 注册表 ---- */
const DRAWER_LOADERS = {
  state: renderStateTab,
  prompt: renderPromptTab,
  trace: renderTraceTab,
  memory: renderMemoryTab,
  memorydb: renderMemoryDBTab,
  tools: renderToolsTab,
  sched: renderSchedTab,
  models: renderModelsTab,
  deliv: renderDelivTab,
};

const DRAWER_TABS = [
  { id: "state", label: "State", icon: "📊" },
  { id: "prompt", label: "提示词", icon: "📄" },
  { id: "trace", label: "追踪", icon: "🧭" },
  { id: "memory", label: "记忆", icon: "🧠" },
  { id: "memorydb", label: "Memory DB", icon: "🗄️" },
  { id: "tools", label: "工具", icon: "🔧" },
  { id: "sched", label: "定时任务", icon: "🗓️" },
  { id: "models", label: "模型", icon: "⚙️" },
  { id: "deliv", label: "交付物", icon: "📦" },
];

function initDrawer() {
  drawerTabsEl.innerHTML = DRAWER_TABS.map((t) =>
    `<button class="drawer-tab" data-id="${t.id}">${t.icon} ${t.label}</button>`).join("");
  $$(".drawer-tab", drawerTabsEl).forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.id));
  });
}

/* ==================== SSE 实时事件 ==================== */
function startSSE() {
  if (State.sse) return;
  try {
    const es = new EventSource("/api/sse");
    State.sse = es;
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data);
        handleLiveEvent(evt);
      } catch (err) { /* 忽略 */ }
    };
    es.onerror = () => {
      // 自动重连由 EventSource 内置处理
    };
  } catch (e) {
    State.sse = null;
  }
}

function handleLiveEvent(evt) {
  if (evt.type === "log") {
    if (State.drawerTab === "logs") renderLogsTab(drawerBody);
  } else if (evt.type === "event") {
    if (State.drawerTab === "events") renderEventsTab(drawerBody);
  }
}

/* ==================== 事件绑定 ==================== */
function bindEvents() {
  $("#btnNewChat").addEventListener("click", newThread);
  $("#btnSend").addEventListener("click", sendFromComposer);
  $("#btnStop").addEventListener("click", stopChat);
  $("#btnToggleTheme").addEventListener("click", cycleTheme);
  $("#btnCollapseSidebar").addEventListener("click", () => {
    $("#app").classList.add("side-collapsed");
  });
  $("#btnSidebar").addEventListener("click", () => {
    $("#app").classList.remove("side-collapsed");
  });
  $("#threadSearch").addEventListener("input", () => loadThreads());
  $("#btnDrawer").addEventListener("click", openDrawer);
  $("#btnDrawerClose").addEventListener("click", closeDrawer);
  $("#drawerMask").addEventListener("click", closeDrawer);
  $("#btnClearLogs").addEventListener("click", async () => {
    try {
      await apiPost("/api/clear-logs", {});
      toast("日志/事件已清空", "success");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#sidebarModel").addEventListener("click", () => {
    openDrawer();
    activateTab("models");
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
      e.preventDefault();
      newThread();
    }
    if (e.key === "Escape") {
      if (!$("#drawer").classList.contains("hidden") && $("#drawer").classList.contains("show")) closeDrawer();
      else hideCmdMenu();
    }
  });
}

/* ==================== 初始化 ==================== */
async function init() {
  // 主题
  applyTheme();
  // 模型信息
  try {
    const m = await apiGet("/api/models");
    const cur = m.current || {};
    State.model = { provider: cur.provider, model: cur.model };
  } catch (e) { /* 忽略 */ }

  // 会话列表 & 当前会话
  const data = await loadThreads();
  const curTid = data.current_thread_id;
  if (curTid) {
    State.threadId = curTid;
    await loadHistory(curTid);
  } else {
    renderWelcome();
  }
  refreshTopbar();

  // UI
  initDrawer();
  bindEvents();
  startSSE();
  chatInput.focus();
}

document.addEventListener("DOMContentLoaded", init);
