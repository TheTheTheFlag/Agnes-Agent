/* ============================================================================
   视频创作工作台（Agnes Video 2.5 Flash / Agnes Video V2.0）
   ----------------------------------------------------------------------------
   模型与模式：
     · agnes-video-2.5-flash：文生视频 / 首尾帧 / 图片参考（720P，seconds 控时长）
     · agnes-video-v2.0     ：文生视频 / 图生视频 / 关键帧动画（num_frames + frame_rate 控时长）
   左侧创作台：模型 · 模式 · 参考图 · 提示词；底部参数条（画幅 / 时长 / 种子）
   右侧作品库：视频网格 + 播放灯箱；生成中的任务自动轮询 /api/video/task/{id}，
              完成后后端已自动把 mp4 落盘到服务器，卡片封面走 ffmpeg 抽帧。
   依赖 app.js 全局工具：$ / $$ / apiGet / apiPost / escapeHtml / truncate / fmtAgo / toast。
   依赖 image-studio.js 的上传约定：/api/upload 返回 {path}，参考图用 uploads/<name> 路径。
   ============================================================================ */

const VIDEO_MODE_LBL = { txt2img: "文生图", img2img: "图生图", multi: "多图合成" };

const VideoStudio = {
  model: "agnes-video-2.5-flash",
  mode: "text",
  refs: [],            // [{path, name}]
  busy: false,
  status: null,
  gallery: [],
  pollers: {},         // id -> timer
  el: null,
  selMode: false,      // 批量删除选择模式
  sel: new Set(),      // 已勾选 id 集合
  page: 1,             // 当前页
  perPage: 50,         // 每页条数（与图片作品库一致）
};

/* ---------- 主渲染 ---------- */
async function renderVideoTab(el) {
  el.innerHTML = `
    <div class="st woo">
      ${studioShell.hero("你好，今天想要创作什么？", "")}
      <div class="woo-panel">
        <div class="woo-headrow">
          ${studioShell.typeButton("vsType", "video")}
          <div class="woo-model-wrap">
            <select class="d-input st-param woo-model-select" id="vsModel"></select>
          </div>
          <div class="st-mode-seg woo-mode" id="vsModeSeg"></div>
        </div>

        <div class="woo-input-wrap">
          <textarea id="vsPrompt" class="woo-input" rows="5" placeholder="上传参考图、输入文字或 @主体，描述你想生成的视频。"></textarea>
          <button type="button" id="vsAttach" class="woo-attach" title="添加参考图">＋</button>
        </div>
        <div class="woo-input-status" id="vsPromptHint"></div>

        <div class="st-block woo-refblock" id="vsRefBlock" style="display:none;margin-top:10px">
          <div class="woo-refhd">
            <span id="vsRefTitle" style="font-size:12.5px;font-weight:700;color:var(--text,#e8e9ec)">参考图</span>
            <span class="st-ref-hint" id="vsRefHint"></span>
            <div style="flex:1"></div>
            <button class="d-btn sm" id="vsPickLib">🖼 从作品库选择</button>
          </div>
          <div class="woo-refs" id="vsRefs"><div class="d-empty">暂无参考图，请先选择上方模式</div></div>
          <div class="d-row" style="margin-top:8px">
            <button class="d-btn" id="vsAddRef">＋ 添加参考图</button>
            <span class="d-empty" style="margin:0" id="vsRefNote"></span>
          </div>
          <input type="file" id="vsRefFile" accept="image/*" multiple style="display:none">
        </div>

        <div class="woo-bar">
          <button type="button" id="vsRatioBtn" class="woo-ratio-btn"><b>16:9</b><span class="woo-caret">▾</span></button>
          <label class="woo-bar-lbl" for="vsSeconds">时长</label>
          <select id="vsSeconds" class="d-input st-param woo-bar-sel"></select>
          <select id="vsDur" class="d-input st-param woo-bar-sel" style="display:none"></select>
          <label class="woo-bar-lbl" for="vsSeed">种子</label>
          <input id="vsSeed" class="d-input st-param woo-bar-seed" placeholder="留空随机">
          <select id="vsRatio" class="d-input st-param" style="display:none"></select>
          <div style="flex:1"></div>
          <button type="button" class="woo-submit" id="vsGen" title="生成视频">↑</button>
          <span class="d-empty st-busy" id="vsBusy" style="display:none"></span>
        </div>
      </div>

      <div id="vsKeyBanner" class="st-banner woo-banner hidden"></div>

      <div class="woo-gallery">
        <div class="st-gallery-hd">
          <span class="st-gallery-title">🗂 视频作品库</span>
          <span class="d-empty" style="margin:0" id="vsCountLbl"></span>
          <span class="d-empty" style="margin:0 8px;color:#c0392b;display:none" id="vsSelLbl"></span>
          <div style="flex:1"></div>
          <span id="vsSelBar" style="display:none">
            <button class="d-btn sm" id="vsSelAll">全选</button>
            <button class="d-btn sm danger" id="vsSelDel" disabled>删除选中</button>
            <button class="d-btn sm" id="vsSelCancel">取消</button>
          </span>
          <button class="d-btn sm danger" id="vsBatchDel">🗑 批量删除</button>
          <button class="d-btn sm" id="vsRefresh">⟳ 刷新</button>
        </div>
        <div id="vsGrid" class="st-grid"><div class="d-empty">加载中…</div></div>
        <div id="vsPagination" class="st-pagination" style="display:none"></div>
      </div>
    </div>`;

  VideoStudio.el = {
    model: $("#vsModel", el), seg: $("#vsModeSeg", el), refBlock: $("#vsRefBlock", el), refs: $("#vsRefs", el),
    refTitle: $("#vsRefTitle", el), refHint: $("#vsRefHint", el), refNote: $("#vsRefNote", el),
    prompt: $("#vsPrompt", el), promptHint: $("#vsPromptHint", el),
    gen: $("#vsGen", el), busy: $("#vsBusy", el),
    grid: $("#vsGrid", el), countLbl: $("#vsCountLbl", el), banner: $("#vsKeyBanner", el),
    selLbl: $("#vsSelLbl", el), selBar: $("#vsSelBar", el), selDel: $("#vsSelDel", el),
    pagination: $("#vsPagination", el),
  };

  try {
    VideoStudio.status = await apiGet("/api/video/status");
  } catch (e) { VideoStudio.status = null; }
  if (!VideoStudio.status || !VideoStudio.status.configured) {
    VideoStudio.el.banner.classList.remove("hidden");
    VideoStudio.el.banner.innerHTML = `⚠️ 未检测到 agnes API Key：请在「模型」页接入 agnes 网关（base_url 为 api.agnes-ai.cn）。配置后重新进入本页即可。`;
  }

  const models = (VideoStudio.status && VideoStudio.status.models) || [];
  if (models.length && !models.some((m) => m.id === VideoStudio.model)) {
    VideoStudio.model = models[0].id;
  }
  paintVideoModels();
  paintVideoModeSeg();
  paintVideoParams();
  paintVideoRefs();
  paintVideoPrompt();

  $("#vsAttach", el).addEventListener("click", () => {
    if (!refSpec()) { toast("当前模型 / 模式无需参考图", "info"); return; }
    $("#vsRefFile", el).click();
  });
  $("#vsAddRef", el).addEventListener("click", () => $("#vsRefFile", el).click());
  $("#vsPickLib", el).addEventListener("click", () => openVideoRefPicker());
  $("#vsRefFile", el).addEventListener("change", (e) => {
    const f = e.target.files;
    console.log("[VideoRef] change事件触发, files:", f, "length:", f ? f.length : 0);
    if (f && f.length > 0) {
      onVideoRefFiles(f);
    } else {
      console.warn("[VideoRef] 没有选择文件");
    }
    e.target.value = "";
  });
  VideoStudio.el.model.addEventListener("change", () => {
    VideoStudio.model = VideoStudio.el.model.value;
    const modes = Object.keys(modelMeta(VideoStudio.model).modes || {});
    if (!modes.includes(VideoStudio.mode)) VideoStudio.mode = modes[0];
    VideoStudio.refs = [];
    paintVideoModels(); paintVideoModeSeg(); paintVideoParams(); paintVideoRefs(); paintVideoPrompt();
  });
  $("#vsRefresh", el).addEventListener("click", () => loadVideoGallery());
  VideoStudio.el.gen.addEventListener("click", generateVideo);

  // —— v2 参数条 ——
  syncVideoBar();
  $("#vsRatioBtn", el).addEventListener("click", openVideoRatio);
  studioShell.bindTypeButton("vsType", "video");

  // —— 批量删除 ——
  $("#vsBatchDel", el).addEventListener("click", () => {
    if (VideoStudio.selMode) { VideoStudio.selMode = false; VideoStudio.sel.clear(); }
    else { VideoStudio.selMode = true; }
    paintVsSelBar();
    paintVideoGallery();
  });
  $("#vsSelAll", el).addEventListener("click", () => {
    getVsPaginatedItems().items.forEach((i) => VideoStudio.sel.add(i.id));
    paintVsSelBar();
    paintVideoGallery();
  });
  $("#vsSelDel", el).addEventListener("click", async () => {
    const ids = [...VideoStudio.sel];
    if (!ids.length) return;
    if (!confirm(`确认删除选中的 ${ids.length} 条视频（含服务器文件）？`)) return;
    try {
      const r = await apiPost("/api/video/delete", { ids });
      if (!r.ok) throw new Error(r.error || "删除失败");
      VideoStudio.gallery = VideoStudio.gallery.filter((g) => !VideoStudio.sel.has(g.id));
      VideoStudio.sel.clear();
      VideoStudio.selMode = false;
      paintVsSelBar();
      paintVideoGallery();
      toast(`已删除 ${r.deleted || ids.length} 条视频`, "success");
    } catch (e) { toast("删除失败: " + e.message, "error"); }
  });
  $("#vsSelCancel", el).addEventListener("click", () => {
    VideoStudio.selMode = false; VideoStudio.sel.clear();
    paintVsSelBar();
    paintVideoGallery();
  });

  await loadVideoGallery();
  resumeVideoPolling();
}

/* ---------- 元数据 ---------- */
function modelMeta(id) {
  const models = (VideoStudio.status && VideoStudio.status.models) || [];
  return models.find((m) => m.id === id) || models[0] || { modes: {}, label: id, note: "" };
}
function modeMeta() {
  return (modelMeta(VideoStudio.model).modes || {})[VideoStudio.mode] || { needs: [] };
}

function paintVideoModels() {
  const models = (VideoStudio.status && VideoStudio.status.models) || [];
  VideoStudio.el.model.innerHTML = models.map((m) =>
    `<option value="${escapeHtml(m.id)}" ${m.id === VideoStudio.model ? "selected" : ""}>${escapeHtml(m.id)}</option>`).join("");
}

function paintVideoModeSeg() {
  const modes = modelMeta(VideoStudio.model).modes || {};
  VideoStudio.el.seg.innerHTML = Object.entries(modes).map(([k, m]) =>
    `<button class="st-mode-btn ${k === VideoStudio.mode ? "active" : ""}" data-mode="${k}">
       <b>${m.icon || ""} ${escapeHtml(m.label)}</b><small>${escapeHtml(m.desc || "")}</small></button>`).join("");
  $$(".st-mode-btn", VideoStudio.el.seg).forEach((b) =>
    b.addEventListener("click", () => { VideoStudio.mode = b.dataset.mode; paintVideoModeSeg(); paintVideoParams(); paintVideoRefs(); paintVideoPrompt(); }));
}

function refSpec() {
  const needs = modeMeta().needs || [];
  if (needs.includes("image")) return { min: 1, max: 1, title: "参考图", labels: ["参考图"], note: "图生视频需要 1 张图" };
  if (needs.includes("images2")) return { min: 2, max: 4, title: "关键帧", labels: ["首帧", "尾帧", "帧 3", "帧 4"], note: "关键帧动画 ≥2 张（顺序即过渡顺序）" };
  if (needs.includes("images")) return { min: 1, max: 5, title: "参考图", labels: ["图 1", "图 2", "图 3", "图 4", "图 5"], note: "图片参考 1–5 张（提示词用 <Picture N> 指代）" };
  if (needs.includes("frames")) return { min: 1, max: 2, title: "首尾帧", labels: ["首帧", "尾帧"], note: "首帧 / 尾帧至少提供 1 个" };
  return null;
}

function paintVideoRefs() {
  console.log("[paintVideoRefs] 被调用");
  console.log("[paintVideoRefs] VideoStudio.refs.length:", VideoStudio.refs.length);
  const spec = refSpec();
  console.log("[paintVideoRefs] spec:", spec);
  const block = VideoStudio.el.refBlock;
  console.log("[paintVideoRefs] block:", block ? "找到" : "未找到");
  if (!spec) { block.style.display = "none"; return; }
  block.style.display = "";
  VideoStudio.el.refTitle.textContent = spec.title;
  VideoStudio.el.refHint.textContent = `${VideoStudio.refs.length}/${spec.max}`;
  VideoStudio.el.refNote.textContent = spec.note;
  const box = VideoStudio.el.refs;
  if (!VideoStudio.refs.length) {
    box.innerHTML = `<div class="d-empty">暂无参考图，点下方按钮添加</div>`;
  } else {
    box.innerHTML = VideoStudio.refs.map((r, i) => `
    <div class="st-ref">
      <img src="${r.url || ("/api/uploads/" + encodeURIComponent(r.name))}" alt="">
      <div class="st-ref-body">
        <span class="st-ref-tag">${escapeHtml(spec.labels[i] || ("图 " + (i + 1)))}</span>
        <div class="st-ref-ops">
          <button class="d-btn sm" data-ref="${i}" data-act="left" ${i === 0 ? "disabled" : ""}>←</button>
          <button class="d-btn sm" data-ref="${i}" data-act="right" ${i === VideoStudio.refs.length - 1 ? "disabled" : ""}>→</button>
          <button class="d-btn sm danger" data-ref="${i}" data-act="del">移除</button>
        </div>
      </div>
    </div>`).join("");
    // 事件委派：在 box 上綁定一個監聽器，處理所有按鈕點擊
    box.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn) return;
      const i = Number(btn.dataset.ref);
      if (btn.dataset.act === "del") VideoStudio.refs.splice(i, 1);
      else if (btn.dataset.act === "left" && i > 0) {
        [VideoStudio.refs[i - 1], VideoStudio.refs[i]] = [VideoStudio.refs[i], VideoStudio.refs[i - 1]];
      }
      else if (btn.dataset.act === "right" && i < VideoStudio.refs.length - 1) {
        [VideoStudio.refs[i + 1], VideoStudio.refs[i]] = [VideoStudio.refs[i], VideoStudio.refs[i + 1]];
      }
      paintVideoRefs();
    });
    console.log("[paintVideoRefs] ✅ 渲染完成，refs长度:", VideoStudio.refs.length);
  }
  console.log("[paintVideoRefs] 完成");
}

function paintVideoPrompt() {
  const m = modeMeta();
  VideoStudio.el.prompt.placeholder = "上传参考图、输入文字或 @主体，描述你想生成的视频。";
  VideoStudio.el.promptHint.innerHTML = escapeHtml(m.hint || "") + (m.ex ? ' <span class="d-empty" style="margin:0">例：' + escapeHtml(m.ex) + "</span>" : "");
}

/* ---------- v2 参数条（画幅） ---------- */
function syncVideoBar() {
  const s = document.getElementById("vsRatio");
  const b = document.getElementById("vsRatioBtn");
  if (b) b.innerHTML = "<b>" + (s && s.value ? s.value : "16:9") + "</b><span class=\"woo-caret\">▾</span>";
}

function openVideoRatio() {
  const s = document.getElementById("vsRatio");
  if (!s) return;
  if (!s.options.length) { toast("当前模型暂无可选画幅", "info"); return; }
  studioShell.ratioModal({
    title: "视频画幅设置",
    ratios: Array.from(s.options).map((o) => o.value),
    cur: { ratio: s.value || "16:9", size: "2K", count: 1 },
    onSubmit: (v) => {
      s.value = v.ratio;
      syncVideoBar();
    },
  });
}

function paintVideoParams() {
  const meta = modelMeta(VideoStudio.model);
  const isFlash = VideoStudio.model === "agnes-video-2.5-flash";
  const secEl = document.getElementById("vsSeconds");
  const durEl = document.getElementById("vsDur");
  const ratioEl = document.getElementById("vsRatio");
  if (isFlash) {
    if (secEl) {
      secEl.style.display = "";
      const kept = secEl.value || "5";
      secEl.innerHTML = (meta.seconds || ["4", "5", "6", "8", "10", "12"]).map((s) =>
        `<option ${String(kept) === s ? "selected" : ""}>${s}</option>`).join("");
    }
    if (durEl) durEl.style.display = "none";
  } else {
    if (durEl) {
      durEl.style.display = "";
      const kept = durEl.value || "";
      durEl.innerHTML = (meta.durations || []).map((d) =>
        `<option value="${d.num_frames}" ${String(kept) === String(d.num_frames) ? "selected" : ""}>${d.label}（${d.num_frames} 帧）</option>`).join("");
    }
    if (secEl) secEl.style.display = "none";
  }
  if (ratioEl) {
    const ratios = isFlash ? (meta.aspect_ratios || ["16:9"]) : (meta.ratios || ["16:9"]);
    const kept = ratioEl.value || "16:9";
    ratioEl.innerHTML = ratios.map((r) =>
      `<option ${String(kept) === r ? "selected" : ""}>${r}</option>`).join("");
  }
  syncVideoBar();
}

/* ---------- 参考图上传 ---------- */
const _UPLOAD_TIMEOUT = 60000; // 60秒超时

async function onVideoRefFiles(files) {
  const spec = refSpec();
  if (!spec) return;

  for (const f of Array.from(files || [])) {
    // 显示上传中状态
    const uploadNote = document.createElement("span");
    uploadNote.className = "d-empty";
    uploadNote.textContent = `上传中: ${f.name} (${(f.size / 1024 / 1024).toFixed(1)}MB)`;
    VideoStudio.el.refNote.appendChild(uploadNote);

    try {
      const fd = new FormData();
      fd.append("file", f);
      
      // 使用 AbortController 设置超时
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), _UPLOAD_TIMEOUT);
      
      const up = await fetch("/api/upload", {
        method: "POST",
        body: fd,
        credentials: "include",
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);

      if (up.status === 401) {
        onUnauthorized();
        return;
      }
      const text = await up.text();
      let ud = {};
      try { ud = JSON.parse(text); } catch(e) { }

      if (!up.ok) {
        throw new Error(ud.error || text || "上传失败");
      }

      VideoStudio.refs.push({ path: ud.path, name: ud.path.split("/").pop() });
      uploadNote.textContent = `✅ ${f.name}`;
      uploadNote.style.color = "#22c55e";
    } catch (e) {
      let errMsg = e.message;
      if (e.name === "AbortError") {
        errMsg = `上传超时（${_UPLOAD_TIMEOUT / 1000}秒），请检查网络后重试`;
      }
      uploadNote.textContent = `❌ ${f.name}: ${errMsg}`;
      uploadNote.style.color = "#ef4444";
      toast("参考图上传失败: " + errMsg, "error");
    } finally {
      // 5秒后移除提示
      setTimeout(() => uploadNote.remove(), 5000);
    }
  }
  paintVideoRefs();
}

/* ---------- 从图片作品库选择参考图 ---------- */
async function openVideoRefPicker() {
  const spec = refSpec();
  if (!spec) return;

  const wrap = document.createElement("div");
  wrap.className = "rag-modal-mask";
  wrap.innerHTML = `
    <div class="rag-modal st-lightbox">
      <div class="rag-modal-hd">
        <h4>从图片作品库选择${escapeHtml(spec.title)}（可选 ${Math.max(0, spec.max - VideoStudio.refs.length)} 张 / 上限 ${spec.max}）</h4>
        <button class="icon-btn rg-modal-x">✕</button>
      </div>
      <div class="rag-modal-bd" id="vsPickerBd">
        <div class="d-empty" style="padding:28px 0;text-align:center">加载中…</div>
      </div>
      <div class="st-pagination" id="vsPickerPag"></div>
      <div class="d-row" style="margin-top:12px;padding:0 16px 16px">
        <button class="d-btn primary" data-ok disabled>＋ 加入选中（0）</button>
        <button class="d-btn" data-close>关闭</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".rg-modal-x").addEventListener("click", close);
  wrap.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));

  const okBtn = wrap.querySelector("[data-ok]");
  const bd = wrap.querySelector("#vsPickerBd");
  const pagEl = wrap.querySelector("#vsPickerPag");
  const picked = new Set();
  const PAGE = 30;

  let allItems = [];
  let page = 1;
  let totalPages = 1;
  let isLoading = false;

  async function loadPage(p) {
    isLoading = true;
    renderPager();
    try {
      const offset = (p - 1) * PAGE;
      console.log("[loadPage] 请求第", p, "页，offset:", offset);
      const r = await apiGet("/api/image/history?limit=" + PAGE + "&offset=" + offset + "&exclude_source=short-drama-director,drama");
      console.log("[loadPage] 响应 total:", r.total, "items.length:", (r.items || []).length);
      allItems = r.items || [];
      totalPages = Math.ceil((r.total || allItems.length) / PAGE);
      page = p;
      renderPage();
    } catch (e) {
      console.error("[loadPage] 错误:", e);
      bd.innerHTML = `<div class="d-empty" style="padding:28px 0;text-align:center;color:#ef4444">加载失败: ${escapeHtml(e.message)}</div>`;
      pagEl.innerHTML = "";
    } finally {
      isLoading = false;
      renderPager();
    }
  }

  function renderPage() {
    // allItems 已经是当前页数据，直接渲染
    if (!allItems.length) {
      bd.innerHTML = `<div class="d-empty" style="padding:28px 0;text-align:center">本页无图片</div>`;
      pagEl.innerHTML = "";
      return;
    }
    bd.innerHTML = `<div class="vs-pick-grid">${allItems.map((it) => `
      <button class="vs-pick-item${picked.has(it.id) ? ' sel' : ''}" data-id="${escapeHtml(it.id)}">
        <img loading="lazy" src="/api/image/file/${encodeURIComponent(it.id)}?thumb=1" alt="">
        <span>${escapeHtml(VIDEO_MODE_LBL[it.mode] || it.mode || "")} · ${escapeHtml(fmtAgo(it.created_at))}</span>
      </button>`).join("")}</div>`;
    bd.querySelectorAll(".vs-pick-item").forEach((btn) => btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      if (picked.has(id)) { picked.delete(id); btn.classList.remove("sel"); }
      else {
        if (VideoStudio.refs.length + picked.size >= spec.max) { toast(`该模式最多 ${spec.max} 张参考图`, "error"); return; }
        picked.add(id); btn.classList.add("sel");
      }
      okBtn.textContent = `＋ 加入选中（${picked.size}）`;
      okBtn.disabled = !picked.size;
    }));
    renderPager();
  }

  function renderPager() {
    if (totalPages <= 1) { pagEl.innerHTML = ""; return; }
    let html = `<span class="st-page-info">${page}/${totalPages}</span>`;
    html += `<button class="st-page-btn"${page <= 1 ? ' disabled' : ''} data-g="p">‹上一页</button>`;
    html += `<input class="st-page-jump" type="number" min="1" max="${totalPages}" value="${page}" placeholder="页码">`;
    html += `<button class="st-page-btn" data-jump>跳转</button>`;
    html += `<button class="st-page-btn"${page >= totalPages ? ' disabled' : ''} data-g="n">下一页›</button>`;
    pagEl.innerHTML = html;
    pagEl.querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", (e) => {
      e.preventDefault();
      if (b.dataset.g === "p") loadPage(Math.max(1, page - 1));
      else if (b.dataset.g === "n") loadPage(Math.min(totalPages, page + 1));
    }));
    const jumpBtn = pagEl.querySelector("[data-jump]");
    const jumpInput = pagEl.querySelector(".st-page-jump");
    if (jumpBtn && jumpInput) {
      jumpBtn.addEventListener("click", () => {
        const n = parseInt(jumpInput.value);
        if (n && n >= 1 && n <= totalPages) loadPage(n);
      });
      jumpInput.addEventListener("keydown", (e) => { if (e.key === "Enter") jumpBtn.click(); });
    }
  }

  okBtn.addEventListener("click", () => {
    let n = 0;
    for (const it of allItems) {
      if (!picked.has(it.id)) continue;
      if (VideoStudio.refs.length >= spec.max) break;
      const name = it.file || (it.id + ".png");
      VideoStudio.refs.push({ path: "image_library/" + name, name, url: "/api/image/file/" + encodeURIComponent(it.id) });
      n++;
    }
    paintVideoRefs();
    toast(`已加入 ${n} 张参考图`, "success");
    close();
  });

  loadPage(1);
}
/* ---------- 生成 ---------- */
const _GENERATE_TIMEOUT = 120000; // 120秒超时（与后端一致）

async function generateVideo() {
  if (VideoStudio.busy) return;
  const prompt = VideoStudio.el.prompt.value.trim();
  if (!prompt) { toast("请先填写提示词", "error"); VideoStudio.el.prompt.focus(); return; }
  const spec = refSpec();
  if (spec && VideoStudio.refs.length < spec.min) { toast(`${spec.title}至少需要 ${spec.min} 张`, "error"); return; }

  const val = (id, d) => { const n = document.getElementById(id); return n ? n.value : d; };
  const isFlash = VideoStudio.model === "agnes-video-2.5-flash";
  const body = {
    model: VideoStudio.model,
    mode: VideoStudio.mode,
    prompt,
    refs: VideoStudio.refs.map((r) => r.path),
    seed: val("vsSeed", ""),
    aspect_ratio: val("vsRatio", "16:9") || "16:9",
  };
  if (isFlash) {
    body.seconds = val("vsSeconds", "5");
  } else {
    const nf = Number(val("vsDur", "121"));
    if (nf) body.num_frames = nf;
  }

  console.log("[generateVideo] 开始提交，body:", body);
  console.log("[generateVideo] 当前URL:", window.location.href);
  console.log("[generateVideo] 网络类型:", navigator.connection ? navigator.connection.effectiveType : "unknown");

  VideoStudio.busy = true;
  VideoStudio.el.gen.disabled = true;
  VideoStudio.el.busy.style.display = "";
  VideoStudio.el.busy.textContent = "⏳ 正在提交任务...";

  const startTime = Date.now();
  let requestDone = false;
  let timedOut = false;

  try {
    // 使用 AbortController 设置超时（仅用于前端，不取消后端处理）
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
      console.warn("[generateVideo] 前端超时，但后端继续处理...");
      timedOut = true;
      controller.abort();
    }, _GENERATE_TIMEOUT);

    console.log("[generateVideo] 发送请求到 /api/video/generate");
    const r = await apiPost("/api/video/generate", body, controller.signal);
    const elapsed = Date.now() - startTime;
    requestDone = true;
    clearTimeout(timeoutId);

    console.log("[generateVideo] 请求完成，耗时:", elapsed, "ms");
    console.log("[generateVideo] 响应:", r);

    if (!r || !r.items || r.items.length === 0) {
      throw new Error("服务器未返回任务信息");
    }

    for (const it of r.items) {
      const i = VideoStudio.gallery.findIndex((g) => g.id === it.id);
      if (i >= 0) VideoStudio.gallery[i] = it; else VideoStudio.gallery.unshift(it);
    }
    if (r.deduped) toast("♻️ 检测到同内容任务进行中，已复用该任务（未重复提交）", "info");
    else if (r.submit_pending) toast("⏳ 提交失败，已排队后台每 60 秒自动重试，直至成功", "info");
    else toast(`✅ 任务已提交（${elapsed}ms），正在生成`, "success");
    paintVideoGallery();
    (r.items || []).forEach((it) => pollVideoTask(it.id));
  } catch (e) {
    const elapsed = Date.now() - startTime;
    console.error("[generateVideo] 请求失败，耗时:", elapsed, "ms", e);

    let errMsg = e.message;
    if (e.name === "AbortError") {
      // 超时但任务可能已在后端处理中，不报错，改为提示用户刷新
      toast("⏳ 任务已提交，后端处理中（网络较慢），请刷新作品库查看进度", "info");
      // 延迟 3 秒后自动轮询，检查是否已创建任务
      setTimeout(() => {
        loadVideoGallery().then(() => resumeVideoPolling());
      }, 3000);
      return;
    } else if (!requestDone) {
      errMsg = `网络错误: ${errMsg}（${elapsed}ms）`;
    }

    toast("❌ 提交失败: " + errMsg, "error");
    VideoStudio.el.busy.textContent = "⚠️ 提交失败，请重试";
  } finally {
    VideoStudio.busy = false;
    VideoStudio.el.gen.disabled = false;
    setTimeout(() => { VideoStudio.el.busy.style.display = "none"; }, 3000);
  }
}

/* ---------- 轮询 ---------- */
function resumeVideoPolling() {
  // 清空旧的轮询器
  Object.keys(VideoStudio.pollers || {}).forEach((id) => {
    clearTimeout(VideoStudio.pollers[id]);
  });
  VideoStudio.pollers = {};
  
  // 重新轮询所有生成中的任务
  const pending = VideoStudio.gallery.filter((it) => !isTerminal(it.status));
  pending.forEach((it) => pollVideoTask(it.id));
}

function isTerminal(s) { return s === "completed" || s === "failed" || s === "cancelled"; }

function pollVideoTask(id) {
  if (!VideoStudio.pollers) VideoStudio.pollers = {};
  if (VideoStudio.pollers[id]) return;
  
  let retryCount = 0;
  let lastSig = "";              // 进度签名：status|progress|attempt|last_heartbeat|error
  let lastChangeAt = Date.now(); // 最近一次有进展的时间
  let slowed = false;            // 长时间无进展 → 降为低频查询
  const maxRetries = 10; // 最多重试 10 次
  
  const tick = async () => {
    try {
      const r = await apiGet("/api/video/task/" + encodeURIComponent(id));
      const it = r.item;
      if (it) {
        const i = VideoStudio.gallery.findIndex((g) => g.id === id);
        if (i >= 0) VideoStudio.gallery[i] = it; else VideoStudio.gallery.unshift(it);
        paintVideoGallery();
        retryCount = 0; // 成功则重置重试计数
        const sig = [it.status, it.progress, it.attempt, it.last_heartbeat, it.error].join("|");
        if (sig !== lastSig) { lastSig = sig; lastChangeAt = Date.now(); }
        
        if (isTerminal(it.status)) {
          clearTimeout(VideoStudio.pollers[id]);
          delete VideoStudio.pollers[id];
          if (it.status === "completed") toast("✅ 视频已生成并入库", "success");
          else if (it.status === "cancelled") toast("⏹ 任务已取消（不再重试）", "info");
          else toast("❌ 生成失败: " + (it.error || ""), "error");
          return;
        }
      }
    } catch (e) {
      retryCount++;
      if (retryCount >= maxRetries) {
        console.error("[pollVideoTask] 达到最大重试次数，放弃轮询:", id, e.message);
        delete VideoStudio.pollers[id];
        // 显示错误提示
        toast("网络异常，请刷新页面查看最新状态", "error");
        return;
      }
      console.warn("[pollVideoTask] 轮询失败，重试", retryCount, "/", maxRetries, ":", e.message);
    }
    
    // 指数退避：前几次快速重试，后面逐渐延长时间；30 分钟无进展则降频续查
    let delay = Math.min(1500 * Math.pow(2, Math.min(retryCount, 3)), 15000);
    if (Date.now() - lastChangeAt > 30 * 60 * 1000) {
      delay = 60000;   // 后端仍在自动重试，降为低频查询、不放弃轮询
      if (!slowed) { slowed = true; toast("🐢 任务长时间无进展（后端仍在自动重试），已切换为低频查询", "info"); }
    }
    VideoStudio.pollers[id] = setTimeout(tick, delay);
  };
  
  // 首次延迟 1.5 秒
  VideoStudio.pollers[id] = setTimeout(tick, 1500);
}

/* ---------- 作品库 ---------- */
async function loadVideoGallery() {
  VideoStudio.page = 1;
  try {
    // 后端上限 _MAX_GALLERY=200，一次取全量；隐藏短剧流程产物，仅展示创作台页面生成内容
    const r = await apiGet("/api/video/history?limit=200&exclude_source=short-drama-director,drama");
    VideoStudio.gallery = r.items || [];
  } catch (e) { /* 保留现有视图 */ }
  paintVideoGallery();
  resumeVideoPolling();
}

function getVsPaginatedItems() {
  const total = VideoStudio.gallery.length;
  if (total <= VideoStudio.perPage) return { items: VideoStudio.gallery, page: 1, totalPages: 1, total };
  const start = (VideoStudio.page - 1) * VideoStudio.perPage;
  const items = VideoStudio.gallery.slice(start, start + VideoStudio.perPage);
  const totalPages = Math.ceil(total / VideoStudio.perPage);
  return { items, page: VideoStudio.page, totalPages, total };
}

function videoStatusBadge(it) {
  if (it.status === "completed") return "";
  if (it.status === "failed") return `<span class="vs-badge failed">失败</span>`;
  if (it.status === "cancelled") return `<span class="vs-badge cancelled">已取消</span>`;
  if (it.submit_pending || !it.video_id) return `<span class="vs-badge doing">⏳ 提交排队中</span>`;
  return `<span class="vs-badge doing">生成中 ${it.progress || 0}%</span>`;
}

function paintVideoGallery() {
  if (!VideoStudio.el) return;
  const { items, page, totalPages, total } = getVsPaginatedItems();
  VideoStudio.el.countLbl.textContent = total ? `共 ${total} 件 · 第 ${page}/${totalPages} 页` : "";
  if (!total) {
    if (VideoStudio.selMode) { VideoStudio.selMode = false; VideoStudio.sel.clear(); }
    paintVsSelBar();
    VideoStudio.el.pagination.style.display = "none";
    VideoStudio.el.grid.innerHTML = `<div class="d-empty" style="padding:36px 0;text-align:center">视频作品库为空 —— 从左侧创作台生成第一条视频吧。</div>`;
    return;
  }
  const selOn = VideoStudio.selMode;
  VideoStudio.el.grid.innerHTML = items.map((it) => {
    const done = it.status === "completed" && it.file;
    const poster = it.thumb ? `/api/video/file/${it.id}?thumb=1` : "";
    const media = done
      ? `<img class="vs-thumb" src="/api/video/file/${it.id}?thumb=1" loading="lazy" alt="">
         <span class="vs-play">▶</span>`
      : `<div class="vs-ph">${videoStatusBadge(it)}<span class="vs-ph-hint">${escapeHtml(it.status === "failed" ? (it.error || "生成失败") : (it.submit_pending || !it.video_id ? (it.error || "提交失败，等待自动重试…") : "视频生成中…"))}</span></div>`;
    const dl = done ? `href="/api/video/file/${it.id}" download="${escapeHtml(it.id)}.mp4"` : `href="#" onclick="return false"`;
    const canCancel = !isTerminal(it.status);
    return `
    <figure class="st-card" data-id="${escapeHtml(it.id)}"${selOn ? ` style="position:relative${VideoStudio.sel.has(it.id) ? ";outline:2px solid #c0392b;outline-offset:-2px" : ""}"` : ""}>
      ${selOn ? `<label class="st-card-cb" style="position:absolute;top:6px;left:6px;z-index:6;background:rgba(0,0,0,0.55);border-radius:4px;padding:2px;line-height:1;cursor:pointer"><input type="checkbox" data-sel="${escapeHtml(it.id)}" ${VideoStudio.sel.has(it.id) ? "checked" : ""} style="width:16px;height:16px;cursor:pointer;accent-color:#c0392b"></label>` : ""}
      <div class="st-card-img vs-card-vid">${media}</div>
      <figcaption class="st-card-meta">
        <span class="st-card-chip">${escapeHtml(modelShort(it.model))}</span>
        <span class="st-card-chip">${escapeHtml(it.size || "")}${it.seconds ? " · " + escapeHtml(it.seconds) + "s" : ""}</span>
        <span class="st-card-time">${escapeHtml(fmtAgo(it.created_at))}</span>
      </figcaption>
      <div class="st-card-prompt">${escapeHtml(truncate(it.prompt || "", 80))}</div>
      <div class="st-card-ops"${selOn ? ' style="display:none"' : ""}>
        <button class="d-btn sm" data-act="open" ${done ? "" : "disabled"}>播放</button>
        <a class="d-btn sm" data-act="dl" ${dl}>下载</a>
        <button class="d-btn sm" data-act="copy">复用提示词</button>
        ${canCancel ? `<button class="d-btn sm danger" data-act="cancel">取消</button>` : ""}
        <button class="d-btn sm danger" data-act="del">删除</button>
      </div>
    </figure>`;
  }).join("");

  // 点击卡片图片区域或播放按钮都能打开灯箱
  const openFromCard = (e) => {
    if (VideoStudio.selMode) return;
    if (e.target.closest("[data-act]")) return;
    const c = e.target.closest(".st-card");
    if (!c) return;
    const item = items.find((g) => g.id === c.dataset.id);
    if (item) openVideoLightbox(item);
  };
  $$(".st-card-img", VideoStudio.el.grid).forEach((img) => {
    img.style.cursor = "zoom-in";
    img.addEventListener("click", openFromCard);
  });
  $$(".vs-play", VideoStudio.el.grid).forEach((btn) => {
    btn.style.cursor = "pointer";
    btn.addEventListener("click", openFromCard);
  });

  $$(".st-card", VideoStudio.el.grid).forEach((card) => {
    const item = items.find((g) => g.id === card.dataset.id);
    if (!item) return;
    if (selOn) {
      // 批量选择模式：点卡片或勾选切换选中
      card.style.cursor = "pointer";
      const syncCardSel = (checked) => {
        if (checked) { card.style.outline = "2px solid #c0392b"; card.style.outlineOffset = "-2px"; }
        else { card.style.outline = ""; card.style.outlineOffset = ""; }
      };
      const toggle = (cb) => {
        if (!cb) return;
        const checked = cb.checked;
        if (checked) VideoStudio.sel.add(cb.dataset.sel); else VideoStudio.sel.delete(cb.dataset.sel);
        syncCardSel(checked);
        paintVsSelBar();
      };
      $$(".st-card-cb input", card).forEach((cb) => cb.addEventListener("click", (e) => {
        e.stopPropagation();
        toggle(cb);
      }));
      card.addEventListener("click", () => {
        const cb = $(".st-card-cb input", card);
        if (!cb) return;
        cb.checked = !cb.checked;
        toggle(cb);
      });
    }
    $$("[data-act]", card).forEach((b) => b.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try {
        if (b.dataset.act === "open") openVideoLightbox(item);
        else if (b.dataset.act === "copy") { await copyText(item.prompt || ""); toast("提示词已复制", "success"); }
        else if (b.dataset.act === "cancel") {
          if (!confirm("确认取消该任务？提交中的任务将停止自动重试。")) return;
          const r = await apiPost("/api/video/cancel", { ids: [item.id] });
          if (!r.ok) throw new Error(r.error || "取消失败");
          toast("⏹ 已取消任务", "info");
          const i = VideoStudio.gallery.findIndex((g) => g.id === item.id);
          if (i >= 0) { VideoStudio.gallery[i].status = "cancelled"; VideoStudio.gallery[i].submit_pending = false; }
          paintVideoGallery();
        }
        else if (b.dataset.act === "del") {
          if (!confirm("从作品库移除该视频（含服务器文件）？")) return;
          const r = await apiPost("/api/video/delete", { ids: [item.id] });
          if (!r.ok) throw new Error(r.error || "删除失败");
          VideoStudio.gallery = VideoStudio.gallery.filter((g) => g.id !== item.id);
          paintVideoGallery();
        }
      } catch (e) { toast("操作失败: " + e.message, "error"); }
    }));
  });

  // 渲染分页控件
  paintVsPagination(totalPages, page);
}

function paintVsPagination(totalPages, currentPage) {
  if (totalPages <= 1) {
    VideoStudio.el.pagination.style.display = "none";
    return;
  }
  VideoStudio.el.pagination.style.display = "flex";
  VideoStudio.el.pagination.innerHTML = `
    <button class="d-btn sm ${currentPage === 1 ? 'disabled' : ''}" data-page="prev" ${currentPage === 1 ? 'disabled' : ''}>‹ 上一页</button>
    <span class="st-page-info">第 ${currentPage} / ${totalPages} 页</span>
    <input type="number" class="st-page-input" min="1" max="${totalPages}" value="${currentPage}" placeholder="页码">
    <button class="d-btn sm" data-page="go">跳转</button>
    <button class="d-btn sm ${currentPage === totalPages ? 'disabled' : ''}" data-page="next" ${currentPage === totalPages ? 'disabled' : ''}>下一页 ›</button>
  `;
  $("[data-page='prev']", VideoStudio.el.pagination).addEventListener("click", () => {
    if (currentPage > 1) { VideoStudio.page--; paintVideoGallery(); }
  });
  $("[data-page='next']", VideoStudio.el.pagination).addEventListener("click", () => {
    if (currentPage < totalPages) { VideoStudio.page++; paintVideoGallery(); }
  });
  $("[data-page='go']", VideoStudio.el.pagination).addEventListener("click", () => {
    const input = $(".st-page-input", VideoStudio.el.pagination);
    const p = parseInt(input.value);
    if (!isNaN(p) && p >= 1 && p <= totalPages && p !== currentPage) {
      VideoStudio.page = p; paintVideoGallery();
    }
  });
  // 回车跳转
  $(".st-page-input", VideoStudio.el.pagination).addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("[data-page='go']", VideoStudio.el.pagination).click();
  });
}

function paintVsSelBar() {
  const n = VideoStudio.sel.size;
  if (VideoStudio.el.selLbl) {
    VideoStudio.el.selLbl.style.display = VideoStudio.selMode ? "" : "none";
    VideoStudio.el.selLbl.textContent = `已选 ${n}`;
  }
  if (VideoStudio.el.selBar) VideoStudio.el.selBar.style.display = VideoStudio.selMode ? "" : "none";
  if (VideoStudio.el.selDel) VideoStudio.el.selDel.disabled = n === 0;
}

function modelShort(id) {
  if (id === "agnes-video-2.5-flash") return "2.5 Flash";
  if (id === "agnes-video-v2.0") return "V2.0";
  return id;
}

function openVideoLightbox(item) {
  const src = "/api/video/file/" + item.id;
  const thumbSrc = item.thumb ? `/api/video/file/${item.id}?thumb=1` : "";
  const wrap = document.createElement("div");
  wrap.className = "rag-modal-mask";
  // 先显示缩略图占位，避免播放器黑屏等待
  wrap.innerHTML = `
    <div class="rag-modal st-lightbox">
      <div class="rag-modal-hd">
        <h4>${escapeHtml(modelShort(item.model))} · ${escapeHtml(item.size || "")}${item.seconds ? " · " + escapeHtml(item.seconds) + "s" : ""}</h4>
        <button class="icon-btn rg-modal-x">✕</button>
      </div>
      <div class="rag-modal-bd" id="vs-lightbox-body">
        <img id="vs-lb-thumb" src="${thumbSrc || src}" style="width:100%;max-height:60vh;object-fit:contain;background:#000;" alt="">
        <p style="text-align:center;color:#888;margin-top:8px;font-size:12px;">加载中，请稍候…</p>
        <pre class="d-pre">${escapeHtml(item.prompt || "")}</pre>
        <div class="d-row">
          <a class="d-btn primary" download="${escapeHtml(item.id)}.mp4" href="${src}">⬇ 下载视频</a>
          <button class="d-btn" data-close>关闭</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);

  const close = () => {
    const v = wrap.querySelector("video");
    if (v) { v.pause(); v.src = ""; }
    wrap.remove();
  };
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".rg-modal-x").addEventListener("click", close);
  wrap.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));

  // 点击缩略图或播放按钮后加载真实视频
  const loadVideo = () => {
    const body = document.getElementById("vs-lightbox-body");
    if (!body || body.querySelector("video")) return; // 已加载
    body.querySelector("img")?.remove();
    const p = body.querySelector("p"); if (p) p.remove();
    const video = document.createElement("video");
    video.className = "st-lb-img";
    video.controls = true;
    video.autoplay = true;
    video.playsinline = true;
    video.style.cssText = "width:100%;max-height:60vh;object-fit:contain;background:#000;";
    video.src = src;
    body.insertBefore(video, body.firstChild);
    video.addEventListener("error", () => {
      video.remove();
      body.innerHTML = `<div class="d-empty" style="padding:28px 0;text-align:center;color:#ef4444">视频加载失败</div>` + body.innerHTML;
    });
  };

  // 点击任意位置触发加载（除了关闭按钮）
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap || e.target.closest("[data-close]") || e.target.classList.contains("rg-modal-x")) return;
    loadVideo();
  });
}
