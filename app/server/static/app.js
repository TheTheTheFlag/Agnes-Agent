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
  streamBuffer: "",           // token 累积缓冲（当前段）
  procCount: 0,               // 已折叠的"工具轮思考过程"段计数
  renderTimer: null,
  liveToolName: "",           // LiveStatus 当前工具名（chunk 增量累积用）
  liveArgs: "",               // LiveStatus 参数累积缓冲
  displayMode: "verbose",     // 对话显示模式：verbose=详细（每事件独立气泡）/ compact=简洁（聚合成摘要行）
  procGroup: null,            // 简洁模式下当前打开的"执行过程"聚合组（{el, body, counts, nodes}）
  approvalCard: null,         // 当前审批卡片
  drawerTab: null,
  sse: null,
  bulkMode: false,            // 会话列表批量删除模式
  bulkSelected: new Set(),    // 批量删除选中的 thread_id
  renderedThreads: [],        // 当前渲染的可见会话（供"全选"）
  todos: [],                  // 当前会话的任务待办（planner 拆的子任务）
  todosExpanded: false,       // 待办面板是否展开
  todoPanelDismissed: false,  // 用户是否手动关闭了面板
  pendingAttachments: [],     // 待发送附件 [{path, name, isImg}]：上传/粘贴后先进附件条，点发送才发出
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
  // SQLite datetime 默认存 "YYYY-MM-DD HH:MM:SS.ffffff"，非 ISO；补 T 使其可被 new Date 解析
  const d = new Date(String(ts).replace(" ", "T"));
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
function onUnauthorized() {
  // 登录态失效（未登录 / token 过期）：回到登录界面
  showLogin();
  throw new Error("未登录或登录已过期");
}

async function apiGet(url) {
  const r = await fetch(url);
  if (r.status === 401) return onUnauthorized();
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (r.status === 401) return onUnauthorized();
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

/* ==================== 登录 ==================== */
function showLogin() {
  $("#loginOverlay").classList.remove("hidden");
  $("#loginError").classList.add("hidden");
  $("#loginPass").value = "";
  setTimeout(() => { const u = $("#loginUser"); if (u) u.focus(); }, 60);
}

function hideLogin() {
  $("#loginOverlay").classList.add("hidden");
}

async function checkAuth() {
  try {
    const r = await fetch("/api/auth/status");
    const d = await r.json().catch(() => ({}));
    return !!d.authenticated;
  } catch (e) {
    return false;
  }
}

function bindAuthEvents() {
  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#btnLogin");
    btn.disabled = true;
    $("#loginError").classList.add("hidden");
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: $("#loginUser").value.trim(), password: $("#loginPass").value }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "登录失败");
      hideLogin();
      init();   // 登录成功 → 初始化主界面
    } catch (err) {
      $("#loginError").textContent = err.message || "登录失败";
      $("#loginError").classList.remove("hidden");
    } finally {
      btn.disabled = false;
    }
  });
  $("#btnLogout").addEventListener("click", async () => {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) { /* 忽略 */ }
    showLogin();
  });
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

/* 上传文件 → 加入待发送附件条（不自动发送）。用户输入文字后点发送，附件随消息一起发出。 */
async function uploadFileToBar(file) {
  const imgLike = file.type.startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(file.name || "");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    if (r.status === 401) return onUnauthorized();
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    if (State.pendingAttachments.length >= 6) throw new Error("一次最多挂 6 个附件");
    State.pendingAttachments.push({ path: d.path, name: d.name || d.path, isImg: imgLike });
    renderAttachBar();
    chatInput.focus();
  } catch (e) {
    toast("上传失败: " + e.message);
  }
}

/* 渲染待发送附件条（缩略图/文件名 + 移除按钮） */
function renderAttachBar() {
  const bar = $("#attachBar");
  if (!bar) return;
  const atts = State.pendingAttachments || [];
  bar.innerHTML = atts.map((a, i) => `
    <div class="attach-item">
      ${a.isImg ? `<img src="/api/${a.path}" alt="">` : `<div class="attach-file">📄</div>`}
      <span class="attach-name" title="${escapeHtml(a.name)}">${escapeHtml(a.name)}</span>
      <button class="attach-remove" data-i="${i}" title="移除附件">×</button>
    </div>`).join("");
  bar.classList.toggle("hidden", !atts.length);
  $$(".attach-remove", bar).forEach((b) => b.addEventListener("click", () => {
    State.pendingAttachments.splice(+b.dataset.i, 1);
    renderAttachBar();
  }));
}

/* 图片链接预处理：把消息里的裸图片 URL / 本地技能产物路径转成 markdown 图片语法，
   使 marked 渲染成 <img>。本地产物走受登录保护的 /api/skill-media/ 端点。 */
function imageizeMarkdown(text) {
  if (!text) return text;
  let t = text;
  // 0) 用户上传文件：uploads/<file> → /api/uploads/<file>（含 markdown 链接内的 src）
  t = t.replace(/(uploads\/[A-Za-z0-9_.\-]+)/g, "/api/$1");
  // 1) 本地技能产物：app/skills/<skill>/output/<rest> → /api/skill-media/<skill>/<rest>
  t = t.replace(/app\/skills\/([A-Za-z0-9_.\-]+)\/output\/([A-Za-z0-9_\-./]+)/g, (m, skill, rest) => {
    if (!/\.(png|jpe?g|gif|webp|bmp)$/i.test(rest)) return m;
    return `![${skill}](${"/api/skill-media/" + skill + "/" + rest})`;
  });
  // 2) 远程图片 URL（非 markdown 链接内部、以图片扩展名结尾）
  t = t.replace(/(?<!\]\()https?:\/\/[^\s"'<>]+/g, (m) => {
    const u = m.replace(/[),.;，。、:：]+$/, "");
    if (/\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i.test(u)) {
      return `![图片](${u})${m.slice(u.length)}`;
    }
    return m;
  });
  return t;
}

function renderMarkdown(text) {
  if (!text) return "";
  // 先转义（防 XSS）→ marked 渲染；图片 URL 已在上一步转成 markdown 图片语法
  let html = marked.parse(escapeHtml(imageizeMarkdown(text)));
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
  closeProcGroup();  // 欢迎页 = 新会话起点，过程聚合组一并作废
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
  closeProcGroup();  // 正文出现即结束当前"执行过程"聚合组（简洁模式下另有新组）
  const inner = messagesInner();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `<div class="msg-body"><div class="msg-text">${renderMarkdown(text)}</div></div>`;
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
  // 正文不切分本轮的过程摘要行，只登记为"本轮正文锚点"：
  // 之后若还有事件，摘要行会插到这条正文之前（过程在上、回答在下）
  noteTurnTextEl(wrap);
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

/* ==================== 审批卡片 ==================== */
function renderApprovalCard(data) {
  const host = State.currentAssistantEl || addAssistantBubble("");
  const card = document.createElement("div");
  card.className = "approval-card";
  card.innerHTML = `
    <div class="approval-title">
      <svg class="approval-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="7.5" width="9" height="6" rx="1.5"/><path d="M5.5 7.5V5a2.5 2.5 0 0 1 5 0v2.5"/></svg>
      <span>需要你的确认</span>
    </div>
    <div class="approval-question">${escapeHtml(data.question || "是否允许执行该操作？")}</div>
    ${data.command ? `<div class="approval-cmd">${escapeHtml(data.command)}</div>` : ""}
    <div class="approval-actions">
      <button class="btn-approve">允许执行</button>
      <button class="btn-deny">拒绝</button>
    </div>`;
  $(".btn-approve", card).addEventListener("click", () => respondApproval(true, data.mode));
  $(".btn-deny", card).addEventListener("click", () => respondApproval(false, data.mode));
  // 移除气泡里的加载中动画（审批卡取代了它）
  const msgText = $(".msg-text", host);
  if (msgText) $(".typing-dots", msgText)?.remove();
  msgText.appendChild(card);
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

/* ==================== 任务待办面板 ==================== */
// 设置/刷新待办列表：items = [{id, text, status: 'todo'|'doing'|'done'}]，
// 由后端 SSE 事件触发（node_thought 携带 subtask 状态）。
function setTodos(items) {
  State.todos = items || [];
  renderTodoPanel();
}

// _INJECTED_: summarizer final 事件兜底锚点
function _isSummarizerFresh(last, evt) {
  if (!last || !last.timestamp) return false;
  return new Date(last.timestamp).getTime() >= new Date(evt.timestamp || 0).getTime() - 2000;
}
function renderSummarizerFromHistory(last) {
  if (!last || !last.content) return;
  State.currentAssistantEl = addAssistantBubble("");
  State.streamBuffer = last.content;
  const _host = State.currentAssistantEl;
  const _box = _host && $(".msg-text", _host);
  if (_box) {
    const _old = $("#stream-text", _box);
    if (_old) _old.remove();
    const _tn = document.createElement("div");
    _tn.id = "stream-text";
    _box.prepend(_tn);
  }
  endStreaming();
  scrollToBottom();
}
// 待办状态 → 图标
function todoMark(status) {
  if (status === "done") return "✓";
  if (status === "doing") return "⟳";
  if (status === "failed") return "✕";
  return "•";
}

// 把后端 DAG 节点快照（[{id, status, description, replaces, edges: ...}]）转成待办列表并刷新面板。
// 流程：
//   1) 解析"被替代"关系：replaces 指向的旧节点不显示（被新节点替代）
//   2) 过滤 skipped（作废节点不显示）
//   3) Kahn 拓扑分层：同层保持原序，跨层按依赖排序
//   4) 状态映射：success→done、running→doing、failed→failed（红 ✕）、其余→todo
// 规划任务全程由这个函数驱动"动态变化"的待办列表（替代原先的 DAG 图）。
function updateTodoFromNodes(nodes, edges) {
  if (!Array.isArray(nodes) || !nodes.length) return;

  // 1) 解析"被替代"关系
  const replacedIds = new Set();
  nodes.forEach((n) => { if (n.replaces) replacedIds.add(String(n.replaces)); });

  // 2) 过滤 skipped + 被替代的旧节点
  const alive = nodes.filter(
    (n) => (n.status || "") !== "skipped" && !replacedIds.has(String(n.id))
  );
  if (!alive.length) return;

  // 3) 拓扑分层（业界 DAG 渲染统一做法：Kahn 拓扑分层 + 同层保序）
  const layers = topoSortLayers(alive, Array.isArray(edges) ? edges : []);

  // 4) 拍平成 items
  const items = [];
  for (const layer of layers) {
    for (const n of layer) {
      const st = n.status || "todo";
      let status = "todo";
      if (st === "success") status = "done";
      else if (st === "running") status = "doing";
      else if (st === "failed") status = "failed";
      items.push({
        id: n.id,
        text: (n.description || n.desc || String(n.id)).trim(),
        status,
        rawStatus: st,
        replaces: n.replaces || "",
      });
    }
  }
  setTodos(items);
}

// Kahn 拓扑分层：返 [[上游层], [中层], [下游层], ...]，同层保持原数组序。
// soft 边不参与入度计算（业界做法：软依赖不阻塞）。
function topoSortLayers(nodes, edges) {
  const idToNode = new Map(nodes.map((n) => [String(n.id), n]));
  const indeg = new Map(nodes.map((n) => [String(n.id), 0]));
  const adj = new Map(nodes.map((n) => [String(n.id), []]));
  for (const e of edges) {
    const f = String(e.from), t = String(e.to);
    if (idToNode.has(f) && idToNode.has(t) && !e.soft) {
      adj.get(f).push(idToNode.get(t));
      indeg.set(t, indeg.get(t) + 1);
    }
  }
  // 同层内按"原数组序"出列：维护一个 in-order 的初始 frontier
  const ordered = nodes.map((n) => String(n.id));
  const inFrontier = new Set(ordered.filter((id) => indeg.get(id) === 0));
  const layers = [];
  while (inFrontier.size) {
    const layer = ordered.filter((id) => inFrontier.has(id)).map((id) => idToNode.get(id));
    layers.push(layer);
    const next = new Set();
    for (const u of layer) {
      for (const v of adj.get(String(u.id))) {
        const d = indeg.get(String(v.id)) - 1;
        indeg.set(String(v.id), d);
        if (d === 0) next.add(String(v.id));
      }
    }
    inFrontier.clear();
    inFrontier.add(...next);
  }
  // 兜底：若有环（不该发生），剩余的放最后一层
  const placed = new Set();
  layers.forEach((l) => l.forEach((n) => placed.add(String(n.id))));
  const remaining = nodes.filter((n) => !placed.has(String(n.id)));
  if (remaining.length) layers.push(remaining);
  return layers;
}

function renderTodoPanel() {
  const panel = $("#todoPanel");
  if (!panel) return;
  const items = State.todos;
  if (!items.length) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  // 徽章：完成/总数；有失败节点时追加"失败 N"，否则失败会完全看不见
  const done = items.filter((t) => t.status === "done").length;
  const failed = items.filter((t) => t.status === "failed").length;
  const total = items.length;
  const badge = $("#todoBadge");
  if (badge) {
    badge.textContent = failed ? `待办 ${done}/${total} · 失败 ${failed}` : `待办 ${done}/${total}`;
    badge.classList.toggle("done", done === total);
    badge.classList.toggle("failed", failed > 0);
  }
  // 摘要行：优先显示"未完成/失败"中的最新一条，否则显示最后一条已完成
  const latest = items.find((t) => t.status !== "done") || items[items.length - 1];
  const latestEl = $("#todoLatest");
  if (latestEl) {
    latestEl.textContent = `${todoMark(latest.status)} ${latest.text || latest.id}`;
  }
  // 展开图标
  const toggle = $("#todoToggle");
  if (toggle) toggle.textContent = State.todosExpanded ? "▴" : "▾";
  // 列表
  const list = $("#todoList");
  if (list) {
    list.innerHTML = items.map((t) => {
      const mark = todoMark(t.status);
      // "被替代"角标：局部重规划生成的新节点会标注它替代了哪个旧节点，
      // 避免用户困惑"为什么 a1 和 design 看起来都在第一层"。
      const replTag = t.replaces
        ? `<span class="todo-repl" title="本次由局部重规划生成，替代原节点 ${escapeHtml(t.replaces)}">⟲ ${escapeHtml(t.replaces)}</span>`
        : "";
      return `<div class="todo-item ${t.status}"><span class="todo-status">${mark}</span><span class="todo-text">${escapeHtml(t.text || t.id || "")}</span>${replTag}</div>`;
    }).join("");
  }
  // 展开状态
  panel.classList.toggle("expanded", State.todosExpanded);
}

function toggleTodoPanel() {
  if (!State.todos.length) return;
  State.todosExpanded = !State.todosExpanded;
  renderTodoPanel();
}

function closeTodoPanel() {
  State.todoPanelDismissed = true;
  const panel = $("#todoPanel");
  if (panel) panel.classList.add("hidden");
}

function resetTodoPanel() {
  State.todos = [];
  State.todosExpanded = false;
  State.todoPanelDismissed = false;
  renderTodoPanel();
}

/* ==================== 移动端适配（<768px，与 CSS 断点一致） ==================== */
function isMobile() {
  return window.matchMedia("(max-width: 767.98px)").matches;
}

/* 左侧会话栏：桌面用 side-collapsed 内联折叠，手机用 side-open 抽屉+遮罩，两套状态互不干扰 */
function openSidebar() {
  const app = $("#app");
  if (isMobile()) {
    app.classList.remove("side-collapsed");
    app.classList.add("side-open");
    const mask = $("#sidebarMask");
    mask.classList.remove("hidden");
    requestAnimationFrame(() => mask.classList.add("show"));
  } else {
    app.classList.remove("side-collapsed");
  }
}

function closeSidebar() {
  const app = $("#app");
  if (isMobile()) {
    app.classList.remove("side-open");
    const mask = $("#sidebarMask");
    mask.classList.remove("show");
    setTimeout(() => mask.classList.add("hidden"), 240);
  } else {
    app.classList.add("side-collapsed");
  }
}

/* 移动端：键盘弹出时按 visualViewport 收缩根高度，避免输入框被遮挡 */
function syncViewportHeight() {
  const app = $("#app");
  if (isMobile() && window.visualViewport) {
    app.style.height = window.visualViewport.height + "px";
  } else {
    app.style.height = "";
  }
}

/* ==================== 审批模式（每次询问 / 本次会话允许 / 永久允许） ==================== */
function setApprovalMode(mode) {
  $$(".approval-mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  apiPost("/api/command", { command: "/system " + mode }).then((d) => {
    if (d && d.result) toast("🛡️ " + d.result, "success");
  }).catch((e) => toast(e.message, "error"));
}

function refreshApprovalMode() {
  const bar = $("#approvalModeBar");
  if (!bar) return;
  // 从后端读取当前审批模式并高亮（新建/切换会话后调用，保证按钮与实际生效模式一致）
  apiPost("/api/command", { command: "/system" }).then((d) => {
    const m = (d && d.result || "").match(/(per_ask|session_allow|always_allow)/);
    if (m) $$(".approval-mode-btn", bar).forEach((b) => b.classList.toggle("active", b.dataset.mode === m[1]));
  }).catch(() => {});
}

function initApprovalMode() {
  const bar = $("#approvalModeBar");
  if (!bar) return;
  $$(".approval-mode-btn", bar).forEach((b) => {
    b.addEventListener("click", () => {
      // 桌面：三个按钮常显，直接切换；手机：默认只显示当前模式胶囊，点击弹出三选
      if (!isMobile()) { setApprovalMode(b.dataset.mode); return; }
      if (bar.classList.contains("open")) {
        setApprovalMode(b.dataset.mode);
        bar.classList.remove("open");
      } else {
        bar.classList.add("open");
      }
    });
  });
  // 移动端：点击弹层以外区域时收起
  document.addEventListener("click", (e) => {
    if (isMobile() && !bar.contains(e.target)) bar.classList.remove("open");
  });
  refreshApprovalMode();
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
      box.querySelectorAll(":scope > :not(.tool-card):not(.approval-card):not(.proc-text)").forEach((n) => n.remove());
      const tn = document.createElement("div");
      tn.id = "stream-text";
      box.prepend(tn);
    }
  }
  State.streamBuffer += text;
  scheduleStreamRender();
}

/* ---------------- 极简 LiveStatus: 只显示当前工具 name + args ---------------- */
function setLiveStatus(name, args) {
  const root = $("#liveStatus");
  const text = $(".live-status-text", root);
  if (!root || !text) return;
  root.classList.add("running");
  const argStr = (args || "").trim().slice(0, 60);
  text.textContent = argStr ? `${name}(${argStr}${argStr.length >= 60 ? "…" : ""})` : name;
}
function setLiveStatusIdle() {
  const root = $("#liveStatus");
  const text = $(".live-status-text", root);
  if (!root || !text) return;
  root.classList.remove("running");
  text.textContent = "空闲";
  State.liveToolName = "";
  State.liveArgs = "";
}

/* 工具轮"思考过程"折叠：
   模型在每轮工具调用前会先输出一句过程性文本（如"让派蒙先确认一下…"），
   这些不是最终回答。由于后端把每轮 LLM 的 content 都按 token 推给前端，
   这里以 tool_call 首个 chunk 为"该轮文本已收齐"的信号：把缓冲中的文本
   从正文移出，折叠成灰色小段（默认收起，点击展开），避免满屏口头禅。 */
function foldToolRoundText() {
  const host = State.currentAssistantEl;
  if (!host) return;
  const box = $(".msg-text", host);
  const tn = box && $("#stream-text", box);
  const text = (State.streamBuffer || "").trim();
  if (!tn || !text) return;  // 无正文的工具轮（常见）不处理
  State.procCount += 1;
  State.streamBuffer = "";
  tn.textContent = "";
  const seg = document.createElement("div");
  seg.className = "proc-text collapsed";
  seg.innerHTML = `<span class="proc-label">💭 思考过程 ${State.procCount}</span><div class="proc-body"></div>`;
  $(".proc-body", seg).innerHTML = renderMarkdown(text);
  seg.addEventListener("click", (e) => {
    e.stopPropagation();
    seg.classList.toggle("collapsed");
  });
  box.insertBefore(seg, tn);
  scrollToBottom();
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
    const tn = $("#stream-text", host);
    // 只有确实有内容时才用缓冲刷新。done / node end / 停止 都会二次进入这里，
    // 此时缓冲已空，无条件 flush 会把刚渲染好的最终回答覆盖成空白
    // （表现为"回答一闪而过"）。
    if (State.streamBuffer) {
      flushStreamRender();
      if (tn) tn.innerHTML = renderMarkdown(State.streamBuffer);
    }
    // 没有文本内容、没有任何卡片/思考过程段 → 移除空气泡。
    // 注意：已经渲染出文字的不能当空气泡删掉（紧凑模式的最终回答就没有工具卡）。
    const hasExtras = $(".tool-card, .approval-card, .proc-text", host);
    const hasText = !!(tn && tn.textContent.trim());
    if (!State.streamBuffer && !hasExtras && !hasText) host.remove();
  }
  // 不在这里清 State.currentAssistantEl——
  // 真正的"流结束"由调用方在合适的时机显式清空（done/error/abort/node end 非 chatbot）
  // interrupt/resume 路径下，chatbot node end 和 final 都不应清空，避免新气泡被创建
  State.streamBuffer = "";
  scrollToBottom();
}

/* ==================== 对话显示模式（详细 / 简洁） ==================== */
/* 详细模式（verbose）：每个节点 / 模型调用 / 工具调用都是独立气泡，链路一目了然（原有效果）。
   简洁模式（compact）：过程事件收进一个可展开的摘要行
   「N 个工具 · M 段思考 · K 次模型调用」，对话区只留正文 + 折叠行。
   模式持久化在 localStorage；切换后当前会话立即按新模式重放。 */
const DISPLAY_MODE_KEY = "agnes-display-mode";
const DISPLAY_MODES = [
  { id: "verbose", icon: "🧩", name: "详细模式", desc: "每个节点 / 模型调用 / 工具调用都是独立气泡，适合盯链路调试。" },
  { id: "compact", icon: "✨", name: "简洁模式", desc: "过程事件聚合成一行「N 个工具 · M 段思考」摘要，点开才看细节，只留正文。" },
];

function displayModeName(mode) {
  const m = DISPLAY_MODES.find((x) => x.id === mode);
  return m ? m.name : mode;
}

// 启动时恢复上次选择（必须在首次渲染历史之前调用）
function initDisplayMode() {
  const saved = localStorage.getItem(DISPLAY_MODE_KEY);
  State.displayMode = saved === "compact" ? "compact" : "verbose";
  document.documentElement.dataset.display = State.displayMode;
}

function setDisplayMode(mode) {
  mode = mode === "compact" ? "compact" : "verbose";
  const changed = mode !== State.displayMode;
  State.displayMode = mode;
  localStorage.setItem(DISPLAY_MODE_KEY, mode);
  document.documentElement.dataset.display = mode;
  closeProcGroup();
  if (!changed) return;
  if (State.streaming) {
    // 回复还在生成中：不重放（避免打断当前流式气泡），新模式对后续渲染生效
    toast(`${displayModeName(mode)}已开启，本次回复结束后生效`);
    return;
  }
  toast(`已切换到${displayModeName(mode)}`);
  if (State.threadId) loadHistory(State.threadId);
}

/* ---- 简洁模式：把过程事件（节点/模型/工具/思考）聚合成一个可展开组 ---- */
const PROC_KIND_FMT = [
  ["tool", (n) => `${n} 个工具`],
  ["thought", (n) => `${n} 段思考`],
  ["llm", (n) => `${n} 次模型调用`],
  ["approval", (n) => `${n} 次审批`],
  ["event", (n) => `${n} 条事件`],
];

// 摘要行结构刻意保持极简：一行浅灰小字 + 右侧 ›，与 Reasonix 的过程行观感一致
// （不放图标、"执行过程"这类标签和计数器，避免变成"横幅卡片"）。
// 若本轮已有正文气泡（_turnTextEl），摘要行插到它之前——"过程在上、回答在下"。
function createProcGroup() {
  const group = document.createElement("details");
  group.className = "proc-group";
  group.innerHTML = `
    <summary class="proc-group-head">
      <span class="pg-counts"></span>
      <span class="pg-arrow">›</span>
    </summary>
    <div class="proc-group-body"></div>`;
  const inner = messagesInner();
  if (_turnTextEl && _turnTextEl.parentNode === inner) inner.insertBefore(group, _turnTextEl);
  else inner.appendChild(group);
  return { el: group, body: $(".proc-group-body", group), counts: {}, nodes: new Set() };
}

// 摘要只列"用户可见的过程量"，节点 start/end 属于链路调试信息，不计入
// （除非这一段过程只有节点事件——那时退化成"N 个节点"，总比光秃秃的"过程"有信息量）
function updateProcGroupSummary(g) {
  const parts = PROC_KIND_FMT.filter(([k]) => g.counts[k] > 0).map(([k, fmt]) => fmt(g.counts[k]));
  const el = $(".pg-counts", g.el);
  if (!el) return;
  el.textContent = parts.join(" · ") || (g.nodes.size ? `${g.nodes.size} 个节点` : "过程");
}

// 返回"当前过程气泡该塞进哪个容器"：详细模式 → null（由调用方兜底到消息流）；
// 简洁模式 → 当前聚合组（不存在、或已随消息流被清掉时新建），并把该事件计入摘要。
// 分组边界是"一轮一次"，只由用户消息 / 切换会话 / 清空消息区来关闭。
function procGroupHost(kind, statKey) {
  if (State.displayMode !== "compact") return null;
  const inner = messagesInner();
  let g = State.procGroup;
  if (!g || g.el.parentNode !== inner) {
    g = createProcGroup();
    State.procGroup = g;
  }
  if (kind === "node") g.nodes.add(statKey || "node");
  else if (kind) g.counts[kind] = (g.counts[kind] || 0) + 1;
  updateProcGroupSummary(g);
  return g.body;
}

// 本轮第一个正文气泡：新摘要行插在它之前，保证"过程摘要在上、回答在下"。
// 正文本身不再关闭聚合组——否则回答之后（如 summarizer / 记忆摘要）再发生的
// 事件会另起一条摘要行跑到回答下面去。
let _turnTextEl = null;
function noteTurnTextEl(el) {
  if (!el) return;
  const inner = messagesInner();
  if (!_turnTextEl || _turnTextEl.parentNode !== inner) _turnTextEl = el;
}

// 一轮过程的结束点：用户消息出现、切换/清空会话时调用（见调用点）
function closeProcGroup() {
  State.procGroup = null;
  _turnTextEl = null;
}

/* ==================== 独立事件气泡渲染（节点/模型/工具/思考） ==================== */
// 把任意长文本包进 <details>，超长默认收起、点击展开查看全部。
function wrapCollapsible(label, bodyHtml, startOpen) {
  const openAttr = startOpen ? " open" : "";
  return `<details class="evt-collapse"${openAttr}><summary>${label}</summary><div class="evt-body">${bodyHtml}</div></details>`;
}

// 通用独立气泡：icon 图标 + title 标题行 + (可选 meta) + body（可能含可展开内容）
function addEventBubble(kind, icon, title, metaHtml, bodyHtml, ts, host, statKey) {
  // host 可选：不传 → 渲染到对话区消息流（默认，自动滚到底）；
  // 传入其它容器时（调试面板的"追踪"页）复用同一套气泡样式，两处视觉保持一致。
  // 对话流路径下，简洁模式会自动把气泡收进"执行过程"聚合组。
  const inner = host || procGroupHost(kind, statKey) || messagesInner();
  const wrap = document.createElement("div");
  wrap.className = `msg assistant evtb evtb-${kind}`;
  const now = ts ? new Date(ts).toISOString() : new Date().toISOString();
  wrap.innerHTML = `
    <div class="msg-body">
      <div class="msg-head evtb-head">
        <span class="msg-avatar">A</span>
        <span class="msg-label">${icon} ${escapeHtml(title)}</span>
        <span class="msg-time">${fmtClock(now)}</span>
      </div>
      ${metaHtml ? `<div class="evtb-meta">${metaHtml}</div>` : ""}
      <div class="msg-text evtb-text">${bodyHtml || ""}</div>
    </div>`;
  inner.appendChild(wrap);
  if (!host) scrollToBottom();  // 只有对话区才需要自动滚动
  return wrap;
}

// 把对象安全转成可读多行文本（用于展示参数/结果）
function prettyText(obj) {
  if (obj == null) return "";
  if (typeof obj === "string") return obj;
  try {
    return typeof obj === "object" ? JSON.stringify(obj, null, 2) : String(obj);
  } catch (e) { return String(obj); }
}

// 节点变化气泡
function renderNodeEvent(evt, ts, host) {
  const name = evt.name || "";
  const phase = evt.phase === "start" ? "▶ 开始" : "■ 结束";
  const result = evt.result ? `<span class="evtb-chip">${escapeHtml(String(evt.result))}</span>` : "";
  addEventBubble("node", "🧩", `节点 ${name} ${phase}`, result, "", ts, host, name);
}

// 模型调用气泡：输入（可展开完整）+ 输出（可展开完整）+ 耗时
function renderLlmCall(data, ts, host) {
  const node = data.node || "";
  const duration = data.duration_ms != null ? ` · ${data.duration_ms}ms` : "";
  addEventBubble("llm", "🧠", `模型调用(${node})${duration}`,
    `<span class="evtb-chip">输入 ${(data.input || "").length} 字符 · 输出 ${(data.output || "").length} 字符</span>`,
    wrapCollapsible("📥 模型输入（点击展开/收起）", `<pre class="evt-pre">${escapeHtml(data.input || "")}</pre>`, false) +
    wrapCollapsible("📤 模型输出（点击展开/收起）", `<pre class="evt-pre">${escapeHtml(data.output || "")}</pre>`, false), ts, host);
}

// 工具调用气泡：工具名 + 参数 + 结果（可展开完整）
function renderToolEvent(evt, ts, host) {
  const name = evt.name || "tool";
  const node = evt.node ? ` · 子任务 ${evt.node}` : "";
  const argsText = prettyText(evt.params != null ? evt.params : evt.args);
  const resultText = evt.result != null ? evt.result : (evt.output_preview || "");
  addEventBubble("tool", "🔧", `工具 ${name}${node}`, "",
    wrapCollapsible("输入参数（点击展开/收起）", `<pre class="evt-pre">${escapeHtml(argsText)}</pre>`, false) +
    wrapCollapsible("执行结果（点击展开/收起）", `<pre class="evt-pre">${escapeHtml(String(resultText))}</pre>`, false), ts, host);
}

// 节点思考气泡（planner/executor/summarizer 的 node_thought）
function renderThoughtEvent(data, ts, host) {
  const role = data.role || "";
  const title = data.title || "思考";
  const text = data.text || "";
  addEventBubble("thought", "💭", String(title),
    `<span class="evtb-chip">${escapeHtml(String(role))}</span>`,
    wrapCollapsible("内容（点击展开/收起）", `<pre class="evt-pre">${escapeHtml(String(text))}</pre>`, false), ts, host);
}

// 历史回放：审批卡（只读，展示提问/命令/最终决定；不做交互）
function renderHistoryApproval(m, meta) {
  const decision = meta.allow == null ? "待用户确认（会话中断于此）"
    : meta.allow ? "✅ 已允许执行" : "⛔ 已拒绝";
  const mode = meta.mode || "";
  addEventBubble("approval", "🛂", "审批请求",
    `<span class="evtb-chip">${escapeHtml(decision)}${mode ? " · " + escapeHtml(mode) : ""}</span>`,
    `<div class="approval-q">${escapeHtml(meta.question || "是否允许执行该操作？")}</div>` +
    (meta.command ? `<div class="approval-cmd">${escapeHtml(meta.command)}</div>` : ""), m.timestamp);
}

// 历史回放：未知 kind 事件兜底（只展示类型与摘要，不丢内容）
function renderHistoryRawEvent(m, meta) {
  const label = m.kind || "event";
  addEventBubble("event", "•", label,
    `<span class="evtb-chip">${escapeHtml(String(m.role || ""))}</span>`,
    wrapCollapsible("内容（点击展开/收起）",
      `<pre class="evt-pre">${escapeHtml(m.content || JSON.stringify(meta) || "")}</pre>`, false), m.timestamp);
}

/* ==================== 对话流（SSE /api/chat） ==================== */
function handleChatEvent(evt) {
  const step = evt.step;
  if (step === "node") {
    // 节点变化 → 独立气泡（每个节点的 start/end 都展示）
    renderNodeEvent(evt);
    setLiveBadge(evt.phase === "start");
    // 节点结束时应清空流式状态，避免旧气泡残留
    if (evt.phase === "end" && evt.name === "chatbot") {
      // chatbot 节点结束后，立即清空状态，等待下一个节点开始
      endStreaming();
      State.currentAssistantEl = null;
      State.streamBuffer = "";
    } else if (evt.phase === "end" && evt.name === "summarizer") {
      // 兜底：summarizer 节点结束时主动拉一次 /api/messages，
      // 防止 SSE final 事件因 chat.py 的流竞态没传到前端。
      // 拉到的最后一条 assistant 消息即是"该看到的最终交付汇报"。
      (async () => {
        try {
          const r = await apiGet(`/api/messages?thread_id=${encodeURIComponent(State.threadId)}&limit=2`);
          const last = (r && r.messages || []).filter(m => m.role === "assistant").pop();
          if (last && last.content && _isSummarizerFresh(last, evt)) {
            renderSummarizerFromHistory(last);
          }
        } catch (e) { /* 静默兜底，不影响 UI */ }
      })();
    }
  } else if (step === "token") {
    // 模型输出已由 llm_call 独立气泡完整承载，这里不再逐字追加进气泡，避免重复。
    // 仅更新顶栏 liveStatus 的活跃状态（保持"运行中"指示）。
    // （保留空分支，避免未知 step 落入末尾静默逻辑之外的路径；token 本身无需进一步处理）
  } else if (step === "llm_call") {
    // 每次 LLM 调用的完整输入/输出 → 独立"模型调用"气泡（可点击展开全部）
    renderLlmCall(evt.data || {});
  } else if (step === "node_thought") {
    // 节点思考（planner/executor/summarizer 的 node_thought）→ 独立气泡
    renderThoughtEvent((evt.data) || {});
  } else if (step === "tool_chunk" || step === "tool") {
    // 工具调用 → 独立气泡（参数 + 结果，可展开）；同时更新顶栏实时状态行
    if (evt.name) {
      State.liveToolName = evt.name;
      State.liveArgs = evt.args || "";
      setLiveStatus(State.liveToolName, State.liveArgs);
    } else if (step === "tool_chunk" && State.liveToolName) {
      State.liveArgs = (State.liveArgs || "") + (evt.args || "");
      setLiveStatus(State.liveToolName, State.liveArgs);
    }
    if (step === "tool" && evt.phase === "end" && evt.name) {
      setLiveStatus(evt.name, evt.args || "");
      // 工具执行完成 → 独立工具气泡展示输入输出
      renderToolEvent(evt);
    }
  } else if (step === "approval") {
    renderApprovalCard(evt.data || {});
  } else if (step === "final") {
    // 最终答复：chatbot 节点通常已通过 token 流实时显示，final 只是兜底（避免重复覆盖，
    // 故仅在 streamBuffer 为空时应用）。但 summarizer 节点（规划任务收尾）的总结文本
    // 是 llm.invoke 一次性生成，不经 token 流到达，前端这里只收到 final 事件；
    // 若沿用 `!State.streamBuffer` 判断，会被执行过程中残留的缓冲（如
    // "🚀 正在为你规划并执行"）挡住，导致"任务完成了但前端看不到总结回复"。
    // 故 summarizer 的 final 必须无条件覆盖显示。
    const _isSummarizer = evt.node === "summarizer";
    // 修复：只有 summarizer 或有现有气泡时才创建/复用气泡
    // 避免 chatbot 节点结束后、planner 节点开始前出现多余的加载中气泡。
    // 但"简洁模式"下没有"🧠 模型调用"气泡承载输出（它被折叠进过程摘要行），
    // 所以 final 必须无条件渲染正文，否则又变成"只剩摘要行、回答不见了"。
    const _needFinalBubble = _isSummarizer || !!State.currentAssistantEl
      || State.displayMode === "compact";
    if (evt.text && _needFinalBubble) {
      // summarizer 是本轮的最终交付答复：**强制新建一个干净气泡**，不复用执行期间的残留气泡。
      // 原因：执行期间 State.currentAssistantEl 可能指向一个非底部、且已被 endStreaming
      // 处理过的气泡；复用它会让总结渲染到页面中部（需滚动才可见），
      // 表现为"任务跑完了却没有输出"。
      const _freshBubble = _isSummarizer || !State.currentAssistantEl;
      if (_freshBubble) {
        State.currentAssistantEl = addAssistantBubble("");
      }
      // 用其最终文本取代气泡内容，展示答复
      State.streamBuffer = evt.text;
      // 干净气泡里只有打字动画，没有流式文本节点：这里现场建一个，
      // 否则 endStreaming 找不到 #stream-text，正文写不进去（气泡会一直空着）。
      if (_freshBubble) {
        const _host = State.currentAssistantEl;
        const _box = _host && $(".msg-text", _host);
        if (_box) {
          const _old = $("#stream-text", _box);
          if (_old) _old.remove();
          _box.querySelectorAll(":scope > .typing-dots").forEach((n) => n.remove());
          const _tn = document.createElement("div");
          _tn.id = "stream-text";
          _box.prepend(_tn);
        }
      }
      endStreaming();
    }
    // 内容已完整送达：立即复位"工具名 / 运行中"指示。
    // （后端随后还会做记忆摘要/git 快照等收尾，done 事件会晚到，
    //   若等 done 才复位，内容输出完后命令栏会继续挂着工具名一段时间。）
    setLiveStatusIdle();
    setLiveBadge(false);
  } else if (step === "done") {
    endStreaming();
    setLiveBadge(false);
    setLiveStatusIdle();
    State.currentAssistantEl = null;  // 真正的流结束：清空，下次新消息时建新气泡
  } else if (step === "error") {
    endStreaming();
    setLiveBadge(false);
    setLiveStatusIdle();
    addErrorBubble(evt.message || "未知错误");
    State.currentAssistantEl = null;
  } else if (step === "dag_push") {
    // 规划开始时：用后端传来的节点结构初始化待办列表（替代原 DAG 图）
    if (Array.isArray((evt.dag || {}).edges)) window._currentEdges = evt.dag.edges;
    updateTodoFromNodes((evt.dag || {}).nodes || [], window._currentEdges);
  } else if (step === "node_status") {
    // 执行中：用全量节点快照刷新待办状态（动态变化）；无全量快照时用单节点状态更新
    if (evt.nodes && evt.nodes.length) {
      updateTodoFromNodes(evt.nodes, window._currentEdges);
    } else if (evt.node_id) {
      State.todos = (State.todos || []).map((t) => {
        if (String(t.id) === String(evt.node_id)) {
          let status = "todo";
          if (evt.status === "success") status = "done";
          else if (evt.status === "running") status = "doing";
          return Object.assign({}, t, { status, rawStatus: evt.status });
        }
        return t;
      });
      renderTodoPanel();
    }
  } else if (step === "replan") {
    // 局部重规划提示：把待办面板重新展开，让用户看到新拆的子任务
    State.todosExpanded = true;
    renderTodoPanel();
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
        const parsed = JSON.parse(line.slice(6));
        // 诊断开关：控制台执行 window.__diagSSE = true 后开启
        if (window.__diagSSE) {
          const _t = (parsed.text || "").slice(0, 40).replace(/\n/g, "\\n");
          const _a = (parsed.args || "").slice(0, 30);
          console.log("[SSE]", parsed.step, parsed.name || "", parsed.phase || "", parsed.node || "", "t='" + _t + "'", _a ? "args=" + _a : "");
        }
        onEvent(parsed);
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
    // 新会话的欢迎页必须先清掉：否则它会和这一轮对话叠在一起
    // （表现为"切了显示模式却没重绘、切会话才正常"）。
    // 顺手用首条消息当会话标题，省得顶栏一直挂着"新对话"。
    const _inner = messagesInner();
    if ($(".welcome", _inner)) {
      _inner.innerHTML = "";
      updateChatTitle(text);
    }
    addUserBubble(text);
    $("#chatInput").value = "";
    autosizeInput();
    hideCmdMenu();
  } else {
    // resume 模式：复用当前 assistant 气泡继续流式
    if (!State.currentAssistantEl) State.currentAssistantEl = addAssistantBubble("");
    State.currentAssistantEl.classList.add("streaming");
    // resume 路径：addAssistantBubble 默认带 typing-dots，需移除并建 #stream-text
    const _box = $(".msg-text", State.currentAssistantEl);
    if (_box && !$("#stream-text", State.currentAssistantEl)) {
      _box.querySelectorAll(":scope > :not(.tool-card):not(.approval-card):not(.proc-text)").forEach((n) => n.remove());
      const _tn = document.createElement("div");
      _tn.id = "stream-text";
      _box.prepend(_tn);
    }
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
    State.currentAssistantEl = null;  // 异常也清空，避免下次 new 消息时复用挂起气泡
    setLiveStatusIdle();
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
  setLiveStatusIdle();
  State.currentAssistantEl = null;  // 停止时清空，下次新消息时建新气泡
  State.streaming = false;
  updateComposer();
}

/* ==================== 历史消息加载 ==================== */
async function loadHistory(tid) {
  const inner = messagesInner();
  inner.innerHTML = "";
  closeProcGroup();  // 清空消息流时丢弃"执行过程"聚合组引用，避免指向已移除的 DOM
  resetTodoPanel();
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

  // 去重：同 thread 内某条 assistant 文本若已被对应 llm_call 事件承载（前端流式期间
  // 已经实时渲染 + llm_call 气泡的"📤 模型输出"折叠面板显示），切会话回放时不再
  // 单独画一条 Agnes 气泡——避免"同一回复出现两次"。找不到配对的（如旧数据、
  // 一次性内部 LLM 调用未广播）仍正常画 Agnes 气泡。
  // llm_call.meta.output 格式由 _llm_messages_to_text 生成：
  //   "--- ai ---\n{正文}\n[tool_calls] {json}\n"（有 tool_calls 时）
  //   "--- ai ---\n{正文}\n"（纯文本时）
  // 解析时只取中间正文段与 m.content 比较。
  const _llmOutputs = new Set();
  const _extractLlmBody = (raw) => {
    if (!raw) return "";
    const s = String(raw);
    // 去前缀 "--- ai ---\n"
    let i = s.indexOf("--- ai ---");
    let body = i >= 0 ? s.slice(i + "--- ai ---".length).replace(/^\r?\n/, "") : s;
    // 去后缀 "\n[tool_calls] ...\n"（如果存在）
    const j = body.indexOf("\n[tool_calls] ");
    if (j >= 0) body = body.slice(0, j);
    return body.trim();
  };
  for (const m of msgs) {
    if (m && m.kind === "llm_call" && m.meta) {
      const _out = _extractLlmBody(m.meta.output || "");
      if (_out) _llmOutputs.add(_out);
    }
  }
  const _isDupOfLlmCall = (content) => {
    if (!content) return false;
    const t = String(content).trim();
    if (!t) return false;
    if (_llmOutputs.has(t)) return true;
    // 容忍前后空白/换行差异：归一化后再比一次
    const norm = t.replace(/\s+/g, " ");
    for (const out of _llmOutputs) {
      if (out.replace(/\s+/g, " ") === norm) return true;
    }
    return false;
  };

  for (const m of msgs) {
    // 事件气泡（持久化回放）：node / llm_call / tool_call / thought / approval
    if (m.kind && m.kind !== "chat" && m.role === "event") {
      const meta = m.meta || {};
      if (m.kind === "node_start" || m.kind === "node_end") {
        renderNodeEvent({ name: meta.node || m.content || "", phase: m.kind === "node_start" ? "start" : "end" }, m.timestamp);
      } else if (m.kind === "llm_call") {
        renderLlmCall(meta, m.timestamp);
      } else if (m.kind === "tool_call") {
        renderToolEvent({
          name: meta.name || m.content || "",
          node: meta.node || "",
          params: meta.params,
          args: meta.params,
          result: meta.result,
          output_preview: meta.result,
        }, m.timestamp);
      } else if (m.kind === "thought") {
        renderThoughtEvent(meta, m.timestamp);
      } else if (m.kind === "approval") {
        renderHistoryApproval(m, meta);
      } else {
        renderHistoryRawEvent(m, meta);
      }
      assistantWrap = null;
      continue;
    }
    if (m.role === "user") {
      closeProcGroup();  // 用户消息是一轮过程的分界：后续过程事件另起一组
      const wrap = document.createElement("div");
      wrap.className = "msg user";
      wrap.innerHTML = `<div class="msg-body"><div class="msg-text">${renderMarkdown(m.content)}</div></div>`;
      inner.appendChild(wrap);
      assistantWrap = null;
    } else if (m.role === "assistant") {
      // 跳过：以"🚀 正在为你规划并执行"开头的消息是触发规划时的过渡文案，
      // 在流式期间已经实时显示过，不需要在历史中重复显示。
      const _isPlanningPrompt = /^🚀\s*正在为你规划并执行/.test(m.content || "");
      if (_isPlanningPrompt) {
        assistantWrap = null;
        continue;
      }
      // 跳过：同 thread 已有 llm_call 事件承载同一段文本（与前端流式播放期间显示的
      // "🧠 模型调用 → 📤 模型输出"内容一致），不重复画 Agnes 气泡。
      // 但"简洁模式"下 llm_call 已被折叠进过程摘要行，用户看不到模型输出——
      // 此时必须让正文气泡照常显示，否则会出现"对话里只剩摘要行、回复全没了"。
      if (State.displayMode !== "compact" && _isDupOfLlmCall(m.content)) {
        // tool_calls 仍需维护（供后续 tool 消息填充工具卡片）——消息顺序里 tool 消息
        // 紧跟此 assistant 之后，必须让 pendingToolIds 准备好。
        const hasCalls = Array.isArray(m.tool_calls) && m.tool_calls.length;
        if (hasCalls) {
          pendingToolCalls = m.tool_calls.slice();
          pendingToolIds = {};
          m.tool_calls.forEach((tc) => {
            pendingToolIds[tc.id] = { name: tc.name || "tool", args: tc.arguments || tc.args || "", done: false };
          });
        }
        // assistantWrap 维持上一次非跳过的 wrapper（用于 tool 结果回填），或新建空 wrapper
        // 兼容"该 assistant 后紧跟 tool 消息"的情况
        assistantWrap = assistantWrap || (() => { const w = document.createElement("div"); w.className = "msg assistant"; inner.appendChild(w); return w; })();
        continue;
      }
      const content = m.content || "";
      const hasCalls = Array.isArray(m.tool_calls) && m.tool_calls.length;
      // 简洁模式下工具卡由"过程摘要行"承载，正文气泡里不再重复贴
      const showToolCards = hasCalls && State.displayMode !== "compact";
      // 工具轮的 assistant 常常只有 tool_calls、没有正文：这种"空气泡"不占位，
      // 也不能当作一轮过程的分界（否则会把一轮切成好几条摘要行）。
      if (!content.trim() && !showToolCards) continue;
      const wrap = document.createElement("div");
      wrap.className = "msg assistant";
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
      // 正文只登记为本轮锚点，不再切分过程分组：本轮后续事件仍归同一条摘要行，
      // 且该摘要行始终位于这条回答之上（"输出在最下面"）
      if (content.trim()) noteTurnTextEl(wrap);
      // 收集 tool_calls（供后续 tool 消息回填工具卡）
      if (hasCalls) {
        pendingToolCalls = m.tool_calls.slice();
        pendingToolIds = {};
        m.tool_calls.forEach((tc) => {
          pendingToolIds[tc.id] = { name: tc.name || "tool", args: tc.arguments || tc.args || "", done: false };
        });
        if (showToolCards) {
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
  State.renderedThreads = visible; // 供"全选"使用

  if (!visible.length) {
    threadListEl.innerHTML = `<div class="thread-empty">${threads.length ? "没有匹配的会话" : "还没有会话，点击「新建会话」开始"}</div>`;
    return;
  }

  visible.forEach((t) => {
    const item = document.createElement("div");
    item.className = "thread-item" + (t.thread_id === State.threadId ? " active" : "");
    const last = t.last ? fmtAgo(t.last) : "从未对话";
    // 标题显示该会话的最后一条用户消息（不显示裸 thread_id）
    const title = truncate((t.last_user_msg || "").trim() || "（无消息）", 24);
    const checked = State.bulkMode && State.bulkSelected.has(t.thread_id);
    item.innerHTML = `
      ${State.bulkMode ? `<input type="checkbox" class="t-check"${checked ? " checked" : ""}>` : ""}
      <div class="t-main">
        <div class="t-title">${escapeHtml(title)}</div>
        <div class="t-sub">${t.count} 条消息 · ${last}</div>
      </div>
      ${State.bulkMode ? "" : `<button class="t-del" title="删除会话">×</button>`}`;
    item.addEventListener("click", (e) => {
      if (e.target.classList.contains("t-del") || e.target.classList.contains("t-check")) return;
      if (State.bulkMode) {
        const cb = $(".t-check", item);
        cb.checked = !cb.checked;
        if (cb.checked) State.bulkSelected.add(t.thread_id); else State.bulkSelected.delete(t.thread_id);
        updateBulkBar();
        return;
      }
      selectThread(t.thread_id);
    });
    const del = $(".t-del", item);
    if (del) del.addEventListener("click", (e) => {
      e.stopPropagation();
      if (confirm("确定删除该会话？此操作不可恢复。")) {
        deleteThread(t.thread_id);
      }
    });
    const cb = $(".t-check", item);
    if (cb) cb.addEventListener("change", () => {
      if (cb.checked) State.bulkSelected.add(t.thread_id); else State.bulkSelected.delete(t.thread_id);
      updateBulkBar();
    });
    threadListEl.appendChild(item);
  });
  updateBulkBar();
}

/* ==================== 会话批量删除 ==================== */
function renderThreadToolbar() {
  const bar = $("#threadToolbar");
  if (!bar) return;
  if (!State.bulkMode) {
    bar.innerHTML = `<button id="btnBulk" class="t-btn">🗑 批量删除</button>`;
    const b = $("#btnBulk", bar);
    if (b) b.addEventListener("click", enterBulkMode);
  } else {
    const n = State.bulkSelected.size;
    bar.innerHTML = `
      <span class="tb-count">已选 ${n}</span>
      <button id="bulkAll" class="t-btn">全选</button>
      <button id="bulkDel" class="t-btn danger"${n ? "" : " disabled"}>删除</button>
      <button id="bulkCancel" class="t-btn">取消</button>`;
    $("#bulkAll", bar).addEventListener("click", () => {
      const all = (State.renderedThreads || []).map((t) => t.thread_id);
      if (State.bulkSelected.size === all.length && all.length) State.bulkSelected.clear();
      else all.forEach((id) => State.bulkSelected.add(id));
      updateBulkBar();
      loadThreads(); // 重渲染勾选状态
    });
    $("#bulkDel", bar).addEventListener("click", bulkDeleteThreads);
    $("#bulkCancel", bar).addEventListener("click", exitBulkMode);
  }
}

function updateBulkBar() {
  const bar = $("#threadToolbar");
  if (!bar) return;
  const n = State.bulkSelected.size;
  const count = $(".tb-count", bar);
  if (count) count.textContent = `已选 ${n}`;
  const del = $("#bulkDel", bar);
  if (del) del.disabled = !n;
  // 同步刷新各 checkbox 勾选态
  $$(".t-check", threadListEl).forEach((cb) => {
    const item = cb.closest(".thread-item");
    const idx = Array.from(threadListEl.children).indexOf(item);
    const t = (State.renderedThreads || [])[idx];
    if (t) cb.checked = State.bulkSelected.has(t.thread_id);
  });
}

function enterBulkMode() {
  State.bulkMode = true;
  State.bulkSelected = new Set();
  renderThreadToolbar();
  loadThreads(); // 重渲染列表，每项显示 checkbox
}

function exitBulkMode() {
  State.bulkMode = false;
  State.bulkSelected = new Set();
  renderThreadToolbar();
  loadThreads();
}

async function bulkDeleteThreads() {
  const ids = [...State.bulkSelected];
  if (!ids.length) return;
  if (!confirm(`确定删除选中的 ${ids.length} 个会话？此操作不可恢复。`)) return;
  try {
    const r = await apiPost("/api/threads/delete", { thread_ids: ids });
    const deletedIds = new Set((r.deleted || []).map((d) => d.thread_id));
    if (State.threadId && deletedIds.has(State.threadId)) {
      State.threadId = null;
      messagesInner().innerHTML = "";
      renderWelcome();
      updateChatTitle("新对话");
    }
    exitBulkMode();
    loadThreads();
    toast(`已删除 ${r.total != null ? r.total : ids.length} 个会话`, "success");
  } catch (e) {
    toast("批量删除失败: " + e.message, "error");
  }
}

async function selectThread(tid) {
  if (isMobile()) closeSidebar();  // 手机端选择会话后自动收起边栏抽屉
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
    closeProcGroup();  // 清空消息流时丢弃过程聚合组引用
    resetTodoPanel();
    renderWelcome();
    updateChatTitle("新对话");
    refreshTopbar();
    loadThreads();
    refreshApprovalMode(); // /new 会切换 thread_id，重新同步审批模式按钮（防按钮高亮与实际模式不一致）
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
      refreshApprovalMode(); // 同上：切换会话后同步审批模式按钮
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
    // 统一走 sendFromComposer：带附件一起发送（含 streaming/空输入/斜杠命令处理）
    sendFromComposer();
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
  const atts = State.pendingAttachments || [];
  if (State.streaming) return;
  if (!val && !atts.length) return;
  if (val.startsWith("/") && !atts.length) { runCommand(val); return; }
  // 有附件：把附件 markdown 拼进消息（图片渲染 + Agent 可读 uploads/ 路径），随本次输入一起发送
  let text = val;
  if (atts.length) {
    const attText = atts.map((a) => a.isImg
      ? `![${a.name}](${a.path})`
      : `[附件：${a.name}](${a.path})`).join("\n");
    text = attText + (val ? "\n\n" + val : "");
    State.pendingAttachments = [];
    renderAttachBar();
  }
  sendMessage(text);
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
    // 与对话区右侧保持一致：直接复用对话流的气泡渲染（同一套 addEventBubble 样式），
    // host 指向本抽屉容器即可，不再自己拼一套 trace 专属 HTML。
    el.innerHTML = drawerSection(`追踪（${events.length} 条）`, `<div id="traceStream" class="trace-stream"></div>`) +
      `<div class="d-row"><button class="d-btn danger" id="traceClear">清空追踪</button></div>`;
    const host = $("#traceStream", el);
    for (const evt of events) {
      const d = evt.data || {};
      const etype = evt.type || "";
      try {
        if (etype === "node_start") {
          renderNodeEvent({ name: d.node, phase: "start" }, evt.ts, host);
        } else if (etype === "node_end") {
          renderNodeEvent({ name: d.node, phase: "end", result: d.result }, evt.ts, host);
        } else if (etype === "llm_call") {
          renderLlmCall(d, evt.ts, host);
        } else if (etype === "tool_call") {
          renderToolEvent(d, evt.ts, host);
        } else if (etype === "thought") {
          renderThoughtEvent(d, evt.ts, host);
        } else if (etype === "error") {
          addEventBubble("node", "❌", "错误", "",
            `<pre class="evt-pre">${escapeHtml(String(d.message || ""))}</pre>`, evt.ts, host);
        }
      } catch (e) { /* 单条渲染失败不影响整体 */ }
    }
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
  // 凡带 thread_id 列的表（messages / history_summaries / dag_plans / command_history）
  // 默认按当前会话预填过滤，省去每次手输；user_profile / user_preferences / dag_nodes /
  // dag_edges / semantic_cache 无 thread_id 列，预填反而会让查询报错，故仍展示全量。
  const mdbTableMeta = new Map(tables.map((t) => [t.name, t]));
  const applyDefaultWhere = () => {
    const cols = (mdbTableMeta.get(tableSel.value) || {}).columns || [];
    whereInp.value = cols.includes("thread_id") && State.threadId
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

/* ---- 技能 ---- */
async function renderSkillsTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/skills");
    const skills = data.skills || [];
    if (!skills.length) {
      el.innerHTML = drawerSection("技能（app/skills）", `<div class="d-empty">暂无技能。可让 Agent 用 search_skillhub 搜索下载，或手工把 SKILL.md 放到 app/skills/ 下。</div>`);
      return;
    }
    el.innerHTML = drawerSection(`技能（${skills.length}）`, skills.map((s) => `
      <div class="tool-item skill-item" data-name="${escapeHtml(s.name)}">
        <div class="t-name">✨ ${escapeHtml(s.name)}</div>
        <div class="t-desc">${escapeHtml(s.description || "")}</div>
        ${(s.triggers || []).length ? `<div class="t-desc" style="color:var(--text-faint)">触发词: ${escapeHtml(s.triggers.slice(0, 8).join("、"))}</div>` : ""}
        <div class="t-desc" style="font-family:var(--mono);color:var(--text-faint)">${escapeHtml(s.path || "")}</div>
      </div>`).join(""));
    $$(".skill-item", el).forEach((item) => {
      item.addEventListener("click", () => renderSkillDetail(el, item.dataset.name));
    });
  } catch (e) { el.innerHTML = drawerErr(e); }
}

async function renderSkillDetail(el, name) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const s = await apiGet(`/api/skills/${encodeURIComponent(name)}`);
    const head = `
      <div class="t-desc">${escapeHtml(s.description || "")}</div>
      ${(s.triggers || []).length ? `<div class="t-desc">触发词: ${escapeHtml(s.triggers.join("、"))}</div>` : ""}
      <div class="t-desc" style="color:var(--text-faint)">位置: ${escapeHtml(s.path || "")}</div>`;
    el.innerHTML = `<div class="skill-back"><button class="btn-ghost" id="btnSkillBack">← 返回技能列表</button></div>`
      + drawerSection(`技能: ${escapeHtml(s.name)}`, head)
      + drawerSection("SKILL.md 原文", `<pre class="d-pre">${escapeHtml(s.raw || "")}</pre>`);
    $("#btnSkillBack").addEventListener("click", () => renderSkillsTab(el));
  } catch (e) { el.innerHTML = drawerErr(e); }
}

/* ---- 定时任务 ---- */
async function renderSchedTab(el) {
  el.innerHTML = `<div class="d-empty">加载中…</div>`;
  try {
    const data = await apiGet("/api/scheduler");
    const tasks = data.tasks || [];
    const schedDesc = (t) => {
      if (t.schedule_type === "cron") return `cron ${t.cron_expr || "(空)"}`;
      if (t.schedule_type === "interval") return `每 ${t.interval_seconds}s（旧格式）`;
      return `每日 ${t.daily_time}（旧格式）`;
    };
    const list = tasks.length ? tasks.map((t) => `
      <div class="sched-item">
        <div class="s-head">
          <span class="s-name">${escapeHtml(t.name)}</span>
          <span class="s-badge ${t.enabled ? "on" : "off"}">${t.enabled ? "运行中" : "已停用"}</span>
          <span style="flex:1"></span>
          <button class="d-btn sm" data-act="toggle" data-id="${t.id}">${t.enabled ? "停用" : "启用"}</button>
          <button class="d-btn sm danger" data-act="del" data-id="${t.id}">删除</button>
        </div>
        <div class="s-meta">${schedDesc(t)} · ${t.thread_id || "默认线程"}</div>
        <div class="s-result">${escapeHtml(truncate(t.prompt, 120))}</div>
        ${t.last_result ? `<div class="s-result" style="color:var(--text-faint)">上次: ${escapeHtml(truncate(t.last_result, 140))}</div>` : ""}
      </div>`).join("") : `<div class="d-empty">暂无定时任务</div>`;

    el.innerHTML = `
      <div class="d-card">
        <h4 style="margin-bottom:8px">新建定时任务</h4>
        <div class="d-row"><label>任务名</label><input class="d-input" id="schedName" placeholder="如：每日日报"></div>
        <div class="d-row"><label>Cron 表达式</label><input class="d-input" id="schedCron" placeholder="*/5 * * * *（分 时 日 月 周）"></div>
        <div class="d-row" style="font-size:11px;color:var(--text-faint)">示例：<code>*/5 * * * *</code> 每5分钟 · <code>0 9 * * *</code> 每天9点 · <code>0 9 * * 1-5</code> 工作日9点</div>
        <div class="d-row"><label>提示词</label><textarea class="d-input" id="schedPrompt" rows="2" placeholder="任务内容…"></textarea></div>
        <div class="d-row"><button class="d-btn primary" id="schedAdd">创建</button></div>
      </div>
      ${drawerSection("任务列表", list)}`;

    $("#schedAdd", el).addEventListener("click", async () => {
      const name = $("#schedName", el).value.trim();
      const prompt = $("#schedPrompt", el).value.trim();
      const cron = $("#schedCron", el).value.trim();
      if (!name || !prompt) { toast("任务名和提示词必填", "error"); return; }
      if (!cron || cron.split(/\s+/).length !== 5) { toast("请填写 5 段 cron 表达式（分 时 日 月 周）", "error"); return; }
      const payload = { name, prompt, schedule_type: "cron", cron_expr: cron };
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

/* ---- 对话显示模式（详细 / 简洁） ---- */
async function renderDisplayTab(el) {
  const cur = State.displayMode || "verbose";
  el.innerHTML = drawerSection("对话显示模式", `
    <div class="mode-list">
      ${DISPLAY_MODES.map((m) => `
        <button class="mode-card ${m.id === cur ? "active" : ""}" data-mode="${m.id}">
          <span class="mode-ico">${m.icon}</span>
          <span class="mode-text"><b>${m.name}</b><span>${escapeHtml(m.desc)}</span></span>
          <span class="mode-check">${m.id === cur ? "✓" : ""}</span>
        </button>`).join("")}
    </div>
    <div class="d-empty" style="margin-top:8px">
      切换后当前会话立即按新模式重绘；若回复正在生成，则在本次回复结束后生效。选择会被记住。
    </div>`);
  $$(".mode-card", el).forEach((btn) => {
    btn.addEventListener("click", () => {
      setDisplayMode(btn.dataset.mode);
      renderDisplayTab(el);
    });
  });
}

/* ---- 抽屉 tab 注册表 ---- */
const DRAWER_LOADERS = {
  state: renderStateTab,
  display: renderDisplayTab,
  prompt: renderPromptTab,
  trace: renderTraceTab,
  memory: renderMemoryTab,
  memorydb: renderMemoryDBTab,
  tools: renderToolsTab,
  skills: renderSkillsTab,
  sched: renderSchedTab,
  models: renderModelsTab,
  deliv: renderDelivTab,
};

const DRAWER_TABS = [
  { id: "state", label: "State", icon: "📊" },
  { id: "display", label: "显示", icon: "🖥️" },
  { id: "prompt", label: "提示词", icon: "📄" },
  { id: "trace", label: "追踪", icon: "🧭" },
  { id: "memory", label: "记忆", icon: "🧠" },
  { id: "memorydb", label: "Memory DB", icon: "🗄️" },
  { id: "tools", label: "工具", icon: "🔧" },
  { id: "skills", label: "技能", icon: "✨" },
  { id: "sched", label: "定时任务", icon: "🗓️" },
  { id: "models", label: "模型", icon: "⚙️" },
  // 交付物 tab 已移到主页顶栏（#btnDeliverables），点击时仍通过 activateTab("deliv") 渲染
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
  // 后端 SSE 数据结构：{type, data, timestamp, thread_id}——subtasks 嵌套在 data 里
  const eType = evt.type;
  const eData = evt.data || {};
  if (eType === "log") {
    if (State.drawerTab === "logs") renderLogsTab(drawerBody);
  } else if (eType === "event") {
    if (State.drawerTab === "events") renderEventsTab(drawerBody);
  } else if (eType === "planner" || eType === "executor") {
    // 任务计划/子任务状态变化 → 刷新待办面板。
    // 后端事件负载有两种形态：executor 全量快照用 `nodes`（[{id,status,description,replaces}]），
    // 旧版 subtasks 结构也兼容。统一转成待办列表驱动（替代 DAG 图）。
    if (eData.nodes && Array.isArray(eData.nodes) && eData.nodes.length) {
      // 缓存最新 edges（供 updateTodoFromNodes 做拓扑分层用）
      if (Array.isArray(eData.edges)) window._currentEdges = eData.edges;
      updateTodoFromNodes(eData.nodes, window._currentEdges);
    } else if (eData.subtasks && Array.isArray(eData.subtasks)) {
      const items = eData.subtasks.map((s) => ({
        id: s.id,
        text: s.desc || s.description || String(s.id),
        status: s.status === "done" ? "done" : (s.status === "running" ? "doing" : "todo"),
      }));
      setTodos(items);
    }
  }
}

/* ==================== 事件绑定 ==================== */
function bindEvents() {
  $("#btnNewChat").addEventListener("click", newThread);
  $("#btnSend").addEventListener("click", sendFromComposer);
  $("#btnAttach").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";            // 允许重复选择同一文件
    files.forEach(uploadFileToBar);
  });
  // 直接粘贴文件（如截图 Ctrl+V）到发送栏 → 加入附件条；纯文本粘贴不受影响
  $("#chatInput").addEventListener("paste", (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    const files = [];
    for (const it of items) {
      if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
    }
    if (files.length) {
      e.preventDefault();
      files.forEach(uploadFileToBar);
      toast(files.length + " 个文件已加入附件，输入指令后点发送");
    }
  });
  $("#btnStop").addEventListener("click", stopChat);
  $("#btnToggleTheme").addEventListener("click", cycleTheme);
  $("#btnCollapseSidebar").addEventListener("click", closeSidebar);
  $("#btnSidebar").addEventListener("click", openSidebar);
  $("#sidebarMask").addEventListener("click", closeSidebar);
  // 移动端：键盘弹出/收起及窗口尺寸变化时同步根高度
  window.addEventListener("resize", syncViewportHeight);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", syncViewportHeight);
  $("#threadSearch").addEventListener("input", () => loadThreads());
  $("#btnDrawer").addEventListener("click", openDrawer);
  $("#btnDrawerClose").addEventListener("click", closeDrawer);
  $("#drawerMask").addEventListener("click", closeDrawer);
  // 交付物入口（主页设置按钮左边）：打开抽屉并激活交付物页
  $("#btnDeliverables").addEventListener("click", () => {
    openDrawer();
    activateTab("deliv");
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
      else if ($("#approvalModeBar").classList.contains("open")) $("#approvalModeBar").classList.remove("open");
      else if ($("#app").classList.contains("side-open")) closeSidebar();
      else hideCmdMenu();
    }
  });
}

/* ==================== 初始化 ==================== */
async function init() {
  // 登录校验：未登录先显示登录界面，登录成功后再走主流程
  if (!(await checkAuth())) {
    showLogin();
    return;
  }

  // 主题
  applyTheme();
  // 对话显示模式（必须在首次渲染历史前恢复，否则历史会按默认模式画一遍）
  initDisplayMode();
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
  initApprovalMode();
  renderThreadToolbar();
  chatInput.focus();
}

document.addEventListener("DOMContentLoaded", () => {
  bindAuthEvents();
  init();
});
