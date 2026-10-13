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
  if (parts[0] === "graph") return { section: "graph" };
  return { section: "kb" };
}

function ragNavHighlight() {
  const r = ragParseHash() || { section: "kb" };
  const active = ["kb", "create", "detail"].includes(r.section) ? "kb" : r.section;
  $$(".rag-nav-item").forEach((a) => a.classList.toggle("active", a.dataset.route === active));
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
    else if (r.section === "graph") await ragGraphManage(content);
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
        <a class="rag-nav-item" data-route="graph" href="#/rag-admin/graph"><span class="rag-nav-ico">🕸</span>全局图谱</a>
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

/* 通用分页控件（上一页 / 页码 / 下一页 / 跳页，页码从 1 开始） */
function ragBuildPager(page, pages, total, onGo) {
  const pg = document.createElement("div");
  pg.className = "d-row";
  pg.style.cssText = "gap:8px;margin-top:8px;flex-wrap:wrap";
  const win = 5;
  const lo = Math.max(1, page - Math.floor(win / 2));
  const hi = Math.min(pages, lo + win - 1);
  let h = `<span class="d-empty" style="margin:0">共 ${total} 条 · 第 ${page}/${pages} 页</span>`;
  h += `<button class="d-btn d-btn-sm" data-nav="prev" ${page > 1 ? "" : "disabled"}>‹ 上一页</button>`;
  h += `<span class="d-row" style="gap:4px;flex-wrap:wrap">`;
  if (lo > 1) h += `<button class="d-btn d-btn-sm" data-pg="1">1</button>`;
  if (lo > 2) h += `<span class="d-empty" style="margin:0">…</span>`;
  for (let p = lo; p <= hi; p++) h += `<button class="d-btn d-btn-sm${p === page ? " primary" : ""}" data-pg="${p}">${p}</button>`;
  if (hi < pages - 1) h += `<span class="d-empty" style="margin:0">…</span>`;
  if (hi < pages) h += `<button class="d-btn d-btn-sm" data-pg="${pages}">${pages}</button>`;
  h += `</span>`;
  h += `<button class="d-btn d-btn-sm" data-nav="next" ${page < pages ? "" : "disabled"}>下一页 ›</button>`;
  h += `<span class="d-row" style="gap:4px"><input type="number" class="d-input" data-jump min="1" max="${pages}" style="width:64px;flex:none" placeholder="页码">&nbsp;<button class="d-btn d-btn-sm" data-jumpgo>跳转</button></span>`;
  pg.innerHTML = h;
  const go = (p) => { p = Math.min(pages, Math.max(1, parseInt(p, 10) || page)); if (p !== page) onGo(p); };
  const prev = pg.querySelector("button[data-nav='prev']");
  const next = pg.querySelector("button[data-nav='next']");
  if (prev) prev.addEventListener("click", () => go(page - 1));
  if (next) next.addEventListener("click", () => go(page + 1));
  pg.querySelectorAll("button[data-pg]").forEach((b) => b.addEventListener("click", () => go(parseInt(b.dataset.pg, 10))));
  const jgo = pg.querySelector("button[data-jumpgo]");
  const jin = pg.querySelector("input[data-jump]");
  if (jgo) jgo.addEventListener("click", () => go(jin.value));
  if (jin) jin.addEventListener("keydown", (e) => { if (e.key === "Enter") go(jin.value); });
  return pg;
}

/* 逐项串行删除（避免单个长请求超时），onProgress(i,n) 回报进度 */
async function ragBatchDelete(items, doOne, onProgress) {
  let ok = 0, fail = 0;
  const errs = [];
  for (let i = 0; i < items.length; i++) {
    if (onProgress) onProgress(i + 1, items.length);
    try { await doOne(items[i], i); ok++; }
    catch (e) { fail++; if (errs.length < 3) errs.push(String((e && e.message) || e)); }
    if (i < items.length - 1) await new Promise((res) => setTimeout(res, 60));
  }
  return { ok, fail, errs };
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
  const PAGE = 15;
  let page = 1;
  let running = false;
  const selected = new Set();
  const render = async () => {
    running = true;
    body.innerHTML = `<div class="d-empty">加载中…</div>`;
    let r = await apiGet(`/api/kb/documents?${new URLSearchParams({ thread_id: tid, page, page_size: PAGE })}`);
    const pages = Math.max(1, Math.ceil(r.total / PAGE));
    if (page > pages) { page = pages; r = await apiGet(`/api/kb/documents?${new URLSearchParams({ thread_id: tid, page, page_size: PAGE })}`); }
    const docs = r.docs || [];
    const anyBusy = docs.some((d) => ["processing", "pending"].includes(d.status.toLowerCase()));
    body.innerHTML = drawerSection("文档列表", `
      <div class="rg-toolbar">
        <button class="d-btn" id="rgUpFile">📄 上传文件</button>
        <button class="d-btn" id="rgUpBatch">📚 批量上传</button>
        <button class="d-btn" id="rgUpUrl">🔗 导入 URL</button>
        <select class="d-input" id="rgStrat" style="width:auto;flex:none" title="本次上传/喂入采用的分块策略">
          <option value="">分块策略：跟随配置</option>
          <option value="R">R 递归字符</option>
          <option value="C">C QA 键值对（问答 CSV 每问一答一个 chunk）</option>
          <option value="F">F 固定 Token</option>
          <option value="P">P 段落语义</option>
          <option value="V">V 语义向量</option>
        </select>
        <select class="d-input" id="rgTarget" style="width:auto;flex:none" title="本次上传/喂入的导入目标">
          <option value="both">导入目标：向量 + 图</option>
          <option value="vector">仅导入向量库</option>
          <option value="graph">仅导入图库</option>
        </select>
        <button class="d-btn danger" id="rgRetryFail" ${docs.some((d) => d.status.toLowerCase() === "failed") ? "" : "disabled"}>⟳ 重试失败</button>
        <button class="d-btn danger" id="rgDocBatchDel" disabled>🗑 批量删除</button>
        <span class="d-empty" id="rgDocSelCount" style="margin:0"></span>
        <input type="file" id="rgFile1" style="display:none" accept=".txt,.md,.json,.csv,.html,.pdf,.docx,.pptx,.xlsx,.xls,.epub,.ipynb">
        <input type="file" id="rgFileN" style="display:none" multiple accept=".txt,.md,.json,.csv,.html,.pdf,.docx,.pptx,.xlsx,.xls,.epub,.ipynb">
      </div>
      <textarea class="d-input" id="rgFeedText" rows="6" placeholder="或直接粘贴文本喂入（自动分块 + 抽取实体关系建图）…"></textarea>
      <div class="rg-toolbar"><button class="d-btn primary" id="rgFeed">＋ 喂入文本</button>
        <div class="d-empty" id="rgBusy" style="display:none">⏳ 正在解析/嵌入/建图（可能 10-60 秒）…</div></div>
      ${docs.length ? `
      <table class="d-table"><thead><tr>
        <th style="width:30px"><input type="checkbox" id="rgDocAll" title="全选本页"></th>
        <th>文件名</th><th>大小</th><th>状态</th><th>切片</th><th>上传时间</th><th>操作</th>
      </tr></thead><tbody>` + docs.map((d) => `
        <tr>
          <td><input type="checkbox" class="rg-doc-check" data-id="${escapeHtml(d.id)}"></td>
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

    // 分页：上一页 / 页码 / 下一页 / 跳页（页码从 1 开始）
    if (r.total > 0) body.appendChild(ragBuildPager(page, pages, r.total, (p) => { page = p; render(); }));

    // 选择与批量删除（跨页保留选择）
    const syncSel = () => {
      const ids = docs.map((d) => d.id);
      $$("input.rg-doc-check", body).forEach((c) => { c.checked = selected.has(c.dataset.id); });
      const all = $("#rgDocAll", body);
      if (all) {
        const onPage = ids.filter((id) => selected.has(id)).length;
        all.checked = ids.length > 0 && onPage === ids.length;
        all.indeterminate = onPage > 0 && onPage < ids.length;
      }
      const btn = $("#rgDocBatchDel", body);
      if (btn) btn.disabled = selected.size === 0;
      const sc = $("#rgDocSelCount", body);
      if (sc) sc.textContent = selected.size ? `已选 ${selected.size} 项` : "";
    };
    $$("input.rg-doc-check", body).forEach((c) => c.addEventListener("change", () => {
      if (c.checked) selected.add(c.dataset.id); else selected.delete(c.dataset.id);
      syncSel();
    }));
    const allChk = $("#rgDocAll", body);
    if (allChk) allChk.addEventListener("change", () => {
      docs.forEach((d) => { if (allChk.checked) selected.add(d.id); else selected.delete(d.id); });
      syncSel();
    });
    syncSel();

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
    const strat = () => { const s = $("#rgStrat", body); return s ? s.value : ""; };
    const target = () => { const t = $("#rgTarget", body); return t ? t.value : "both"; };
    const progressHost = document.createElement("div");
    progressHost.id = "rgProgressHost";
    content.appendChild(progressHost);
    const splitTip = (r2) => r2.splits && r2.splits > 1 ? `（过大的内容已自动拆为 ${r2.splits} 份，后台逐份串行喂入）` : "";
    const startIngestPoll = (trackId, label) => new Promise((resolve) => {
      progressHost.innerHTML = `<div class="rg-progress">
        <div class="d-empty" style="margin:0">⏳ ${escapeHtml(label)}…</div>
        <div class="rg-progress-bar" id="rgPbar"><i style="width:0%"></i></div>
        <div class="d-row" style="justify-content:space-between;flex-wrap:wrap;gap:4px">
          <span class="d-empty" id="rgPtext" style="margin:0">准备中…</span>
          <span class="d-row" id="rgChips" style="gap:3px;flex-wrap:wrap"></span>
        </div>
      </div>`;
      const pbar = $("#rgPbar", progressHost);
      const ptext = $("#rgPtext", progressHost);
      const chips = $("#rgChips", progressHost);
      const draw = (p) => {
        const total = Math.max(1, p.total || 1);
        const done = p.done || 0;
        const failed = p.failed || 0;
        const pct = Math.min(100, Math.round((done * 100) / total));
        if (pbar) { pbar.style.display = "block"; const i = $("i", pbar); if (i) i.style.width = pct + "%"; }
        if (ptext) ptext.textContent = `完成 ${done}/${total}${failed ? `（失败 ${failed}）` : ""}`;
        if (chips) {
          chips.innerHTML = (p.docs || []).map((d) => `<span title="${escapeHtml((d.error || "").slice(0, 60) || d.status)}" class="rg-chip ${escapeHtml(d.status)}">${d.status === "queued" ? "·" : d.status === "processing" ? "◌" : d.status === "done" ? "✓" : "✗"}</span>`).join("");
        }
      };
      const poll = () => apiGet(`/api/kb/ingest_progress?track_id=${encodeURIComponent(trackId)}`).then((p) => {
        if (!progressHost.isConnected) { resolve("gone"); return; }
        draw(p);
        if (p.status === "done") { toast(`已完成导入：成功 ${p.done}，失败 ${p.failed}${p.failed ? "（可点击「重试失败」）" : ""}`, p.failed ? "warning" : "success"); progressHost.innerHTML = ""; resolve("done"); }
        else if (p.status === "error") { toast("导入失败: " + (p.error || "未知错误"), "error"); progressHost.innerHTML = ""; resolve("error"); }
        else if (p.status === "cancelled") { progressHost.innerHTML = ""; resolve("cancelled"); }
        else setTimeout(poll, 1500);
      }).catch((e) => { progressHost.innerHTML = ""; toast("进度查询失败: " + e.message, "error"); resolve("error"); });
      setTimeout(poll, 300);
    });
    $("#rgFeed", body).addEventListener("click", async () => {
      const txt = $("#rgFeedText", body).value.trim();
      if (!txt) { toast("请先输入内容", "error"); return; }
      const r2 = await apiPost("/api/kb/ingest", { thread_id: tid, texts: [txt], chunking_strategy: strat(), target: target() });
      if (!r2.ok) { if (r2.error) toast(r2.error, "error"); else toast("喂入失败", "error"); return; }
      $("#rgFeedText", body).value = "";
      const fl = await startIngestPoll(r2.track_id, `喂入文本${splitTip(r2)}`);
      if (fl !== "gone") await render();
    });

    const toggleBusy = (on) => { const b = $("#rgBusy", body); if (b) b.style.display = on ? "" : "none"; };

    // 上传
    const uploadOne = async (file) => {
      const fd = new FormData(); fd.append("file", file);
      const up = await fetch("/api/upload", { method: "POST", body: fd });
      if (up.status === 401) return onUnauthorized();
      const ud = await up.json().catch(() => ({}));
      if (!up.ok) throw new Error(ud.error || "上传失败");
      return apiPost("/api/kb/ingest_file", { thread_id: tid, path: ud.path, chunking_strategy: strat(), target: target() });
    };
    $("#rgUpFile", body).addEventListener("click", () => $("#rgFile1", body).click());
    $("#rgUpBatch", body).addEventListener("click", () => $("#rgFileN", body).click());
    $("#rgFile1", body).addEventListener("change", async (e) => {
      const f = e.target.files[0]; if (!f) return;
      try {
        const r2 = await uploadOne(f); if (!r2.ok) throw new Error(r2.error);
        const fl = await startIngestPoll(r2.track_id, `上传「${f.name}」${splitTip(r2)}`);
        if (fl !== "gone") await render();
      } catch (err) { toast("失败: " + err.message, "error"); }
      e.target.value = "";
    });
    $("#rgFileN", body).addEventListener("change", async (e) => {
      const files = Array.from(e.target.files || []); if (!files.length) return;
      let ok = 0;
      for (const [i, f] of files.entries()) {
        try {
          const r2 = await uploadOne(f); if (!r2.ok) throw new Error(r2.error);
          ok++;
          const fl = await startIngestPoll(r2.track_id, `批量喂入 ${i + 1}/${files.length}「${f.name}」${splitTip(r2)}`);
          if (fl === "gone") return;
        } catch (err) { toast(`「${f.name}」失败: ${err.message}`, "error"); }
      }
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
      try {
        const r2 = await apiPost("/api/kb/retry_failed", { thread_id: tid, chunking_strategy: strat(), target: target() });
        if (!r2.ok) throw new Error(r2.error || "没有可重试的失败文档");
        const fl = await startIngestPoll(r2.track_id, `重试 ${r2.total} 个失败文档（串行 + 退避）`);
        if (fl !== "gone") await render();
      } catch (e) { toast("重试失败: " + e.message, "error"); }
    });

    $("#rgDocBatchDel", body).addEventListener("click", async () => {
      const ids = Array.from(selected);
      if (!ids.length) return;
      if (!confirm(`确认删除选中的 ${ids.length} 个文档及其分块/实体/向量？此操作不可恢复。`)) return;
      const btn = $("#rgDocBatchDel", body);
      btn.disabled = true;
      const res = await ragBatchDelete(ids, async (id) => {
        const r2 = await apiPost("/api/kb/delete", { thread_id: tid, doc_id: id });
        if (!r2.ok) throw new Error(r2.error || "删除失败");
        selected.delete(id);
      }, (i, n) => { btn.textContent = `删除中 ${i}/${n}…`; });
      btn.textContent = "🗑 批量删除";
      toast(`批量删除完成：成功 ${res.ok}，失败 ${res.fail}${res.fail ? "（" + res.errs[0] + "）" : ""}`, res.fail ? "warning" : "success");
      await render();
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
  let page = 1;
  let pageSize = 20;
  const selected = new Set();
  const render = async () => {
    body.innerHTML = `<div class="d-empty">加载中…</div>`;
    const opts = `<option value="">全部文档</option>` + docs.map((d) =>
      `<option value="${escapeHtml(d.id)}"${d.id === selDoc ? " selected" : ""}>${escapeHtml(truncate(d.file_path || d.id, 36))}</option>`).join("");
    let q = await apiGet(`/api/kb/chunks?${new URLSearchParams({ thread_id: tid, doc_id: selDoc, page, page_size: pageSize })}`);
    const pages = Math.max(1, Math.ceil(q.total / pageSize));
    if (page > pages) { page = pages; q = await apiGet(`/api/kb/chunks?${new URLSearchParams({ thread_id: tid, doc_id: selDoc, page, page_size: pageSize })}`); }
    const chunks = q.chunks || [];
    body.innerHTML = drawerSection("切片管理", `
      <div class="rg-toolbar">
        <select class="d-input" id="rgChunkSel" style="width:auto;flex:none;max-width:340px">${opts}</select>
        <select class="d-input" id="rgChunkPageSize" style="width:auto;flex:none" title="每页条数">
          <option value="20">每页 20</option>
          <option value="50">每页 50</option>
          <option value="100">每页 100</option>
        </select>
        <span class="d-empty" style="margin:0">共 ${q.total} 个切片</span>
        <div style="flex:1"></div>
        <button class="d-btn danger" id="rgChunkBatchDel" disabled>🗑 批量删除</button>
        <span class="d-empty" id="rgChunkSelCount" style="margin:0"></span>
        <button class="d-btn d-btn-sm" id="rgChunkReload">刷新</button>
      </div>
      ${chunks.length ? `
      <table class="d-table"><thead><tr>
        <th style="width:30px"><input type="checkbox" id="rgChunkAll" title="全选本页"></th>
        <th>#</th><th>切片预览</th><th>Tokens</th><th>向量ID</th><th>标签</th><th>操作</th>
      </tr></thead><tbody>` + chunks.map((c) => `
        <tr>
          <td><input type="checkbox" class="rg-chunk-check" data-id="${escapeHtml(c.id)}"></td>
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

    $("#rgChunkSel", body).addEventListener("change", (e) => { selDoc = e.target.value; page = 1; selected.clear(); render(); });
    const psSel = $("#rgChunkPageSize", body);
    if (psSel) {
      psSel.value = String(pageSize);
      psSel.addEventListener("change", () => { pageSize = parseInt(psSel.value, 10) || 20; page = 1; render(); });
    }
    $("#rgChunkReload", body).addEventListener("click", () => render());
    if (q.total > 0) body.appendChild(ragBuildPager(page, pages, q.total, (p) => { page = p; render(); }));

    // 选择与批量删除（跨页保留选择）
    const syncSel = () => {
      const ids = chunks.map((c) => c.id);
      $$("input.rg-chunk-check", body).forEach((c) => { c.checked = selected.has(c.dataset.id); });
      const all = $("#rgChunkAll", body);
      if (all) {
        const onPage = ids.filter((id) => selected.has(id)).length;
        all.checked = ids.length > 0 && onPage === ids.length;
        all.indeterminate = onPage > 0 && onPage < ids.length;
      }
      const btn = $("#rgChunkBatchDel", body);
      if (btn) btn.disabled = selected.size === 0;
      const sc = $("#rgChunkSelCount", body);
      if (sc) sc.textContent = selected.size ? `已选 ${selected.size} 项` : "";
    };
    $$("input.rg-chunk-check", body).forEach((c) => c.addEventListener("change", () => {
      if (c.checked) selected.add(c.dataset.id); else selected.delete(c.dataset.id);
      syncSel();
    }));
    const allChk = $("#rgChunkAll", body);
    if (allChk) allChk.addEventListener("change", () => {
      chunks.forEach((c) => { if (allChk.checked) selected.add(c.id); else selected.delete(c.id); });
      syncSel();
    });
    syncSel();

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
        selected.delete(b.dataset.del);
        toast("已删除切片", "success"); await render();
      } catch (e) { toast("删除失败: " + e.message, "error"); }
    }));
    $("#rgChunkBatchDel", body).addEventListener("click", async () => {
      const ids = Array.from(selected);
      if (!ids.length) return;
      if (!confirm(`确认删除选中的 ${ids.length} 个切片？其内容将从知识库移除，不可恢复。`)) return;
      const btn = $("#rgChunkBatchDel", body);
      btn.disabled = true;
      const res = await ragBatchDelete(ids, async (key) => {
        const r = await apiPost("/api/kb/chunk_delete", { thread_id: tid, chunk_key: key });
        if (!r.ok) throw new Error(r.error || "删除失败");
        selected.delete(key);
      }, (i, n) => { btn.textContent = `删除中 ${i}/${n}…`; });
      btn.textContent = "🗑 批量删除";
      toast(`批量删除完成：成功 ${res.ok}，失败 ${res.fail}${res.fail ? "（" + res.errs[0] + "）" : ""}`, res.fail ? "warning" : "success");
      await render();
    });
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
              ["C", "C QA 键值对（question,answer 两列 CSV：每问一答一个 chunk，整对不拆分）"],
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
    <div style="width:100%">
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

/* ==================== 全局图谱管理（仅 __global__） ==================== */

const RAG_GRAPH = { tab: "entities", page: 1, pageSize: 50, q: "", kind: "", selected: new Set(), kinds: [] };

function _ragGraphAttrs(obj) {
  try { return JSON.parse((obj && obj.attrs) || "{}") || {}; } catch (e) { return {}; }
}

function _ragGraphKey(tab, it) {
  return tab === "entities" ? String(it.name || "") : String(it.from_id || "") + "\u0001" + String(it.to_id || "");
}

function _ragGraphAttrRows(a) {
  const keys = Object.keys(a || {});
  if (!keys.length) return `<div class="d-empty">无附加属性</div>`;
  return `<table class="d-table"><tbody>` + keys.map((k) => {
    let v = a[k];
    if (v && typeof v === "object") v = JSON.stringify(v);
    return `<tr><td style="white-space:nowrap;color:var(--text-soft);vertical-align:top">${escapeHtml(k)}</td><td style="word-break:break-word">${escapeHtml(String(v))}</td></tr>`;
  }).join("") + `</tbody></table>`;
}

async function ragGraphManage(content) {
  const TID = "__global__";
  const S = RAG_GRAPH;
  S.tab = S.tab === "relations" ? "relations" : "entities";
  S.page = 1;
  S.selected = new Set();
  let reg = new Map();

  content.innerHTML = `
    <div class="d-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px">
      <div class="d-empty" style="margin:0">全局图谱（跨会话） · <b>__global__</b></div>
      <div class="d-row" style="gap:6px">
        <span id="gStat" class="d-empty" style="margin:0"></span>
        <button class="d-btn d-btn-sm" id="gMergeVariant" title="把大小写/拼写变体实体并入规范名节点">合并变体</button>
        <button class="d-btn d-btn-sm" id="gExport">导出 JSON</button>
        <button class="d-btn d-btn-sm" id="gRefresh">刷新</button>
      </div>
    </div>
    <div class="rag-subtabs">
      <a class="rag-subtab${S.tab === "entities" ? " active" : ""}" data-gtab="entities" href="javascript:void(0)">实体</a>
      <a class="rag-subtab${S.tab === "relations" ? " active" : ""}" data-gtab="relations" href="javascript:void(0)">关系</a>
    </div>
    <div id="gBody"></div>`;

  const body = $("#gBody", content);

  const setTab = (tab) => {
    if (S.tab === tab) return;
    S.tab = tab; S.page = 1; S.q = ""; S.kind = ""; S.selected = new Set();
    $$("[data-gtab]", content).forEach((x) => x.classList.toggle("active", x.dataset.gtab === tab));
    load();
  };
  $$("[data-gtab]", content).forEach((a) => a.addEventListener("click", () => setTab(a.dataset.gtab)));
  $("#gRefresh", content).addEventListener("click", () => load());

  $("#gMergeVariant", content).addEventListener("click", async () => {
    if (!confirm("合并图谱中的大小写/拼写变体实体？将把变体节点并入规范名，不可恢复。")) return;
    const btn = $("#gMergeVariant", content);
    btn.disabled = true; const old = btn.textContent; btn.textContent = "合并中…";
    try {
      const r = await apiPost(`/api/graph/merge_variants?thread_id=${encodeURIComponent(TID)}`, {});
      if (!r.ok) throw new Error(r.error || "合并失败");
      const n = r.merged ? Object.keys(r.merged).length : 0;
      toast(`已并入 ${n} 组变体`, "success");
      load();
    } catch (e) { toast("合并失败：" + e.message, "error"); }
    finally { btn.disabled = false; btn.textContent = old; }
  });

  const fetchAll = async () => {
    const out = [];
    let page = 1;
    while (page <= 200) {
      const params = { thread_id: TID, q: S.q, page, page_size: 500 };
      if (S.tab === "entities") params.kind = S.kind;
      const r = await apiGet(`/api/graph/${S.tab}?${new URLSearchParams(params)}`);
      const items = S.tab === "entities" ? (r.entities || []) : (r.relations || []);
      out.push(...items);
      if (!items.length || out.length >= (r.total || 0)) break;
      page++;
    }
    return out;
  };

  $("#gExport", content).addEventListener("click", async () => {
    const btn = $("#gExport", content);
    btn.disabled = true; const old = btn.textContent; btn.textContent = "导出中…";
    try {
      const data = await fetchAll();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `graph-${S.tab}-__global__-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 3000);
      toast(`已导出 ${data.length} 条`, "success");
    } catch (e) { toast("导出失败：" + e.message, "error"); }
    finally { btn.disabled = false; btn.textContent = old; }
  });

  const updateBatch = () => {
    const btn = $("#gBatchDel", body);
    if (!btn) return;
    btn.disabled = S.selected.size === 0;
    btn.textContent = S.selected.size ? `批量删除 (${S.selected.size})` : "批量删除";
  };

  async function load() {
    body.innerHTML = `<div class="d-empty">加载中…</div>`;
    try {
      const params = { thread_id: TID, q: S.q, page: S.page, page_size: S.pageSize };
      if (S.tab === "entities") params.kind = S.kind;
      const r = await apiGet(`/api/graph/${S.tab}?${new URLSearchParams(params)}`);
      if (r.kinds) S.kinds = r.kinds;
      render(r);
    } catch (e) { body.innerHTML = drawerErr(e); }
  }

  function render(r) {
    const total = r.total || 0;
    const pages = Math.max(1, Math.ceil(total / S.pageSize));
    if (S.page > pages) { S.page = pages; return load(); }
    const items = S.tab === "entities" ? (r.entities || []) : (r.relations || []);
    $("#gStat", content).textContent = `${total} 条`;
    reg = new Map(items.map((it, idx) => [String(idx), it]));

    const toolbar = `
      <div class="rg-toolbar" style="flex-wrap:wrap">
        <input class="d-input" id="gQ" placeholder="搜索名称 / 描述…" value="${escapeHtml(S.q)}" style="width:200px">
        ${S.tab === "entities" ? `<select class="d-input" id="gKind" style="width:auto;flex:none">
          <option value="">全部类型</option>
          ${(S.kinds || []).map((k) => `<option value="${escapeHtml(k)}"${k === S.kind ? " selected" : ""}>${escapeHtml(k)}</option>`).join("")}
        </select>` : ``}
        <select class="d-input" id="gPageSize" style="width:auto;flex:none">
          ${[20, 50, 100, 200].map((n) => `<option value="${n}"${n === S.pageSize ? " selected" : ""}>每页 ${n}</option>`).join("")}
        </select>
        <button class="d-btn" id="gSearch">搜索</button>
        <div style="flex:1"></div>
        <button class="d-btn danger" id="gBatchDel" disabled>批量删除</button>
      </div>`;

    let table;
    if (S.tab === "entities") {
      table = `<table class="d-table"><thead><tr>
        <th style="width:28px"><input type="checkbox" id="gAll"></th>
        <th>名称</th><th style="width:120px">类型</th><th>描述</th><th style="width:170px">操作</th>
      </tr></thead><tbody>${items.length ? items.map((n, idx) => {
        const a = _ragGraphAttrs(n);
        const ck = S.selected.has(_ragGraphKey("entities", n)) ? " checked" : "";
        return `<tr>
          <td><input type="checkbox" data-idx="${idx}"${ck}></td>
          <td><b>${escapeHtml(n.name)}</b></td>
          <td>${escapeHtml(n.kind || "Entity")}</td>
          <td>${escapeHtml(truncate(a.description || "", 90))}</td>
          <td class="d-row" style="gap:6px">
            <button class="d-btn d-btn-sm" data-view="${idx}">详情</button>
            <button class="d-btn d-btn-sm" data-edit="${idx}">编辑</button>
            <button class="d-btn d-btn-sm danger" data-del="${idx}">删除</button>
          </td>
        </tr>`;
      }).join("") : `<tr><td colspan="5"><div class="d-empty">没有实体</div></td></tr>`}</tbody></table>`;
    } else {
      table = `<table class="d-table"><thead><tr>
        <th style="width:28px"><input type="checkbox" id="gAll"></th>
        <th>源</th><th>目标</th><th>描述</th><th style="width:70px">权重</th><th style="width:170px">操作</th>
      </tr></thead><tbody>${items.length ? items.map((e, idx) => {
        const a = _ragGraphAttrs(e);
        const ck = S.selected.has(_ragGraphKey("relations", e)) ? " checked" : "";
        return `<tr>
          <td><input type="checkbox" data-idx="${idx}"${ck}></td>
          <td>${escapeHtml(e.from_id)}</td>
          <td>${escapeHtml(e.to_id)}</td>
          <td>${escapeHtml(truncate(a.description || e.label || "", 80))}</td>
          <td>${a.weight != null ? escapeHtml(String(a.weight)) : "—"}</td>
          <td class="d-row" style="gap:6px">
            <button class="d-btn d-btn-sm" data-view="${idx}">详情</button>
            <button class="d-btn d-btn-sm" data-edit="${idx}">编辑</button>
            <button class="d-btn d-btn-sm danger" data-del="${idx}">删除</button>
          </td>
        </tr>`;
      }).join("") : `<tr><td colspan="6"><div class="d-empty">没有关系</div></td></tr>`}</tbody></table>`;
    }

    body.innerHTML = drawerSection(S.tab === "entities" ? "实体列表" : "关系列表", toolbar + table);
    body.appendChild(ragBuildPager(S.page, pages, total, (p) => { S.page = p; S.selected = new Set(); load(); }));

    $("#gSearch", body).addEventListener("click", () => {
      S.q = $("#gQ", body).value.trim(); S.page = 1; S.selected = new Set(); load();
    });
    $("#gQ", body).addEventListener("keydown", (e) => {
      if (e.key === "Enter") { S.q = e.target.value.trim(); S.page = 1; S.selected = new Set(); load(); }
    });
    const kindSel = $("#gKind", body);
    if (kindSel) kindSel.addEventListener("change", () => { S.kind = kindSel.value; S.page = 1; S.selected = new Set(); load(); });
    $("#gPageSize", body).addEventListener("change", (e) => { S.pageSize = parseInt(e.target.value, 10) || 50; S.page = 1; S.selected = new Set(); load(); });

    $$("[data-idx]", body).forEach((ck) => {
      if (ck.tagName !== "INPUT") return;
      ck.addEventListener("change", () => {
        const it = reg.get(ck.dataset.idx);
        if (!it) return;
        const key = _ragGraphKey(S.tab, it);
        if (ck.checked) S.selected.add(key); else S.selected.delete(key);
        updateBatch();
      });
    });
    const all = $("#gAll", body);
    if (all) all.addEventListener("change", () => {
      $$("input[data-idx]", body).forEach((ck) => {
        const it = reg.get(ck.dataset.idx);
        if (!it) return;
        ck.checked = all.checked;
        const key = _ragGraphKey(S.tab, it);
        if (all.checked) S.selected.add(key); else S.selected.delete(key);
      });
      updateBatch();
    });

    const delOne = async (key) => {
      if (S.tab === "entities") {
        const r = await apiPost("/api/graph/entity_delete", { thread_id: TID, name: key });
        if (!r.ok) throw new Error(r.error || "删除失败");
      } else {
        const [src, tgt] = key.split("\u0001");
        const r = await apiPost("/api/graph/relation_delete", { thread_id: TID, src, tgt });
        if (!r.ok) throw new Error(r.error || "删除失败");
      }
    };

    $("#gBatchDel", body).addEventListener("click", async () => {
      const list = [...S.selected];
      if (!list.length) return;
      if (!confirm(`删除选中的 ${list.length} 条${S.tab === "entities" ? "实体" : "关系"}？不可恢复。`)) return;
      const btn = $("#gBatchDel", body);
      btn.disabled = true;
      const res = await ragBatchDelete(list, delOne, (i, n) => { btn.textContent = `删除中 ${i}/${n}`; });
      S.selected = new Set();
      toast(`删除完成：成功 ${res.ok}，失败 ${res.fail}`, res.fail ? "error" : "success");
      load();
    });

    $$("[data-view]", body).forEach((b) => b.addEventListener("click", () => {
      const it = reg.get(b.dataset.view);
      if (!it) return;
      const a = _ragGraphAttrs(it);
      const title = S.tab === "entities" ? `实体详情 · ${it.name}` : `关系详情 · ${it.from_id} → ${it.to_id}`;
      if (S.tab === "relations") { a.from_id = it.from_id; a.to_id = it.to_id; }
      ragModal(title, `
        <div class="d-empty" style="padding:0 0 6px">${escapeHtml(it.id || "")}</div>
        ${_ragGraphAttrRows(a)}
        <div class="d-row" style="justify-content:flex-end;gap:8px;margin-top:10px"><button class="d-btn" data-close>关闭</button></div>`, { wide: true });
    }));

    $$("[data-del]", body).forEach((b) => b.addEventListener("click", async () => {
      const it = reg.get(b.dataset.del);
      if (!it) return;
      const key = _ragGraphKey(S.tab, it);
      const label = S.tab === "entities" ? `实体「${it.name}」` : `关系「${it.from_id} → ${it.to_id}」`;
      if (!confirm(`删除${label}？不可恢复。`)) return;
      try {
        await delOne(key);
        S.selected.delete(key);
        toast("已删除", "success"); await load();
      } catch (e) { toast("删除失败：" + e.message, "error"); }
    }));

    $$("[data-edit]", body).forEach((b) => b.addEventListener("click", () => {
      const it = reg.get(b.dataset.edit);
      if (!it) return;
      const a = _ragGraphAttrs(it);
      if (S.tab === "entities") {
        const m = ragModal("编辑实体", `
          <label style="font-size:12px;color:var(--text-soft)">名称（修改即重命名）</label>
          <input class="d-input" id="geName" value="${escapeHtml(it.name)}" style="width:100%">
          <label style="font-size:12px;color:var(--text-soft);margin-top:6px">类型</label>
          <input class="d-input" id="geType" value="${escapeHtml(it.kind || a.entity_type || "")}" style="width:100%">
          <label style="font-size:12px;color:var(--text-soft);margin-top:6px">描述</label>
          <textarea class="d-input" id="geDesc" rows="6" style="width:100%">${escapeHtml(a.description || "")}</textarea>
          <div class="d-row" style="justify-content:flex-end;gap:8px;margin-top:10px">
            <button class="d-btn" data-close>取消</button>
            <button class="d-btn primary" id="geSave">保存</button>
          </div>`);
        $("#geSave", m.bd).addEventListener("click", async () => {
          try {
            const r = await apiPost("/api/graph/entity_edit", {
              thread_id: TID,
              name: it.name,
              new_name: $("#geName", m.bd).value.trim(),
              entity_type: $("#geType", m.bd).value.trim(),
              description: $("#geDesc", m.bd).value,
              allow_rename: true,
              allow_merge: false,
            });
            if (!r.ok) throw new Error(r.error || "保存失败");
            toast("实体已更新", "success"); m.close(); await load();
          } catch (e) { toast("保存失败：" + e.message, "error"); }
        });
      } else {
        const m = ragModal("编辑关系", `
          <div class="d-empty" style="padding:0 0 6px">${escapeHtml(it.from_id)} → ${escapeHtml(it.to_id)}</div>
          <label style="font-size:12px;color:var(--text-soft)">描述</label>
          <textarea class="d-input" id="grDesc" rows="5" style="width:100%">${escapeHtml(a.description || it.label || "")}</textarea>
          <label style="font-size:12px;color:var(--text-soft);margin-top:6px">关键词（逗号分隔）</label>
          <input class="d-input" id="grKw" value="${escapeHtml(Array.isArray(a.keywords) ? a.keywords.join(", ") : (a.keywords || ""))}" style="width:100%">
          <label style="font-size:12px;color:var(--text-soft);margin-top:6px">权重</label>
          <input class="d-input" id="grWeight" type="number" step="0.1" value="${a.weight != null ? escapeHtml(String(a.weight)) : ""}" style="width:100%">
          <div class="d-row" style="justify-content:flex-end;gap:8px;margin-top:10px">
            <button class="d-btn" data-close>取消</button>
            <button class="d-btn primary" id="grSave">保存</button>
          </div>`);
        $("#grSave", m.bd).addEventListener("click", async () => {
          const wraw = $("#grWeight", m.bd).value.trim();
          try {
            const r = await apiPost("/api/graph/relation_edit", {
              thread_id: TID, src: it.from_id, tgt: it.to_id,
              description: $("#grDesc", m.bd).value,
              keywords: $("#grKw", m.bd).value,
              weight: wraw === "" ? null : parseFloat(wraw),
            });
            if (!r.ok) throw new Error(r.error || "保存失败");
            toast("关系已更新", "success"); m.close(); await load();
          } catch (e) { toast("保存失败：" + e.message, "error"); }
        });
      }
    }));
  }

  await load();
}
