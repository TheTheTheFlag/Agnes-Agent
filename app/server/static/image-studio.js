/* ============================================================================
   图片创作工作台（Agnes Image 2.5 / 2.1 / 2.0 Flash）
   ============================================================================ */
// 整体包进 IIFE：隔离顶层 const/函数，避免与同页其他模块（video-studio.js 等）
// 在全局词法环境中重名（如 _UPLOAD_TIMEOUT）导致整文件解析失败。
(function () {
window.__imageStudioLoading = true;
console.log("[image-studio.js] 开始执行...");

const IMAGE_MODE_META = {
  txt2img: { i: "✦", t: "文生图", d: "一句话描述 → 生成全新图像", hint: "[主体] + [场景 / 环境] + [风格] + [光照] + [构图] + [质量]",
    ex: "日出时分薄雾峡谷上方的发光浮空城市，电影级写实风格，广角构图，丰富建筑细节，柔和金色光线，高视觉密度" },
  img2img: { i: "✎", t: "图生图", d: "参考 1 张图，按指令编辑/重绘/换风格", hint: "[改什么] + [要保留的元素] + [新风格 / 场景] + [光照 / 构图]",
    ex: "把白天街道改为电影级赛博朋克夜景，添加霓虹招牌与湿滑路面倒影，保留原始街道布局、相机角度和主要建筑形状" },
  multi: { i: "⊕", t: "多图合成", d: "用 ≥2 张参考图组合出全新画面", hint: "[图1 角色] + [图2 角色] + [目标场景] + [图之间关系] + [风格 / 光照 / 构图]",
    ex: "将第一张图作为主要角色，第二张图作为产品参考，生成一张电影级活动海报，保留角色身份与产品外形" },
};

const IMAGE_RATIO_PX = {
  "1:1": "1024×1024", "3:4": "864×1152", "4:3": "1152×864", "16:9": "1312×736",
  "9:16": "736×1312", "2:3": "832×1248", "3:2": "1248×832", "21:9": "1568×672",
};
const IMAGE_STYLE_CHIPS = [
  ["电影写实", "，电影级写实风格，广角构图，柔和自然光，高视觉密度"],
  ["赛博朋克", "，赛博朋克夜景，霓虹招牌，湿滑路面反光，冷青色与品红主调"],
  ["产品摄影", "，商业产品摄影，纯色摄影棚背景，柔和阴影，清晰细节"],
  ["奇幻插画", "，奇幻插画风格，丰富细节，浓郁色彩，梦幻氛围"],
  ["水墨国风", "，传统水墨画风格，留白构图，轻柔晕染，诗意意境"],
  ["3D 渲染", "，高质感 3D 渲染，PBR 材质，柔和全局光照"],
  ["扁平插画", "，扁平矢量插画风格，明快配色，简约几何构图"],
  ["电影海报", "，电影海报式构图，顶部留白区，高对比戏剧光"],
];

const Studio = {
  mode: "txt2img",
  refs: [],           // [{path, name, role}]
  busy: false,
  status: null,
  gallery: [],        // all manifest items (full list)
  page: 1,            // current page
  perPage: 50,        // items per page
  activeTab: false,
  pollers: {},        // id -> timer（生成任务轮询）
  selMode: false,     // 批量删除选择模式
  sel: new Set(),     // 已勾选 id 集合
};

const _UPLOAD_TIMEOUT = 60000;    // 参考图上传超时 60s
const _GENERATE_TIMEOUT = 120000; // 提交请求超时 120s（仅前端，不取消后端处理）

/* ---------- 主渲染 ---------- */
window.renderStudioTab = async function renderStudioTab(el) {
  el.innerHTML = `
    <div class="st">
      <div class="st-head">
        <div class="st-head-title">🎨 图片创作工作台</div>
        <div class="st-head-hint">Agnes Image 2.5 / 2.1 / 2.0 Flash · 文生图 / 图生图 / 多图合成 · 全部档位当前免费</div>
      </div>
      <div id="stKeyBanner" class="st-banner hidden"></div>
      <div class="st-cols">
        <section class="st-left">
          <div class="st-mode-seg" id="stModeSeg"></div>

          <div class="st-block" id="stRefBlock" style="display:none">
            <div class="st-block-hd"><span>参考图</span><span class="st-ref-hint" id="stRefHint"></span></div>
            <div class="st-refs" id="stRefs"><div class="d-empty">暂无参考图</div></div>
            <div class="d-row" style="margin-top:8px">
              <button class="d-btn" id="stAddRef">＋ 添加参考图</button>
              <span class="d-empty" style="margin:0">图生图 1 张 · 多图合成 ≥2 张 · 顺序即"图1 / 图2"</span>
            </div>
            <div class="d-row" id="stRefNoteRow" style="margin-top:6px;flex-wrap:wrap;gap:2px 12px"></div>
            <input type="file" id="stRefFile" accept="image/*" multiple style="display:none">
          </div>

          <div class="st-block">
            <div class="st-block-hd"><span>提示词</span><span class="st-block-tip" id="stPromptHint"></span></div>
            <textarea class="st-prompt" id="stPrompt" rows="4" placeholder=""></textarea>
            <div class="st-chips">
              <span class="st-chip-title">样式速选</span>
              ${IMAGE_STYLE_CHIPS.map(([k]) => `<button class="st-chip" data-style="${k}">${k}</button>`).join("")}
              <button class="st-chip st-chip-clear" data-style="__clear">清空</button>
            </div>
          </div>

          <div class="st-block">
            <div class="st-block-hd"><span>参数</span></div>
            <div class="d-row"><label>模型</label>
              <select class="d-input st-param" id="stModel"></select>
              <span class="st-model-note" id="stModelNote"></span></div>
            <div class="d-row"><label>档位</label>
              <select class="d-input st-param" id="stSize"><option>1K</option><option selected>2K</option><option>3K</option><option>4K</option></select></div>
            <div class="d-row"><label>宽高比</label>
              <select class="d-input st-param" id="stRatio"></select>
              <span class="d-empty" style="margin:0" id="stPx"></span></div>
            <div class="d-row"><label>生成张数</label>
              <select class="d-input st-param" id="stCount"><option>1</option><option>2</option><option>3</option><option>4</option></select>
              <span class="d-empty" style="margin:0">同参数多张，逐个出图</span></div>
          </div>

          <div class="st-actions">
            <button class="d-btn primary st-gen" id="stGen">✦ 生成图像</button>
            <span class="d-empty st-busy" id="stBusy" style="display:none"></span>
          </div>
        </section>

        <section class="st-right">
          <div class="st-gallery-hd">
            <span class="st-gallery-title">🗂 作品库</span>
            <span class="d-empty" style="margin:0" id="stCountLbl"></span>
            <span class="d-empty" style="margin:0 8px;color:#c0392b;display:none" id="stSelLbl"></span>
            <div style="flex:1"></div>
            <span id="stSelBar" style="display:none">
              <button class="d-btn sm" id="stSelAll">全选</button>
              <button class="d-btn sm danger" id="stSelDel" disabled>删除选中</button>
              <button class="d-btn sm" id="stSelCancel">取消</button>
            </span>
            <button class="d-btn sm danger" id="stBatchDel">🗑 批量删除</button>
            <button class="d-btn sm" id="stRefresh">⟳ 刷新</button>
          </div>
          <div id="stGrid" class="st-grid"><div class="d-empty">加载中…</div></div>
          <div id="stPagination" class="st-pagination" style="display:none"></div>
        </section>
      </div>
    </div>`;

  Studio.el = {
    seg: $("#stModeSeg", el), refs: $("#stRefs", el), prompt: $("#stPrompt", el),
    promptHint: $("#stPromptHint", el), refHint: $("#stRefHint", el),
    model: $("#stModel", el), modelNote: $("#stModelNote", el), size: $("#stSize", el),
    ratio: $("#stRatio", el), px: $("#stPx", el), count: $("#stCount", el),
    gen: $("#stGen", el), busy: $("#stBusy", el), grid: $("#stGrid", el),
    countLbl: $("#stCountLbl", el), banner: $("#stKeyBanner", el), refBlock: $("#stRefBlock", el),
    refNote: $("#stRefNoteRow", el),
    pagination: $("#stPagination", el),
    selLbl: $("#stSelLbl", el), selBar: $("#stSelBar", el), selDel: $("#stSelDel", el),
  };

  // —— 状态 ——
  try {
    Studio.status = await apiGet("/api/image/status");
  } catch (e) { Studio.status = null; }
  if (!Studio.status || !Studio.status.configured) {
    Studio.el.banner.classList.remove("hidden");
    Studio.el.banner.innerHTML = `⚠️ 未检测到 agnes 图片 API Key：请在「模型」页接入 agnes 网关（base_url 为 api.agnes-ai.cn）。配置后点右上角「设置」重新进入本页即可。`;
  }

  paintMode();
  paintModelList();
  paintRatioList();
  paintRefs();
  paintPromptPlaceholder();

  // —— 事件 ——
  $("#stAddRef", el).addEventListener("click", () => $("#stRefFile", el).click());
  $("#stRefFile", el).addEventListener("change", (e) => {
    if (e.target.files && e.target.files.length > 0) onRefFiles(e.target.files);
    e.target.value = "";
  });
  $$(".st-chip", el).forEach((c) => c.addEventListener("click", () => {
    const v = Studio.el.prompt.value;
    if (c.dataset.style === "__clear") { Studio.el.prompt.value = ""; }
    else {
      const style = IMAGE_STYLE_CHIPS.find(([k]) => k === c.dataset.style);
      if (style) Studio.el.prompt.value = (v && !v.endsWith("，") && !v.endsWith(",") ? v + "，" : v) + style[1];
    }
    Studio.el.prompt.focus();
  }));
  Studio.el.model.addEventListener("change", paintModelList);
  Studio.el.ratio.addEventListener("change", paintRatioList);
  $("#stRefresh", el).addEventListener("click", () => loadGallery());
  Studio.el.gen.addEventListener("click", generate);

  // —— 批量删除 ——
  $("#stBatchDel", el).addEventListener("click", () => {
    if (Studio.selMode) { Studio.selMode = false; Studio.sel.clear(); }
    else { Studio.selMode = true; }
    paintSelBar();
    paintGallery();
  });
  $("#stSelAll", el).addEventListener("click", () => {
    const { items } = getPaginatedItems();
    items.forEach((i) => Studio.sel.add(i.id));
    paintSelBar();
    paintGallery();
  });
  $("#stSelDel", el).addEventListener("click", async () => {
    const ids = [...Studio.sel];
    if (!ids.length) return;
    if (!confirm(`确认删除选中的 ${ids.length} 件作品？`)) return;
    try {
      const r = await apiPost("/api/image/delete", { ids });
      if (!r.ok) throw new Error(r.error || "删除失败");
      Studio.gallery = Studio.gallery.filter((g) => !Studio.sel.has(g.id));
      Studio.sel.clear();
      Studio.selMode = false;
      paintSelBar();
      paintGallery();
      toast(`已删除 ${r.deleted || ids.length} 件作品`, "success");
    } catch (e) { toast("删除失败: " + e.message, "error"); }
  });
  $("#stSelCancel", el).addEventListener("click", () => {
    Studio.selMode = false; Studio.sel.clear();
    paintSelBar();
    paintGallery();
  });

  await loadGallery();
}

/* ---------- 局部重绘 ---------- */
function paintMode() {
  Studio.el.seg.innerHTML = Object.entries(IMAGE_MODE_META).map(([k, m]) =>
    `<button class="st-mode-btn ${k === Studio.mode ? "active" : ""}" data-mode="${k}">
       <b>${m.i} ${escapeHtml(m.t)}</b><small>${escapeHtml(m.d)}</small></button>`).join("");
  $$(".st-mode-btn", Studio.el.seg).forEach((b) =>
    b.addEventListener("click", () => { Studio.mode = b.dataset.mode; paintMode(); paintPromptPlaceholder(); }));
  Studio.el.refBlock.style.display = Studio.mode === "txt2img" ? "none" : "";
  Studio.el.refHint.textContent = Studio.mode === "img2img" ? "1 张" : "≥2 张";
  paintRefs();
}

function paintModelList() {
  const ms = (Studio.status && Studio.status.models) || [];
  const cur = Studio.el.model.value || (ms[0] && ms[0].id) || "agnes-image-2.5-flash";
  const def = ms[0] && ms[0].id;
  Studio.el.model.innerHTML = ms.map((m) =>
    `<option value="${escapeHtml(m.id)}" ${(m.id === cur || (!cur && m.id === def)) ? "selected" : ""}>${escapeHtml(m.id)}</option>`).join("");
  const sel = ms.find((m) => m.id === Studio.el.model.value);
  Studio.el.modelNote.textContent = sel ? "「" + sel.note + "」" : "";
}

function paintRatioList() {
  const html = Object.keys(IMAGE_RATIO_PX).map((r) =>
    `<option>${r}</option>`).join("");
  const cur = Studio.el.ratio.value;
  Studio.el.ratio.innerHTML = html;
  if (cur) Studio.el.ratio.value = cur;
  Studio.el.px.textContent = "（约 " + IMAGE_RATIO_PX[Studio.el.ratio.value] + " @1K）";
}

function paintPromptPlaceholder() {
  const m = IMAGE_MODE_META[Studio.mode];
  Studio.el.prompt.placeholder = m.ex ? ("示例：" + m.ex) : "";
  Studio.el.promptHint.textContent = m.hint;
}

function paintRefs() {
  const box = Studio.el.refs;
  if (!box) return;
  if (!Studio.refs.length) {
    box.innerHTML = `<div class="d-empty">暂无参考图</div>`;
    return;
  }
  box.innerHTML = Studio.refs.map((r, i) => `
    <div class="st-ref">
      <img src="${r.isLibrary ? '/api/image/file/' : '/api/uploads/'}${encodeURIComponent(r.name)}" alt="">
      <div class="st-ref-body">
        <span class="st-ref-tag">图 ${i + 1}</span>
        <input class="d-input st-role" data-i="${i}" placeholder="角色说明（可选，写进提示词用）" value="${escapeHtml(r.role || "")}">
        <div class="st-ref-ops">
          <button class="d-btn sm" data-ref="${i}" data-act="left" ${i === 0 ? "disabled" : ""}>←</button>
          <button class="d-btn sm" data-ref="${i}" data-act="right" ${i === Studio.refs.length - 1 ? "disabled" : ""}>→</button>
          <button class="d-btn sm danger" data-ref="${i}" data-act="del">移除</button>
        </div>
      </div>
    </div>`).join("");
  $$("button[data-act]", box).forEach((b) => b.addEventListener("click", () => {
    const i = Number(b.dataset.ref);
    if (b.dataset.act === "del") { Studio.refs.splice(i, 1); paintRefs(); }
    else if (b.dataset.act === "left" && i > 0) {
      [Studio.refs[i - 1], Studio.refs[i]] = [Studio.refs[i], Studio.refs[i - 1]]; paintRefs();
    } else if (b.dataset.act === "right" && i < Studio.refs.length - 1) {
      [Studio.refs[i + 1], Studio.refs[i]] = [Studio.refs[i], Studio.refs[i + 1]]; paintRefs();
    }
  }));
  $$(".st-role", box).forEach((inp) => inp.addEventListener("input", () => {
    const i = Number(inp.dataset.i);
    if (Studio.refs[i]) Studio.refs[i].role = inp.value;
  }));
}

async function onRefFiles(files) {
  for (const f of Array.from(files || [])) {
    if (!f.type.startsWith("image/") && !/\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name)) {
      toast("仅支持图片文件: " + f.name, "error"); continue;
    }
    if (f.size > 20 * 1024 * 1024) { toast("图片超过 20MB: " + f.name, "error"); continue; }
    // 与视频创作页一致的上传进度提示：上传中 → ✅/❌，5 秒后移除
    const note = document.createElement("span");
    note.className = "d-empty";
    note.textContent = `上传中: ${f.name} (${(f.size / 1024 / 1024).toFixed(1)}MB)`;
    Studio.el.refNote.appendChild(note);
    try {
      const fd = new FormData(); fd.append("file", f);
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), _UPLOAD_TIMEOUT);
      const up = await fetch("/api/upload", {
        method: "POST", body: fd, credentials: "include", signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (up.status === 401) { onUnauthorized(); return; }
      const text = await up.text();
      let ud = {};
      try { ud = JSON.parse(text); } catch (e) { }
      if (!up.ok) throw new Error(ud.error || text || "上传失败");
      Studio.refs.push({ path: ud.path, name: ud.path.split("/").pop(), role: "" });
      note.textContent = `✅ ${f.name}`;
      note.style.color = "#22c55e";
    } catch (e) {
      let errMsg = e.message;
      if (e.name === "AbortError") errMsg = `上传超时（${_UPLOAD_TIMEOUT / 1000}秒），请检查网络后重试`;
      note.textContent = `❌ ${f.name}: ${errMsg}`;
      note.style.color = "#ef4444";
      toast("参考图上传失败: " + errMsg, "error");
    } finally {
      setTimeout(() => note.remove(), 5000);
    }
  }
  paintRefs();
}

async function generate() {
  if (Studio.busy) return;
  const prompt = Studio.el.prompt.value.trim();
  if (!prompt) { toast("请先填写提示词", "error"); Studio.el.prompt.focus(); return; }
  if (Studio.mode === "img2img" && Studio.refs.length < 1) { toast("图生图需要 1 张参考图", "error"); return; }
  if (Studio.mode === "multi" && Studio.refs.length < 2) { toast("多图合成需要至少 2 张参考图", "error"); return; }

  Studio.busy = true;
  Studio.el.gen.disabled = true;
  Studio.el.busy.style.display = "";
  Studio.el.busy.textContent = "⏳ 正在提交任务...";

  const startTime = Date.now();
  let requestDone = false;
  try {
    // 超时仅用于前端提示，不取消后端处理（任务已异步化，提交即刻入库）
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), _GENERATE_TIMEOUT);
    const r = await apiPost("/api/image/generate", {
      mode: Studio.mode,
      model: Studio.el.model.value,
      prompt,
      size: Studio.el.size.value,
      ratio: Studio.el.ratio.value,
      refs: Studio.refs.map((x) => x.path),
      count: Number(Studio.el.count.value),
    }, controller.signal);
    const elapsed = Date.now() - startTime;
    requestDone = true;
    clearTimeout(timeoutId);

    if (!r || !r.items || r.items.length === 0) throw new Error("服务器未返回任务信息");
    for (const it of r.items) {
      const i = Studio.gallery.findIndex((g) => g.id === it.id);
      if (i >= 0) Studio.gallery[i] = it; else Studio.gallery.unshift(it);
    }
    if (r.deduped) toast("♻️ 检测到同内容任务进行中，已复用该任务（未重复提交）", "info");
    else toast(`✅ 任务已提交（${elapsed}ms），生成中（失败自动重试），完成后入库`, "success");
    paintGallery();
    (r.items || []).forEach((it) => pollImageTask(it.id));
  } catch (e) {
    const elapsed = Date.now() - startTime;
    let errMsg = e.message;
    if (e.name === "AbortError") {
      toast("⏳ 任务已提交，后端处理中（网络较慢），请刷新作品库查看进度", "info");
      setTimeout(() => { loadGallery().then(() => resumeImagePolling()); }, 3000);
      return;
    } else if (!requestDone) {
      errMsg = `网络错误: ${errMsg}（${elapsed}ms）`;
    }
    toast("❌ 提交失败: " + errMsg, "error");
    Studio.el.busy.textContent = "⚠️ 提交失败，请重试";
  } finally {
    Studio.busy = false;
    Studio.el.gen.disabled = false;
    setTimeout(() => { Studio.el.busy.style.display = "none"; }, 3000);
  }
}

/* ---------- 生成任务轮询（与视频创作页同构） ---------- */
function imgTerminal(s) { return !s || s === "completed" || s === "failed" || s === "cancelled"; }

function resumeImagePolling() {
  Object.keys(Studio.pollers || {}).forEach((id) => clearTimeout(Studio.pollers[id]));
  Studio.pollers = {};
  const pending = Studio.gallery.filter((it) => !imgTerminal(it.status));
  pending.forEach((it) => pollImageTask(it.id));
}

function pollImageTask(id) {
  if (!Studio.pollers) Studio.pollers = {};
  if (Studio.pollers[id]) return;
  let retryCount = 0;
  let lastSig = "";              // 进度签名：status|progress|attempt|last_heartbeat|error
  let lastChangeAt = Date.now(); // 最近一次有进展的时间
  let slowed = false;            // 长时间无进展 → 降为低频查询
  const maxRetries = 10;   // 最多重试 10 次；后端 worker 一直在跑，大不了刷新页面
  const tick = async () => {
    try {
      const r = await apiGet("/api/image/task/" + encodeURIComponent(id));
      const it = r.item;
      if (it) {
        const i = Studio.gallery.findIndex((g) => g.id === id);
        if (i >= 0) {
          const prevStatus = Studio.gallery[i].status;
          Studio.gallery[i] = it;
          // 仅在状态变化时整库重绘，避免轮询期反复重建图片节点造成“狂刷新”
          if (prevStatus !== it.status) paintGallery();
        } else {
          Studio.gallery.unshift(it);
          paintGallery();
        }
        retryCount = 0;    // 成功则重置重试计数
        const sig = [it.status, it.progress, it.attempt, it.last_heartbeat, it.error].join("|");
        if (sig !== lastSig) { lastSig = sig; lastChangeAt = Date.now(); }
        if (imgTerminal(it.status)) {
          clearTimeout(Studio.pollers[id]);
          delete Studio.pollers[id];
          if (it.status === "completed") toast("✅ 图片已生成并入库", "success");
          else if (it.status === "cancelled") toast("⏹ 任务已取消（不再重试）", "info");
          else toast("❌ 生成失败: " + (it.error || ""), "error");
          return;
        }
      }
    } catch (e) {
      retryCount++;
      if (retryCount >= maxRetries) {
        console.error("[pollImageTask] 达到最大重试次数，放弃轮询:", id, e.message);
        delete Studio.pollers[id];
        toast("网络异常，请刷新页面查看最新状态", "error");
        return;
      }
      console.warn("[pollImageTask] 轮询失败，重试", retryCount, "/", maxRetries, ":", e.message);
    }
    let delay = Math.min(1500 * Math.pow(2, Math.min(retryCount, 3)), 15000);
    if (Date.now() - lastChangeAt > 30 * 60 * 1000) {
      delay = 60000;   // 30 分钟无进展：后端仍在自动重试，降为低频查询、不放弃轮询
      if (!slowed) { slowed = true; toast("🐢 任务长时间无进展（后端仍在自动重试），已切换为低频查询", "info"); }
    }
    Studio.pollers[id] = setTimeout(tick, delay);
  };
  Studio.pollers[id] = setTimeout(tick, 1500);
}

/* ---------- 作品库 ---------- */
async function loadGallery() {
  Studio.page = 1;
  try {
    const r = await apiGet("/api/image/history?limit=2000");
    Studio.gallery = r.items || [];
  } catch (e) { /* 网络失败时保留现有视图 */ }
  paintGallery();
  resumeImagePolling();
}

const IMAGE_MODE_LBL = { txt2img: "文生图", img2img: "图生图", multi: "多图合成" };

function getPaginatedItems() {
  const total = Studio.gallery.length;
  if (total <= Studio.perPage) return { items: Studio.gallery, page: 1, totalPages: 1, total };
  const start = (Studio.page - 1) * Studio.perPage;
  const items = Studio.gallery.slice(start, start + Studio.perPage);
  const totalPages = Math.ceil(total / Studio.perPage);
  return { items, page: Studio.page, totalPages, total };
}

function paintGallery() {
  const { items, page, totalPages, total } = getPaginatedItems();
  Studio.el.countLbl.textContent = total ? `共 ${total} 件 · 第 ${page}/${totalPages} 页` : "";
  if (!total) {
    if (Studio.selMode) { Studio.selMode = false; Studio.sel.clear(); }
    paintSelBar();
    Studio.el.grid.innerHTML = `<div class="d-empty" style="padding:36px 0;text-align:center">作品库为空 —— 从左侧创作台生成第一张图吧。</div>`;
    Studio.el.pagination.style.display = "none";
    return;
  }
  const selOn = Studio.selMode;
  Studio.el.grid.innerHTML = items.map((it) => {
    const st = it.status || "completed";
    const running = st === "queued" || st === "running";
    const failed = st === "failed";
    const done = st === "completed";
    let body;
    if (running) {
      body = `<div class="st-card-img" style="display:flex;align-items:center;justify-content:center;background:#f4f5f7;min-height:130px;flex-direction:column;gap:6px">
        <span style="font-size:22px">⏳</span>
        <span style="font-size:12px;color:#666">生成中${it.attempt ? " · 尝试 " + it.attempt + " 次" : ""}</span>
        <span style="font-size:11px;color:#999">失败自动重试中…</span></div>`;
    } else if (failed) {
      body = `<div class="st-card-img" style="display:flex;align-items:center;justify-content:center;background:#fdf0f0;min-height:130px;flex-direction:column;gap:6px;padding:8px">
        <span style="font-size:22px">✕</span>
        <span style="font-size:12px;color:#c0392b">生成失败</span>
        <span style="font-size:11px;color:#999;text-align:center">${escapeHtml(truncate(it.error || "", 60))}</span></div>`;
    } else {
      const src = "/api/image/file/" + it.id + (it.thumb ? "?thumb=1" : "");
      body = `<div class="st-card-img"><img loading="lazy" src="${src}" alt=""></div>`;
    }
    return `
    <figure class="st-card" data-id="${escapeHtml(it.id)}"${selOn ? ` style="position:relative${Studio.sel.has(it.id) ? ";outline:2px solid #c0392b;outline-offset:-2px" : ""}"` : ""}>
      ${selOn ? `<label class="st-card-cb" style="position:absolute;top:6px;left:6px;z-index:6;background:rgba(0,0,0,0.55);border-radius:4px;padding:2px;line-height:1;cursor:pointer"><input type="checkbox" data-sel="${escapeHtml(it.id)}" ${Studio.sel.has(it.id) ? "checked" : ""} style="width:16px;height:16px;cursor:pointer;accent-color:#c0392b"></label>` : ""}
      ${body}
      <figcaption class="st-card-meta">
        <span class="st-card-chip">${escapeHtml(IMAGE_MODE_LBL[it.mode] || it.mode)}</span>
        <span class="st-card-chip">${escapeHtml(it.size)}</span>
        <span class="st-card-time">${escapeHtml(fmtAgo(it.created_at))}</span>
      </figcaption>
      <div class="st-card-prompt">${escapeHtml(truncate(it.prompt || "", 80))}</div>
      <div class="st-card-ops"${selOn ? ' style="display:none"' : ""}>
        <button class="d-btn sm" data-act="open" ${done ? "" : "disabled"}>查看</button>
        <button class="d-btn sm" data-act="reuse" ${done ? "" : "disabled"}>用图继续</button>
        <button class="d-btn sm" data-act="copy">复用提示词</button>
        <button class="d-btn sm" data-act="redo">同参重绘</button>
        <button class="d-btn sm danger" data-act="del">删除</button>
      </div>
    </figure>`;
  }).join("");

  const syncCardSel = (card, checked) => {
    if (checked) { card.style.outline = "2px solid #c0392b"; card.style.outlineOffset = "-2px"; }
    else { card.style.outline = ""; card.style.outlineOffset = ""; }
  };
  $$(".st-card", Studio.el.grid).forEach((card) => {
    const item = items.find((g) => g.id === card.dataset.id);
    if (!item) return;
    const done = (item.status || "completed") === "completed";
    if (selOn) {
      // 批量选择模式：点卡片或勾选切换选中
      card.style.cursor = "pointer";
      const toggle = (cb) => {
        if (!cb) return;
        const checked = cb.checked;
        if (checked) Studio.sel.add(cb.dataset.sel); else Studio.sel.delete(cb.dataset.sel);
        syncCardSel(card, checked);
        paintSelBar();
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
    } else {
      // 点击图片区域也能打开灯箱（仅完成的图片可打开）
      const cardImg = $$(".st-card-img", card)[0];
      if (cardImg && done) {
        cardImg.style.cursor = "zoom-in";
        cardImg.addEventListener("click", () => openLightbox(item));
      }
    }
    $$("[data-act]", card).forEach((b) => b.addEventListener("click", async () => {
      try {
        if (b.dataset.act === "open") { if (done) openLightbox(item); }
        else if (b.dataset.act === "copy") {
          await copyText(item.prompt || "");
          toast("提示词已复制", "success");
        } else if (b.dataset.act === "reuse") {
          if (!done) { toast("图片尚未生成完成，无法作为参考图", "error"); return; }
          Studio.mode = Studio.mode === "multi" ? "multi" : "img2img";
          // 使用 item.id（不带扩展名）作为图片ID
          Studio.refs.push({ path: "image_library/" + item.file, name: item.id, role: "", isLibrary: true });
          paintMode(); paintRefs(); paintPromptPlaceholder();
          toast("已作为参考图加入创作台", "success");
        } else if (b.dataset.act === "redo") {
          Studio.el.prompt.value = item.prompt || "";
          Studio.el.size.value = item.size || "2K";
          Studio.el.ratio.value = item.ratio || "1:1";
          Studio.mode = item.mode || "txt2img";
          paintMode(); paintPromptPlaceholder();
          toast("已载入参数，可直接生成", "success");
        } else if (b.dataset.act === "del") {
          if (!confirm("从作品库移除该作品？")) return;
          const r = await apiPost("/api/image/delete", { ids: [item.id] });
          if (!r.ok) throw new Error(r.error || "删除失败");
          Studio.gallery = Studio.gallery.filter((g) => g.id !== item.id);
          paintGallery();
        }
      } catch (e) { toast("操作失败: " + e.message, "error"); }
    }));
  });

  // 渲染分页控件
  paintPagination(totalPages, page);
  paintSelBar();
}

function paintPagination(totalPages, currentPage) {
  if (totalPages <= 1) {
    Studio.el.pagination.style.display = "none";
    return;
  }
  Studio.el.pagination.style.display = "flex";
  Studio.el.pagination.innerHTML = `
    <button class="d-btn sm ${currentPage === 1 ? 'disabled' : ''}" data-page="prev" ${currentPage === 1 ? 'disabled' : ''}>‹ 上一页</button>
    <span class="st-page-info">第 ${currentPage} / ${totalPages} 页</span>
    <input type="number" class="st-page-input" min="1" max="${totalPages}" value="${currentPage}" placeholder="页码">
    <button class="d-btn sm" data-page="go">跳转</button>
    <button class="d-btn sm ${currentPage === totalPages ? 'disabled' : ''}" data-page="next" ${currentPage === totalPages ? 'disabled' : ''}>下一页 ›</button>
  `;
  $("[data-page='prev']", Studio.el.pagination).addEventListener("click", () => {
    if (currentPage > 1) { Studio.page--; paintGallery(); }
  });
  $("[data-page='next']", Studio.el.pagination).addEventListener("click", () => {
    if (currentPage < totalPages) { Studio.page++; paintGallery(); }
  });
  $("[data-page='go']", Studio.el.pagination).addEventListener("click", () => {
    const input = $(".st-page-input", Studio.el.pagination);
    const p = parseInt(input.value);
    if (!isNaN(p) && p >= 1 && p <= totalPages && p !== currentPage) {
      Studio.page = p; paintGallery();
    }
  });
  // 回车跳转
  $(".st-page-input", Studio.el.pagination).addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("[data-page='go']", Studio.el.pagination).click();
  });
}

function paintSelBar() {
  const n = Studio.sel.size;
  if (Studio.el.selLbl) {
    Studio.el.selLbl.style.display = Studio.selMode ? "" : "none";
    Studio.el.selLbl.textContent = `已选 ${n}`;
  }
  if (Studio.el.selBar) Studio.el.selBar.style.display = Studio.selMode ? "" : "none";
  if (Studio.el.selDel) Studio.el.selDel.disabled = n === 0;
}

function openLightbox(item) {
  const raw = "/api/image/file/" + item.id;
  const wrap = document.createElement("div");
  wrap.className = "rag-modal-mask";
  wrap.innerHTML = `
    <div class="rag-modal st-lightbox">
      <div class="rag-modal-hd">
        <h4>${escapeHtml(IMAGE_MODE_LBL[item.mode] || "")} · ${escapeHtml(item.model)} · ${escapeHtml(item.size)} ${item.ratio ? "/ " + escapeHtml(item.ratio) : ""}</h4>
        <button class="icon-btn rg-modal-x">✕</button>
      </div>
      <div class="rag-modal-bd">
        <img class="st-lb-img" src="${raw}" alt="">
        <pre class="d-pre">${escapeHtml(item.prompt || "")}</pre>
        <div class="d-row">
          <a class="d-btn primary" download="${escapeHtml(item.id + (item.file || "png"))}" href="${raw}">⬇ 下载原图</a>
          <button class="d-btn" data-close>关闭</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".rg-modal-x").addEventListener("click", close);
  wrap.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
}
window.__imageStudioLoading = false;
window.__imageStudioReady = typeof renderStudioTab === "function";
console.log("[image-studio.js] 执行完成，renderStudioTab:", typeof renderStudioTab, "ready:", window.__imageStudioReady);
})();