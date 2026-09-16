/* ============================================================================
   RAG 管理台（个人助手版知识库管理）
   ----------------------------------------------------------------------------
   以「会话(thread)=知识库」为模型：lightrag_storage 下每个 thread 目录即一个知识库。
   页面由 hash 路由驱动：#/rag-admin/kb | /kb/create | /kb/<tid>/<view>
   其中 view ∈ documents | chunks | config | test。
   所有数据都走 /api/kb/*（受登录中间件保护），读写由 app.memory.ligraphrag_adapter 桥接。
   依赖 app.js 的全局工具：$ / $$ / apiGet / apiPost / escapeHtml / truncate / fmtAgo /
   toast / drawerSection / drawerErr / State。
   ============================================================================ */

/* ---------- 路由 ---------- */
const RAG_ICONS = {
  kb: `<svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
  doc: `<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,
  chunk: `<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>`,
  config: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.2.63.77 1.09 1.51 1H21a2 2 0 1 1 0 4h-.09c-.74.09-1.31.55-1.51 1.18z"/></svg>`,
  test: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
};

function ragParseHash() {
  const h = (location.hash || "").replace(/^#!?/, "");
  if (!h.startsWith("/rag-admin/")) return null;
  const parts = h.split("/").filter(Boolean); // ["rag-admin", ...]
  parts.shift();
  if (!parts.length || parts[0] === "dashboard") return { section: "kb" };
  if (parts[0] === "kb") {
    if (!parts[1]) return { section: "kb" };
    if (parts[1] === "create") return { section: "create" };
    const view = parts[2] || "documents";
    return { section: "detail", tid: parts[1], view: ["documents", "chunks", "config", "test"].includes(view) ? view : "documents" };
  }
  return { section: "kb" };
}

function ragNavHighlight() {
  $$(".rag-nav-item").forEach((a) => a.classList.toggle("active", a.dataset.route === "kb"));
}

async function renderRagRoute() {
  if (State.drawerTab !== "rag") return;
  const content = $("#ragContent");
  if (!content) return;
  if (RAG.poll) { clearInterval(RAG.poll); RAG.poll = null; }
  content.innerHTML = `<div class="d-empty">加载中…</div>`;
  ragNavHighlight();
  const r = ragParseHash() || { section: "kb" };
  try {
    if (r.section === "kb") await ragKbList(content);
    else if (r.section === "create") await ragKbCreate(content);
    else if (r.section === "detail") await ragKbDetail(content, r);
  } catch (e) {
    content.innerHTML = drawerErr(e);
  }
}

const RAG = { poll: null };

async function renderRagTab(el) {
  el.innerHTML = `
    <div class="rag">
      <nav class="rag-nav">
        <div class="rag-nav-brand"><i></i> RAG</div>
        <a class="rag-nav-item" data-route="kb" href="#/rag-admin/kb"><span class="rag-nav-ico">${RAG_ICONS.kb}</span>知识库管理</a>
        <a class="rag-nav-new" href="#/rag-admin/kb/create">＋ 新建知识库</a>
        <div class="rag-nav-hint">每个会话（thread）自动沉淀为独立知识库，含文档、切片、实体关系与向量索引。</div>
      </nav>
      <section class="rag-content" id="ragContent">
        <div class="d-empty">加载中…</div>
      </section>
    </div>`;
  if (!(location.hash || "").startsWith("#/rag-admin/")) {
    location.hash = "#/rag-admin/kb";
  } else {
    renderRagRoute();
  }
}

window.addEventListener("hashchange", renderRagRoute);

/* ---------- 通用小组件 ---------- */

function ragStatusChip(status) {
  return `<span class="kc-status ${escapeHtml(String(status).toLowerCase())}">${escapeHtml(status)}</span>`;
}

function ragModal(title, bodyHtml, { wide = false } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "rag-modal-mask";
  wrap.innerHTML = `<div class="rag-modal${wide ? " wide" : ""}">
      <div class="rag-modal-hd"><h4>${escapeHtml(title)}</h4><button class="icon-btn rg-modal-x">✕</button></div>
      <div class="rag-modal-bd">${bodyHtml}</div>
    </div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".rg-modal-x").addEventListener("click", close);
  wrap.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  return { wrap, close, bd: wrap.querySelector(".rag-modal-bd") };
}

/* ---------- 页面：知识库列表 ---------- */

async function ragKbList(content) {
  const r = await apiGet("/api/kb/list");
  const kbs = r.knowledge_bases || [];
  content.innerHTML = `
    <div class="rg-toolbar">
      <div class="d-empty" style="margin:0">共 ${kbs.length} 个知识库</div>
      <div style="flex:1"></div>
      <button class="d-btn primary" onclick="location.hash='#/rag-admin/kb/create'">＋ 新建知识库</button>
    </div>
    ${drawerSection("知识库列表", kbs.length ? `
      <table class="d-table"><thead><tr>
        <th>名称</th><th>文档</th><th>切片</th><th>实体</th><th>关系</th><th>解析状态</th><th>更新时间</th><th>操作</th>
      </tr></thead><tbody>` + kbs.map((k) => `
        <tr>
          <td><b>${escapeHtml(k.name)}</b><div class="d-empty" style="padding:0">${escapeHtml(truncate(k.description || k.thread_id, 46))}</div></td>
          <td>${k.doc_count}</td><td>${k.chunk_count}</td><td>${k.entity_count}</td><td>${k.relation_count}</td>
          <td>${Object.entries(k.status_counts || {}).map(([s, n]) => `${escapeHtml(s)} ${n}`).join(" / ") || "—"}</td>
          <td>${fmtAgo(k.updated_at)}</td>
          <td class="d-row" style="gap:6px">
            <button class="d-btn d-btn-sm" onclick="location.hash='#/rag-admin/kb/${encodeURIComponent(k.thread_id)}/documents'">打开</button>
            <button class="d-btn d-btn-sm danger" data-del="${encodeURIComponent(k.thread_id)}">删除</button>
          </td>
        </tr>`).join("") + `</tbody></table>` : `
      <div class="d-empty">还没有知识库。点右上角「＋ 新建知识库」，或直接去聊天——每个会话会自动沉淀出独立知识库。</div>`)}`;
  $$("button[data-del]", content).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("删除整个知识库？将清除该会话的文档、切片、实体（含 Neo4j 图）与向量索引，不可恢复。")) return;
    try {
      const r2 = await apiPost("/api/kb/delete_kb", { thread_id: decodeURIComponent(b.dataset.del) });
      if (!r2.ok) throw new Error(r2.error || "删除失败");
      toast("已删除知识库", "success");
      await ragKbList(content);
    } catch (e) { toast("删除失败: " + e.message, "error"); }
  }));
}

/* ---------- 页面：新建知识库 ---------- */

async function ragKbCreate(content) {
  content.innerHTML = drawerSection("新建知识库", `
    <form id="kbCreateForm" style="display:flex;flex-direction:column;gap:10px;max-width:480px">
      <label>知识库名称 <input class="d-input" id="kbN" placeholder="如：产品手册、论文笔记" required></label>
      <label>描述 <textarea class="d-input" id="kbD" rows="2" placeholder="可选：这个知识库装什么"></textarea></label>
      <div class="d-row" style="gap:8px">
        <button class="d-btn" type="submit">创建</button>
        <button class="d-btn" type="button" onclick="location.hash='#/rag-admin/kb'">取消</button>
      </div>
      <div class="d-empty">创建后会自动生成一个独立的知识库空间（thread），可立刻进入上传文档/喂入文本。</div>
    </form>`);
  $("#kbCreateForm", content).addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    btn.disabled = true;
    try {
      const r = await apiPost("/api/kb/create", { name: $("#kbN", content).value, description: $("#kbD", content).value });
      if (!r.ok) throw new Error(r.error || "创建失败");
      toast("创建成功", "success");
      location.hash = `#/rag-admin/kb/${encodeURIComponent(r.thread_id)}/documents`;
    } catch (err) { toast("创建失败: " + err.message, "error"); btn.disabled = false; }
  });
}

/* ---------- 页面：知识库详情（壳 + 子页） ---------- */

const RAG_DETAIL_VIEWS = [
  ["documents", "文档", RAG_ICONS.doc],
  ["chunks", "切片", RAG_ICONS.chunk],
  ["config", "检索配置", RAG_ICONS.config],
  ["test", "检索调试", RAG_ICONS.test],
];

async function ragKbDetail(content, route) {
  const tid = decodeURIComponent(route.tid);
  const st = await apiGet(`/api/kb/status?thread_id=${encodeURIComponent(tid)}`);
  const base = `#/rag-admin/kb/${encodeURIComponent(tid)}`;
  content.innerHTML = `
    <div class="d-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px">
      <div class="d-empty" style="margin:0">
        <a href="#/rag-admin/kb" style="color:var(--accent,#58a6ff)">知识库</a> /
        <b>${escapeHtml(st.name || tid.slice(0, 13))}</b>
        <span class="d-empty" style="display:inline;padding:0;margin-left:6px">(${escapeHtml(tid.slice(0, 13))}…)</span>
      </div>
      <div class="d-row" style="gap:6px">
        <span class="kc-badge">向量: ${escapeHtml(st.vector_backend)}</span>
        <span class="kc-badge">图谱: ${escapeHtml(st.graph_backend)}</span>
        <button class="d-btn d-btn-sm" onclick="renderRagRoute()"><span class="rag-nav-ico" style="width:14px;height:14px"><svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></span></button>
      </div>
    </div>
    <div class="rag-subtabs">
      ${RAG_DETAIL_VIEWS.map(([v, label, svg]) =>
        `<a href="${base}/${v}" class="rag-subtab${v === route.view ? " active" : ""}"><span class="rag-nav-ico">${svg}</span>${escapeHtml(label)}</a>`).join("")}
    </div>
    <div id="ragDetailBody"></div>`;
  const body = $("#ragDetailBody", content);
  if (route.view === "documents") await ragDocs(content, tid, body);
  else if (route.view === "chunks") await ragChunks(content, tid, body);
  else if (route.view === "config") await ragConfig(content, tid, body);
  else if (route.view === "test") await ragTest(content, tid, body);
}

/* ---------- 子页：文档 ---------- */

async function ragDocs(content, tid, body) {
  let page = 1;
  let running = false;
  const render = async () => {
    running = true;
    body.innerHTML = `<div class="d-empty">加载中…</div>`;
    const r = await apiGet(`/api/kb/documents?${new URLSearchParams({ thread_id: tid, page, page_size: 15 })}`);
    const docs = r.docs || [];
    const anyBusy = docs.some((d) => ["processing", "pending"].includes(d.status.toLowerCase()));
    body.innerHTML = drawerSection("文档列表", `
      <div class="rg-toolbar">
        <button class="d-btn" id="rgUpFile">📄 上传文件</button>
        <button class="d-btn" id="rgUpBatch">📚 批量上传</button>
        <button class="d-btn" id="rgUpUrl">🔗 导入 URL</button>
        <button class="d-btn danger" id="rgRetryFail" ${docs.some((d) => d.status.toLowerCase() === "failed") ? "" : "disabled"}>⟳ 重试失败</button>
        <input type="file" id="rgFile1" style="display:none" accept=".txt,.md,.json,.csv,.html,.pdf,.docx,.pptx,.xlsx,.xls,.epub,.ipynb">
        <input type="file" id="rgFileN" style="display:none" multiple accept=".txt,.md,.json,.csv,.html,.pdf,.docx,.pptx,.xlsx,.xls,.epub,.ipynb">
      </div>
      <textarea class="d-input" id="rgFeedText" rows="6" placeholder="或直接粘贴文本喂入（自动分块 + 抽取实体关系建图）…"></textarea>
      <div class="rg-toolbar"><button class="d-btn primary" id="rgFeed">＋ 喂入文本</button>
        <div class="d-empty" id="rgBusy" style="display:none">⏳ 正在解析/嵌入/建图（可能 10-60 秒）…</div></div>
      ${docs.length ? `
      <table class="d-table"><thead><tr>
        <th>文件名</th><th>大小</th><th>状态</th><th>切片</th><th>上传时间</th><th>操作</th>
      </tr></thead><tbody>` + docs.map((d) => `
        <tr>
          <td><b>${escapeHtml(truncate(d.file_path || d.title, 40))}</b>${d.error_msg ? `<div class="d-empty" style="color:#e5534b;padding:0;word-break:break-all">${escapeHtml(truncate(d.error_msg, 90))}</div>` : ""}</td>
          <td>${fmtBytes(d.content_length)}</td>
          <td>${ragStatusChip(d.status)}</td>
          <td>${d.chunks_count}</td>
          <td>${fmtAgo(d.created_at)}</td>
          <td class="d-row" style="gap:6px">
            <button class="d-btn d-btn-sm" data-act="preview" data-id="${escapeHtml(d.id)}">预览</button>
            <button class="d-btn d-btn-sm" data-act="rebuild" data-id="${escapeHtml(d.id)}">重新解析</button>
            <button class="d-btn d-btn-sm danger" data-act="del" data-id="${escapeHtml(d.id)}">删除</button>
          </td>
        </tr>`).join("") + `</tbody></table>` : `
      <div class="d-empty">暂无文档。上传文件 / 导入 URL / 粘贴文本即可建立第一条知识。</div>`}`);

    // 分页
    const pages = Math.max(1, Math.ceil(r.total / 15));
    if (pages > 1) {
      const pg = document.createElement("div");
      pg.className = "d-row";
      pg.style.cssText = "gap:8px;margin-top:8px";
      pg.innerHTML = `<span class="d-empty" style="margin:0">${escapeHtml(String(page + "/" + pages))}</span>
        ${page > 1 ? `<button class="d-btn d-btn-sm">上一页</button>` : ""}
        ${page < pages ? `<button class="d-btn d-btn-sm">下一页</button>` : ""}`;
      body.appendChild(pg);
      const [p, n] = $$("button", pg);
      if (p) p.addEventListener("click", () => { page--; render(); });
      if (n) n.addEventListener("click", () => { page++; render(); });
    }

    // 行操作
    $$("button[data-act]", body).forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.id;
      if (b.dataset.act === "del") {
        if (!confirm("删除该文档及其分块/实体/向量？")) return;
        try { const r2 = await apiPost("/api/kb/delete", { thread_id: tid, doc_id: id }); if (!r2.ok) throw new Error(r2.error); toast("已删除", "success"); }
        catch (e) { toast("删除失败: " + e.message, "error"); }
        await render();
      } else if (b.dataset.act === "rebuild") {
        if (!confirm("重新解析该文档（删除后按原内容重喂，向量会重建）？")) return;
        b.textContent = "解析中…"; b.disabled = true;
        try { const r2 = await apiPost("/api/kb/reprocess", { thread_id: tid, doc_id: id }); if (!r2.ok) throw new Error(r2.error); toast("已重新解析", "success"); }
        catch (e) { toast("重新解析失败: " + e.message, "error"); }
        await render();
      } else if (b.dataset.act === "preview") {
        ragPreviewChunks(tid, id);
      }
    }));

    // 喂入文本
    $("#rgFeed", body).addEventListener("click", async () => {
      const txt = $("#rgFeedText", body).value.trim();
      if (!txt) { toast("请先输入内容", "error"); return; }
      toggleBusy(true);
      try { const r2 = await apiPost("/api/kb/ingest", { thread_id: tid, texts: [txt] }); if (!r2.ok) throw new Error(r2.error); toast("已喂入", "success"); $("#rgFeedText", body).value = ""; }
      catch (e) { toast("喂入失败: " + e.message, "error"); }
      toggleBusy(false); await render();
    });
    const toggleBusy = (on) => { const b = $("#rgBusy", body); if (b) b.style.display = on ? "" : "none"; };

    // 上传
    const uploadOne = async (file) => {
      const fd = new FormData(); fd.append("file", file);
      const up = await fetch("/api/upload", { method: "POST", body: fd });
      if (up.status === 401) return onUnauthorized();
      const ud = await up.json().catch(() => ({}));
      if (!up.ok) throw new Error(ud.error || "上传失败");
      return apiPost("/api/kb/ingest_file", { thread_id: tid, path: ud.path });
    };
    $("#rgUpFile", body).addEventListener("click", () => $("#rgFile1", body).click());
    $("#rgUpBatch", body).addEventListener("click", () => $("#rgFileN", body).click());
    $("#rgFile1", body).addEventListener("change", async (e) => {
      const f = e.target.files[0]; if (!f) return;
      toggleBusy(true);
      try { const r2 = await uploadOne(f); if (!r2.ok) throw new Error(r2.error); toast(`「${f.name}」已喂入`, "success"); }
      catch (err) { toast("失败: " + err.message, "error"); }
      toggleBusy(false); e.target.value = ""; await render();
    });
    $("#rgFileN", body).addEventListener("change", async (e) => {
      const files = Array.from(e.target.files || []); if (!files.length) return;
      toggleBusy(true);
      let ok = 0;
      for (const [i, f] of files.entries()) {
        $("#rgBusy", body).textContent = `⏳ 喂入 ${i + 1}/${files.length}：${f.name} …`;
        try { const r2 = await uploadOne(f); if (r2.ok) ok++; } catch (err) { toast(`「${f.name}」失败: ${err.message}`, "error"); }
      }
      toggleBusy(false); e.target.value = "";
      toast(`批量完成：成功 ${ok}/${files.length}`, "success");
      await render();
    });
    $("#rgUpUrl", body).addEventListener("click", async () => {
      const url = prompt("输入要导入的网页 URL（抓取正文后喂入）：", "https://");
      if (!url) return;
      toggleBusy(true);
      try { const r2 = await apiPost("/api/kb/import_url", { thread_id: tid, url }); if (!r2.ok) throw new Error(r2.error); toast("URL 已导入", "success"); }
      catch (e) { toast("导入失败: " + e.message, "error"); }
      toggleBusy(false); await render();
    });
    $("#rgRetryFail", body).addEventListener("click", async () => {
      toggleBusy(true);
      try {
        const r2 = await apiGet(`/api/kb/documents?${new URLSearchParams({ thread_id: tid, page: 1, page_size: 200 })}`);
        const failed = (r2.docs || []).filter((d) => d.status.toLowerCase() === "failed");
        let ok = 0;
        for (const d of failed) { const rr = await apiPost("/api/kb/reprocess", { thread_id: tid, doc_id: d.id }); if (rr.ok) ok++; }
        toast(`已重试 ${ok}/${failed.length} 个失败文档`, "success");
      } catch (e) { toast("重试失败: " + e.message, "error"); }
      toggleBusy(false); await render();
    });

    // 后台轮询：解析中任务自动刷新
    if (anyBusy && !RAG.poll) {
      RAG.poll = setInterval(() => { if (State.drawerTab === "rag" && !running) render(); }, 4000);
    }
    running = false;
  };
  await render();
}

function fmtBytes(n) {
  n = Number(n) || 0;
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

async function ragPreviewChunks(tid, docId) {
  const { wrap, close, bd } = ragModal("切片预览", `<div class="d-empty">加载中…</div>`, { wide: true });
  try {
    const r = await apiGet(`/api/kb/chunks?${new URLSearchParams({ thread_id: tid, doc_id: docId })}`);
    const cs = r.chunks || [];
    bd.innerHTML = cs.length ? cs.map((c) => `
      <div class="kc-chunk">
        <div class="kc-chunk-hd">#${c.order} · ${c.tokens} tokens · ${escapeHtml(c.id)}</div>
        <pre class="d-pre" style="white-space:pre-wrap;max-height:180px;overflow:auto">${escapeHtml(c.content)}</pre>
      </div>`).join("") : `<div class="d-empty">该文档暂无切片。</div>`;
  } catch (e) { bd.innerHTML = drawerErr(e); }
}

/* ---------- 子页：切片 ---------- */

async function ragChunks(content, tid, body) {
  const docs = (await apiGet(`/api/kb/documents?${new URLSearchParams({ thread_id: tid, page: 1, page_size: 200 })}`)).docs || [];
  let selDoc = "";
  const render = async () => {
    body.innerHTML = `<div class="d-empty">加载中…</div>`;
    const opts = `<option value="">全部文档</option>` + docs.map((d) =>
      `<option value="${escapeHtml(d.id)}">${escapeHtml(truncate(d.file_path || d.id, 36))}</option>`).join("");
    const q = await apiGet(`/api/kb/chunks?${new URLSearchParams({ thread_id: tid, doc_id: selDoc })}`);
    const chunks = q.chunks || [];
    body.innerHTML = drawerSection("切片管理", `
      <div class="d-row" style="gap:8px">
        <select class="d-input" id="rgChunkSel">${opts}</select>
        <span class="d-empty" style="margin:0">共 ${chunks.length} 个切片</span>
        <button class="d-btn d-btn-sm" id="rgChunkReload">刷新</button>
      </div>
      ${chunks.length ? `
      <table class="d-table"><thead><tr>
        <th>#</th><th>切片预览</th><th>Tokens</th><th>向量ID</th><th>标签</th><th>操作</th>
      </tr></thead><tbody>` + chunks.map((c) => `
        <tr>
          <td>${c.order}</td>
          <td class="cell-long">${escapeHtml(truncate(c.content, 140))}</td>
          <td>${c.tokens}</td>
          <td><code class="rg-code">${escapeHtml(c.id)}</code></td>
          <td>${(c.tags || []).map((t) => `<span class="kc-badge">${escapeHtml(t)}</span>`).join(" ") || "—"}</td>
          <td class="d-row" style="gap:6px">
            <button class="d-btn d-btn-sm" data-edit="${escapeHtml(c.id)}">编辑</button>
            <button class="d-btn d-btn-sm danger" data-del="${escapeHtml(c.id)}">删除</button>
          </td>
        </tr>`).join("") + `</tbody></table>` : `
      <div class="d-empty">该范围没有切片。</div>`}`);
    $("#rgChunkSel", body).addEventListener("change", (e) => { selDoc = e.target.value; loadAll(); });
    $("#rgChunkReload", body).addEventListener("click", render);

    const loadAll = async () => {
      selDoc = $("#rgChunkSel", body).value;
      await render();
    };

    $$("button[data-edit]", body).forEach((b) => b.addEventListener("click", () => {
      const c = chunks.find((x) => x.id === b.dataset.edit);
      if (!c) return;
      const { wrap, close, bd } = ragModal("编辑切片", `
        <div class="d-empty" style="padding:0 0 6px">${escapeHtml(c.id)}</div>
        <label style="font-size:12px;color:var(--text-soft)">内容（保存后重新向量化）</label>
        <textarea class="d-input" rows="8" style="width:100%" id="rgEditContent">${escapeHtml(c.content)}</textarea>
        <label style="font-size:12px;color:var(--text-soft);margin-top:6px">标签（逗号分隔）</label>
        <input class="d-input" id="rgEditTags" value="${escapeHtml((c.tags || []).join(", "))}">
        <div class="d-row" style="gap:8px;margin-top:10px">
          <button class="d-btn" id="rgEditSave">保存</button>
          <button class="d-btn" data-close>取消</button>
        </div>`, { wide: false });
      $("#rgEditSave", bd).addEventListener("click", async () => {
        const content = $("#rgEditContent", bd).value;
        const tags = $("#rgEditTags", bd).value;
        try {
          const r = await apiPost("/api/kb/chunk_edit", { thread_id: tid, chunk_key: c.id, content, tags });
          if (!r.ok) throw new Error(r.error || "保存失败");
          toast("切片已更新并重新向量化", "success");
          close(); await render();
        } catch (e) { toast("保存失败: " + e.message, "error"); }
      });
    }));
    $$("button[data-del]", body).forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`删除切片 ${b.dataset.del}？其内容将从知识库移除。`)) return;
      try {
        const r = await apiPost("/api/kb/chunk_delete", { thread_id: tid, chunk_key: b.dataset.del });
        if (!r.ok) throw new Error(r.error || "删除失败");
        toast("已删除切片", "success"); await render();
      } catch (e) { toast("删除失败: " + e.message, "error"); }
    }));
  };
  await render();
}

/* ---------- 子页：检索配置 ---------- */

async function ragConfig(content, tid, body) {
  const r = await apiGet(`/api/kb/config?thread_id=${encodeURIComponent(tid)}`);
  const c = r.config || {};
  body.innerHTML = `
    <form id="rgConfigForm" style="display:flex;flex-direction:column;gap:16px;max-width:560px">
      <div class="rg-cfg-card">
        <div class="rg-cfg-hd">检索参数</div>
        <div class="rg-cfg-row">
          <label>Top-K 召回数</label>
          <input class="d-input" id="rgCtk" type="number" min="1" max="50" value="${c.top_k ?? 12}">
        </div>
        <div class="rg-cfg-row">
          <label>相似度阈值</label>
          <input class="d-input" id="rgCth" type="number" step="0.05" min="0" max="1" value="${c.threshold ?? 0.2}">
        </div>
        <div class="rg-cfg-row">
          <label>向量重排</label>
          <label class="rg-switch"><input type="checkbox" id="rgCrr" ${c.rerank === false ? "" : "checked"}><i></i>Rerank 模型二次排序</label>
        </div>
        <div class="rg-cfg-row">
          <label>混合检索</label>
          <label class="rg-switch"><input type="checkbox" id="rgChy" ${c.hybrid ? "checked" : ""}><i></i>关键词 + 向量混合（默认检索模式）</label>
        </div>
      </div>
      <div class="rg-cfg-card">
        <div class="rg-cfg-hd">分块参数</div>
        <div class="rg-cfg-row">
          <label>分块策略</label>
          <select class="d-input" id="rgCst">
            ${[
              ["R", "R 递归字符分块（推荐）"],
              ["F", "F 固定 Token 窗口"],
              ["V", "V 语义向量分块（需 langchain-experimental）"],
              ["P", "P 段落语义分块（需文档结构）"],
              ["C", "C 自定义（递归 + 定位前缀）"],
            ].map(([v, l]) => `<option value="${v}" ${(c.chunking_strategy ?? "R") === v ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </div>
        <div class="rg-cfg-row">
          <label>分块大小（tokens）</label>
          <input class="d-input" id="rgCcs" type="number" min="100" max="2000" value="${c.chunk_token_size ?? 600}">
        </div>
        <div class="rg-cfg-row">
          <label>分块重叠</label>
          <input class="d-input" id="rgCco" type="number" min="0" max="400" value="${c.chunk_overlap_token_size ?? 80}">
        </div>
      </div>
      <div class="rg-cfg-card">
        <div class="rg-cfg-hd">实体抽取</div>
        <div class="rg-cfg-row">
          <label>最大实体数</label>
          <input class="d-input" id="rgCem" type="number" min="1" max="100" value="${c.entity_extract_max_entities ?? 30}">
        </div>
        <div class="rg-cfg-row">
          <label>实体 Token 上限</label>
          <input class="d-input" id="rgCet" type="number" min="200" value="${c.max_entity_tokens ?? 3000}">
        </div>
        <div class="rg-cfg-row">
          <label>关系 Token 上限</label>
          <input class="d-input" id="rgCrt" type="number" min="200" value="${c.max_relation_tokens ?? 5000}">
        </div>
      </div>
      <div class="d-row" style="gap:8px">
        <button class="d-btn primary" type="submit">保存配置</button>
        <span class="d-empty">保存后重新构建实例生效；Top-K / 阈值 / 模式亦为检索调试面板默认值。</span>
      </div>
    </form>`;
  $("#rgConfigForm", body).addEventListener("submit", async (e) => {
    e.preventDefault();
    const num = (id) => Number($(id, body).value || 0);
    try {
      const r2 = await apiPost("/api/kb/config", { thread_id: tid, config: {
        top_k: num("#rgCtk"), threshold: num("#rgCth"), rerank: $("#rgCrr", body).checked,
        hybrid: $("#rgChy", body).checked, chunking_strategy: $("#rgCst", body).value, chunk_token_size: num("#rgCcs"),
        chunk_overlap_token_size: num("#rgCco"), entity_extract_max_entities: num("#rgCem"),
        max_entity_tokens: num("#rgCet"), max_relation_tokens: num("#rgCrt"),
      }});
      if (!r2.config) throw new Error("保存失败");
      toast("配置已保存", "success");
    } catch (err) { toast("保存失败: " + err.message, "error"); }
  });
}

/* ---------- 子页：检索调试 ---------- */

async function ragTest(content, tid, body) {
  const cfg = (await apiGet(`/api/kb/config?thread_id=${encodeURIComponent(tid)}`)).config || {};
  body.innerHTML = `
    <div style="max-width:560px">
      <div class="rg-cfg-card" style="margin-bottom:12px">
        <div class="rg-cfg-hd">向量检索</div>
        <textarea class="d-input" id="rgTq" rows="3" placeholder="输入查询，查看向量召回的切片 / 实体 / 关系…"></textarea>
        <div class="rg-cfg-row" style="margin-top:8px">
          <label>Top-K</label>
          <input class="d-input" id="rgTtk" type="number" min="1" max="30" value="${cfg.top_k || 8}" style="width:80px">
        </div>
        <div class="rg-cfg-row">
          <label>阈值</label>
          <input class="d-input" id="rgTth" type="number" step="0.05" min="0" max="1" value="${cfg.threshold || 0.2}" style="width:80px">
        </div>
        <div class="d-row" style="gap:8px;margin-top:10px">
          <button class="d-btn primary" id="rgTSearch">检索</button>
          <span class="d-empty" id="rgTBusy" style="display:none">检索中…</span>
        </div>
      </div>
      <div id="rgTRes"></div>
    </div>`;

  const params = () => ({
    q: $("#rgTq", body).value.trim(),
    top_k: Number($("#rgTtk", body).value || 8),
    threshold: Number($("#rgTth", body).value || 0),
  });

  const renderHits = (hits, title) => {
    if (!hits || !hits.length) return "";
    let h = `<div class="rg-result-group"><div class="rg-result-title">${escapeHtml(title)}<span class="rg-result-count">${hits.length}</span></div>`;
    h += hits.map((x) => `
      <div class="rg-hit-card">
        <div class="rg-hit-top"><span class="rg-hit-score">${Number(x.score).toFixed(3)}</span><code class="rg-code">${escapeHtml(x.id || "")}</code></div>
        <div class="rg-hit-body">${escapeHtml(truncate(x.content, 200))}</div>
      </div>`).join("");
    h += `</div>`;
    return h;
  };

  const busy = (on) => { const b = $("#rgTBusy", body); if (b) b.style.display = on ? "" : "none"; };

  $("#rgTSearch", body).addEventListener("click", async () => {
    const p = params();
    if (!p.q) { toast("请输入查询", "error"); return; }
    busy(true);
    try {
      const r = await apiGet(`/api/kb/search?${new URLSearchParams({ thread_id: tid, q: p.q, top_k: p.top_k })}`);
      const vh = r.vector_hits || {};
      const out = [];
      const threshold = p.threshold;
      const filter = (arr) => (arr || []).filter((x) => !threshold || Number(x.score) >= threshold);
      const fc = filter(vh.chunks);
      const fe = filter(vh.entities);
      const fr = filter(vh.relations);
      if (!fc.length && !fe.length && !fr.length) {
        out.push(`<div class="d-empty" style="margin-top:12px">无高于阈值 ${threshold} 的命中。</div>`);
      }
      out.push(renderHits(fc, "切片"));
      out.push(renderHits(fe, "实体"));
      out.push(renderHits(fr, "关系"));
      $("#rgTRes", body).innerHTML = out.join("");
    } catch (e) { $("#rgTRes", body).innerHTML = drawerErr(e); }
    busy(false);
  });
}