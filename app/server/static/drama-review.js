/* ============================================================================
   短剧审核台（工作台「短剧审核」tab）
   ----------------------------------------------------------------------------
   资产审核：列出项目的 角色/场景/道具 资产（提示词 + 出图），可改提示词重新生成
   （后端 media.py image --ref 锁形象，产物回写 manifest）。
   分镜审核：逐集解析投喂稿，列出每段分镜提示词与已产出视频；可「直接重渲」或
   「修改提示词后重渲」单段；全部确认后「合并成完整视频」（整集 + N集连播）。
   依赖全局工具：$ / $$ / apiGet / apiPost / escapeHtml / toast。
   ============================================================================ */
"use strict";

/* 独立样式（不污染全局 style.css） */
(function () {
  if (document.getElementById("drvStyle")) return;
  const st = document.createElement("style");
  st.id = "drvStyle";
  st.textContent = `
    .drv { padding: 14px 16px; }
    .drv .st-cols { display: grid; grid-template-columns: 300px 1fr; gap: 14px; align-items: start; }
    .drv .st-left > .st-block { margin-bottom: 12px; }
    .drv .drv-proj-sel { width: 100%; }
    .drv .d-row { display: flex; gap: 6px; }
    .drv .drv-body { padding: 4px 2px; }
    .drv-card {
      border: 1px solid #e3e6ef; border-radius: 10px; padding: 10px;
      margin-bottom: 12px; background: #fff;
    }
    .drv-card-top {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 8px; font-size: 13px;
    }
    .drv-status { font-size: 12px; color: #8a90a5; }
    .drv-card-img img, .drv-card-img video {
      width: 100%; max-height: 300px; object-fit: contain;
      border-radius: 8px; background: #f2f4f9; margin-bottom: 8px;
    }
    .drv-kfs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
    .drv-kf { width: 200px; border-radius: 8px; overflow: hidden;
      background: #f2f4f9; border: 1px solid #e3e6ef; }
    .drv-kf img { width: 100%; max-height: 130px; object-fit: contain;
      display: block; margin-bottom: 0; border-radius: 0; }
    .drv-kf-label { display: block; font-size: 11px; color: #4a5070;
      text-align: center; padding: 3px 0; background: #fff; }
    .drv-kf img[data-miss] { visibility: hidden; }
    .drv-kf[data-miss="1"]::after { content: "帧未就绪"; display: flex;
      align-items: center; justify-content: center; height: 90px;
      color: #b0b6c8; font-size: 12px; }
    .drv-kf-regen { width: 100%; font-size: 11px; padding: 3px 0;
      border-radius: 0 0 8px 8px; border: 0; border-top: 1px solid #e3e6ef;
      background: #f7f9ff; color: #4a5070; cursor: pointer; }
    .drv-kf-regen:hover { background: #eef2ff; color: #2b3fd4; }
    .drv-kf-prompt { width: 100%; min-height: 74px; font-size: 11px; line-height: 1.35;
      border-radius: 0; border: 0; border-top: 1px solid #e3e6ef; }
    .drv-kf-note { font-size: 10px; color: #8a90a5; text-align: center;
      padding: 4px 6px; background: #f7f9ff; border-top: 1px solid #e3e6ef; }
    .drv-prompt { width: 100%; font-size: 12px; line-height: 1.5; }
    .drv-card-actions { display: flex; gap: 6px; align-items: center; margin-top: 8px; }
    .drv-tasklog { font-size: 11px; color: #6a7090; flex: 1; word-break: break-all; }
    .drv-segbar {
      display: flex; justify-content: space-between; align-items: center;
      padding: 8px 2px; font-weight: 600;
    }
    .drv-segs { display: grid; gap: 12px; }
    .drv-seg { border: 1px solid #e3e6ef; border-radius: 10px; padding: 10px; background: #fff; }
  `;
  document.head.appendChild(st);
})();

let DRV = { project: "", mode: "assets", ep: "", tasks: new Map() };

function drvPlugin() {
  return {
    tab: "review"
  };
}
window.drvPlugin = drvPlugin;

/* ==================== 项目选择 ==================== */
async function drvLoadProjects() {
  const sel = $("#drvProjectSel");
  try {
    const data = await apiGet("/api/drama-review/projects");
    const list = data.projects || [];
    sel.innerHTML = '<option value="">— 选择作品 —</option>' + list.map((p) =>
      `<option value="${escapeHtml(p.name)}" ${p.name === DRV.project ? "selected" : ""}">${escapeHtml(p.name)}` +
      (p.episodes && p.episodes.length ? `（${p.episodes.length}集）` : "") + "</option>"
    ).join("");
    if (!DRV.project && list.length) { DRV.project = list[0].name; sel.value = DRV.project; }
  } catch (e) {
    sel.innerHTML = '<option value="">加载失败</option>';
    console.error("[drv] projects", e);
  }
}

function drvSelectProject() {
  DRV.project = $("#drvProjectSel").value;
  DRV.ep = "";
  drvRender();
}

/* ==================== 资产模式渲染 ==================== */
async function drvRenderAssets() {
  const box = $("#drvBody");
  box.innerHTML = '<div class="d-empty">加载资产…</div>';
  try {
    const data = await apiGet("/api/drama-review/assets?name=" + encodeURIComponent(DRV.project));
    const list = data.assets || [];
    if (!list.length) {
      box.innerHTML = '<div class="d-empty">未找到 assets/manifest.json</div>';
      return;
    }
    box.innerHTML = list.map((a) => `
      <div class="drv-card" data-id="${escapeHtml(a.id)}">
        <div class="drv-card-top">
          <b>${escapeHtml(a.type)} · ${escapeHtml(a.id)}</b>
          <span class="drv-status" id="drvSt_${escapeHtml(a.id)}">${escapeHtml(a.status || "")}</span>
        </div>
        <div class="drv-card-img">
          ${a.path ? `<img src="/api/deliverables/view?path=${encodeURIComponent(a.path)}${a.md5 ? '&v=' + a.md5 : ''}" alt="${escapeHtml(a.id)}" loading="lazy">` : '<div class="d-empty">无出图</div>'}
        </div>
        <textarea id="drvP_${escapeHtml(a.id)}" class="d-input drv-prompt" rows="4" spellcheck="false">${escapeHtml(a.prompt || "")}</textarea>
        <div class="drv-card-actions">
          <button class="d-btn sm primary" data-act="regen" data-id="${escapeHtml(a.id)}">🖼 用此提示词重新生成</button>
          <span class="drv-tasklog" id="drvLog_${escapeHtml(a.id)}"></span>
        </div>
      </div>`).join("");
    $$(".drv-card [data-act=regen]", box).forEach((b) =>
      b.addEventListener("click", () => drvRegenAsset(b.dataset.id)));
  } catch (e) {
    box.innerHTML = `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function drvRegenAsset(id) {
  const prompt = ($(`#drvP_${id}`).value || "").trim();
  if (!prompt) { toast("提示词不能为空", "warn"); return; }
  try {
    const r = await apiPost("/api/drama-review/assets/regenerate", { name: DRV.project, id, prompt });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "asset", id, cb: drvRenderAssets });
    toast("已启动重新生成", "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

/* ==================== 分镜模式渲染 ==================== */
function drvSegKeyName(seg) {
  return seg.replace(/-(\d+)$/, "");
}

async function drvLoadEpisodes() {
  try {
    const data = await apiGet("/api/drama-review/projects");
    const proj = (data.projects || []).find((p) => p.name === DRV.project);
    return (proj && proj.episodes) || [];
  } catch (e) {
    return [];
  }
}

async function drvRenderSegments() {
  const box = $("#drvBody");
  box.innerHTML = '<div class="d-empty">加载分镜…</div>';
  try {
    const data = await apiGet(
      "/api/drama-review/storyboard?name=" + encodeURIComponent(DRV.project) + "&ep=" + encodeURIComponent(DRV.ep));
    const segs = data.segments || [];
    const vb = Date.now(); // 图片 cache-bust：kf 图 URL 带版本参数，避免 max-age 缓存显示旧帧
    box.innerHTML = `
      <div class="drv-segbar">
        <span class="drv-segbar-title">${escapeHtml(DRV.ep)} · ${segs.length} 段</span>
        <button class="d-btn sm primary" id="drvMergeAll">✅ 确认合并本集（${escapeHtml(DRV.ep)}）</button>
      </div>
      <div class="drv-segs">
        ${segs.map((s) => `
        <div class="drv-seg" data-seg="${escapeHtml(s.seg)}">
          <div class="drv-card-top">
            <b>${escapeHtml(s.seg)} · ${escapeHtml(s.title || "")}</b>
            <span class="drv-status">${s.duration}s ${s.has_video ? "✅已出片" : "⏳未出片"}</span>
          </div>
          ${s.has_video && s.video ? `
          <div class="drv-card-img drv-video">
            <video controls preload="metadata" src="/api/deliverables/view?path=${encodeURIComponent(s.video)}${s.vmtime ? '&v=' + s.vmtime : ''}"></video>
          </div>` : ""}
          ${s.kfs && s.kfs.length ? `
          <div class="drv-card-img drv-kfs">
            ${s.kfs.map((kf, i) => {
              const isRelay = i === 0 && s.kf1_origin === "relay";
              const tag = i === 0 ? (isRelay ? "首帧 ⬅ 上游末帧" : "首帧") : i === 1 ? "尾帧" : "帧" + (i + 1);
              const fr = "kf" + (i + 1);
              if (isRelay) {
                return `<div class="drv-kf" id="drvKf_${encodeURIComponent(s.seg)}_${i}">
              <img loading="lazy" src="/api/deliverables/view?path=${encodeURIComponent(kf)}&v=${vb}" data-kfi="${i}" onerror="const w=this.closest('.drv-kf'); if(w){w.dataset.miss='1'; this.dataset.miss='1';}">
              <span class="drv-kf-label">${tag}</span>
              <div class="drv-kf-note">尾帧接力：渲染时自动抽上段视频末帧</div>
            </div>`;
              }
              const pf = i === 0 ? (s.kf1_prompt || "") : i === 1 ? (s.kf2_prompt || "") : "";
              return `<div class="drv-kf" id="drvKf_${encodeURIComponent(s.seg)}_${i}">
            <img loading="lazy" src="/api/deliverables/view?path=${encodeURIComponent(kf)}&v=${vb}" data-kfi="${i}" onerror="const w=this.closest('.drv-kf'); if(w){w.dataset.miss='1'; this.dataset.miss='1';}">
            <span class="drv-kf-label">${tag}</span>
            <textarea class="d-input drv-kf-prompt" rows="5" spellcheck="false" id="drvKfP_${encodeURIComponent(s.seg)}_${i}">${escapeHtml(pf)}</textarea>
            <button class="d-btn sm drv-kf-regen" data-act="kfreg" data-seg="${escapeHtml(s.seg)}" data-frame="${fr}" data-i="${i}">🔄 改词重出${i === 0 ? "首帧" : "尾帧"}</button>
          </div>`;
            }).join("")}
          </div>` : (s.has_video ? "" : `<div class="drv-card-img"><div class="d-empty">缺关键帧</div></div>`)}
          <textarea id="drvSegP_${escapeHtml(s.seg)}" class="d-input drv-prompt" rows="4" spellcheck="false">${escapeHtml(s.prompt || "")}</textarea>
          <div class="drv-card-actions">
            <button class="d-btn sm" data-act="rerender" data-seg="${escapeHtml(s.seg)}">🎬 直接重渲此段</button>
            <button class="d-btn sm primary" data-act="rerender2" data-seg="${escapeHtml(s.seg)}">✏ 按上方提示词重渲</button>
            <button class="d-btn sm warn" data-act="cascade" data-seg="${escapeHtml(s.seg)}">⛓ 自此段级联重渲（到尾）</button>
            <span class="drv-tasklog" id="drvSegLog_${escapeHtml(s.seg)}"></span>
          </div>
        </div>`).join("")}
      </div>`;
    $$("[data-act=rerender]", box).forEach((b) =>
      b.addEventListener("click", () => drvRerender(b.dataset.seg, "")));
    $$("[data-act=rerender2]", box).forEach((b) =>
      b.addEventListener("click", () => drvRerender(b.dataset.seg, "use_prompt")));
    $$("[data-act=kfreg]", box).forEach((b) =>
      b.addEventListener("click", () => drvKfRegen(b.dataset.seg, b.dataset.frame, b.dataset.i)));
    $$("[data-act=cascade]", box).forEach((b) =>
      b.addEventListener("click", () => drvCascade(b.dataset.seg)));
    $("#drvMergeAll", box).addEventListener("click", () => drvMerge([DRV.ep]));
  } catch (e) {
    box.innerHTML = `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function drvRerender(seg, usePrompt) {
  let prompt = "";
  if (usePrompt) {
    prompt = ($(`#drvSegP_${seg}`).value || "").trim();
    if (!prompt) { toast("提示词不能为空", "warn"); return; }
  }
  try {
    const r = await apiPost("/api/drama-review/segments/render", {
      name: DRV.project, ep: DRV.ep, seg, prompt });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "seg", id: seg, cb: drvRenderSegments });
    toast("已启动重渲 " + seg, "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvKfRegen(seg, frame, i) {
  const promptEl = i !== undefined ? $(`#drvKfP_${encodeURIComponent(seg)}_${i}`) : null;
  const prompt = promptEl ? (promptEl.value || "").trim() : "";
  if (!prompt) { toast("请填写出图提示词", "warn"); return; }
  try {
    const r = await apiPost("/api/drama-review/keyframes/regenerate", {
      name: DRV.project, seg, frame, prompt });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "keyframe", id: seg, cb: drvRenderSegments });
    toast(`已启动 ${seg} ${frame} 改词重出`, "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvCascade(seg) {
  if (!confirm(`确认自此段 ${seg} 级联重渲到片尾？将顺序重渲该段及之后所有段（单线程串行，耗时较长）。`)) return;
  try {
    const r = await apiPost("/api/drama-review/segments/cascade", {
      name: DRV.project, seg });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "seg", id: seg, cb: drvRenderSegments });
    toast(`已启动级联重渲（${seg} → 片尾，共 ${r.count || "?"} 段）`, "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvMerge(eps) {
  try {
    const r = await apiPost("/api/drama-review/merge", { name: DRV.project, eps });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "merge", id: eps.join(","), cb: drvRenderSegments });
    toast("已启动合并 " + eps.join(","), "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

/* ==================== 任务轮询 ==================== */
function drvPoll(tid) {
  if (typeof window.__drvPollingActive === "undefined") window.__drvPollingActive = setInterval(drvPollStep, 3000);
}

async function drvPollStep() {
  if (!DRV.tasks.size) { clearInterval(window.__drvPollingActive); window.__drvPollingActive = undefined; return; }
  for (const [tid, meta] of [...DRV.tasks.entries()]) {
    try {
      const t = await apiGet("/api/drama-review/tasks/" + tid);
      const target = meta.kind === "asset" ? `#drvLog_${meta.id}` : `#drvSegLog_${meta.id}`;
      const el = $(target);
      if (el) {
        const last = (t.log || []).slice(-1)[0] || "";
        el.textContent = (t.status === "running" ? "⏳" : t.status === "done" ? "✅" : t.status === "failed" ? "❌" : "") + " " + last;
      }
      if (t.status === "done" || t.status === "failed") {
        if (meta.cb) meta.cb();
        DRV.tasks.delete(tid);
      }
    } catch (e) { /* 单任务失败不阻断 */ }
  }
}

/* ==================== 主渲染 ==================== */
function drvRender() {
  const box = $("#drvBody");
  if (!DRV.project) { box.innerHTML = '<div class="d-empty">请先选择作品</div>'; return; }
  if (DRV.mode === "assets") {
    drvRenderAssets();
  } else {
    if (!DRV.ep) {
      box.innerHTML = '<div class="d-empty">请选择要审核的集</div>';
      return;
    }
    drvRenderSegments();
  }
}

async function renderReviewTab(el) {
  el.innerHTML = `
    <div class="st drv">
      <div class="st-head">
        <div class="st-head-title">🎬 短剧审核台</div>
        <div class="st-head-hint">资产审核：改提示词重新生成角色/场景/道具图（--ref 锁形象）。分镜审核：逐集列出分镜提示词与成片，可直接重渲或改词重渲；确认后合并成完整视频。</div>
      </div>
      <div class="st-cols">
        <section class="st-left">
          <div class="st-block">
            <div class="st-block-hd"><span>选择作品</span></div>
            <select id="drvProjectSel" class="d-input drv-proj-sel"></select>
            <div class="d-row" style="margin-top:8px">
              <button class="d-btn sm" id="drvRefresh">⟳ 刷新</button>
            </div>
          </div>
          <div class="st-block">
            <div class="st-block-hd"><span>审核模式</span></div>
            <div class="d-row" id="drvModeSeg">
              <button class="d-btn sm" data-mode="assets">🖼 资产审核</button>
              <button class="d-btn sm" data-mode="segments">🎬 分镜审核</button>
            </div>
            <div class="d-row" id="drvEpRow" style="margin-top:8px;flex-wrap:wrap"></div>
          </div>
          <button class="d-btn sm" id="drvMergeFull" style="margin-top:8px">✅ 合并全部集为连播</button>
        </section>
        <section class="st-right">
          <div id="drvBody" class="drv-body"></div>
        </section>
      </div>
    </div>`;

  DRV.mode = "assets";
  drvLoadProjects().then(() => {
    drvSetupModes(el);
    drvSetupEps(el).then(() => drvRender());
  });
}

function drvSetupModes(el) {
  $$("#drvModeSeg button", el).forEach((b) => {
    b.addEventListener("click", () => {
      DRV.mode = b.dataset.mode;
      $$("#drvModeSeg button", el).forEach((x) => x.classList.toggle("primary", x === b));
      drvSetupEps(el).then(() => drvRender());
    });
  });
  $("#drvRefresh", el).addEventListener("click", () => { drvLoadProjects().then(() => drvRender()); });
  $("#drvProjectSel", el).addEventListener("change", drvSelectProject);
  $("#drvMergeFull", el).addEventListener("click", async () => {
    const eps = await drvLoadEpisodes();
    if (!eps.length) { toast("无集可合并", "warn"); return; }
    drvMerge(eps);
  });
}

async function drvSetupEps(el) {
  const row = $("#drvEpRow", el);
  if (DRV.mode !== "segments") { row.innerHTML = ""; DRV.ep = ""; return; }
  const eps = await drvLoadEpisodes();
  if (!eps.length) { row.innerHTML = '<span class="d-empty">无投喂稿</span>'; return; }
  if (!DRV.ep && eps.length) DRV.ep = eps[0];
  row.innerHTML = eps.map((e) =>
    `<button class="d-btn sm ${e === DRV.ep ? "primary" : ""}" data-ep="${escapeHtml(e)}">${escapeHtml(e)}</button>`
  ).join("");
  $$("button[data-ep]", row).forEach((b) => b.addEventListener("click", () => {
    DRV.ep = b.dataset.ep;
    $$("button[data-ep]", row).forEach((x) => x.classList.toggle("primary", x === b));
    drvRender();
  }));
}

window.renderReviewTab = renderReviewTab;
window.__drvPollingActive = undefined;