/* ============================================================================
   图片创作工作台（Agnes Image 2.5 / 2.1 / 2.0 Flash）
   ----------------------------------------------------------------------------
   三模式创作：文生图 / 图生图 / 多图合成。
   左侧创作台：模式切换 · 参考图（含角色标注）· 提示词（样式速选）· 参数（模型/档位/比例/张数）
   右侧作品库：画廊网格 + 大图灯箱，支持下载 / 用图继续 / 复用提示词 / 同参重绘 / 删除。
   所有数据走 /api/image/*（受登录保护），密钥在服务端解析，前端见不到明文。
   依赖 app.js 全局工具：$ / $$ / apiGet / apiPost / escapeHtml / truncate / fmtAgo /
   toast / drawerSection / drawerErr / State。
   ============================================================================ */

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
  gallery: [],        // manifest items
  activeTab: false,
};

/* ---------- 主渲染 ---------- */
async function renderStudioTab(el) {
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
            <div style="flex:1"></div>
            <button class="d-btn sm" id="stRefresh">⟳ 刷新</button>
          </div>
          <div id="stGrid" class="st-grid"><div class="d-empty">加载中…</div></div>
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
  $$(".st-mode-btn", Studio.el.seg).forEach((b) =>
    b.addEventListener("click", () => { Studio.mode = b.dataset.mode; paintMode(); paintPromptPlaceholder(); }));
  $("#stAddRef", el).addEventListener("click", () => $("#stRefFile", el).click());
  $("#stRefFile", el).addEventListener("change", (e) => onRefFiles(e.target.files));
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

  await loadGallery();
}

/* ---------- 局部重绘 ---------- */
function paintMode() {
  Studio.el.seg.innerHTML = Object.entries(IMAGE_MODE_META).map(([k, m]) =>
    `<button class="st-mode-btn ${k === Studio.mode ? "active" : ""}" data-mode="${k}">
       <b>${m.i} ${escapeHtml(m.t)}</b><small>${escapeHtml(m.d)}</small></button>`).join("");
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
      <img src="/api/uploads/${encodeURIComponent(r.name)}" alt="">
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
    try {
      const fd = new FormData(); fd.append("file", f);
      const up = await fetch("/api/upload", { method: "POST", body: fd });
      if (up.status === 401) return onUnauthorized();
      const ud = await up.json().catch(() => ({}));
      if (!up.ok) throw new Error(ud.error || "上传失败");
      Studio.refs.push({ path: ud.path, name: ud.path.split("/").pop(), role: "" });
    } catch (e) { toast("参考图上传失败: " + e.message, "error"); }
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
  Studio.el.busy.textContent = "⏳ 生成中…（每张约 10–60 秒，请耐心等待）";
  try {
    const r = await apiPost("/api/image/generate", {
      mode: Studio.mode,
      model: Studio.el.model.value,
      prompt,
      size: Studio.el.size.value,
      ratio: Studio.el.ratio.value,
      refs: Studio.refs.map((x) => x.path),
      count: Number(Studio.el.count.value),
    });
    if (!r.ok) throw new Error(r.error || "生成失败");
    for (const it of r.items) Studio.gallery.unshift(it);
    toast(`已生成 ${r.items.length} 张`, "success");
  } catch (e) {
    toast("生成失败: " + e.message, "error");
  } finally {
    Studio.busy = false;
    Studio.el.gen.disabled = false;
    Studio.el.busy.style.display = "none";
    paintGallery();
  }
}

/* ---------- 作品库 ---------- */
async function loadGallery() {
  try {
    const r = await apiGet("/api/image/history?limit=120");
    Studio.gallery = r.items || [];
  } catch (e) { /* 网络失败时保留现有视图 */ }
  paintGallery();
}

const IMAGE_MODE_LBL = { txt2img: "文生图", img2img: "图生图", multi: "多图合成" };

function paintGallery() {
  Studio.el.countLbl.textContent = Studio.gallery.length ? `共 ${Studio.gallery.length} 件` : "";
  if (!Studio.gallery.length) {
    Studio.el.grid.innerHTML = `<div class="d-empty" style="padding:36px 0;text-align:center">作品库为空 —— 从左侧创作台生成第一张图吧。</div>`;
    return;
  }
  Studio.el.grid.innerHTML = Studio.gallery.map((it) => {
    const src = "/api/image/file/" + it.id + (it.thumb ? "?thumb=1" : "");
    return `
    <figure class="st-card" data-id="${escapeHtml(it.id)}">
      <div class="st-card-img"><img loading="lazy" src="${src}" alt=""></div>
      <figcaption class="st-card-meta">
        <span class="st-card-chip">${escapeHtml(IMAGE_MODE_LBL[it.mode] || it.mode)}</span>
        <span class="st-card-chip">${escapeHtml(it.size)}</span>
        <span class="st-card-time">${escapeHtml(fmtAgo(it.created_at))}</span>
      </figcaption>
      <div class="st-card-prompt">${escapeHtml(truncate(it.prompt || "", 80))}</div>
      <div class="st-card-ops">
        <button class="d-btn sm" data-act="open">查看</button>
        <button class="d-btn sm" data-act="reuse">用图继续</button>
        <button class="d-btn sm" data-act="copy">复用提示词</button>
        <button class="d-btn sm" data-act="redo">同参重绘</button>
        <button class="d-btn sm danger" data-act="del">删除</button>
      </div>
    </figure>`;
  }).join("");

  $$(".st-card", Studio.el.grid).forEach((card) => {
    const item = Studio.gallery.find((g) => g.id === card.dataset.id);
    if (!item) return;
    $$("button[data-act]", card).forEach((b) => b.addEventListener("click", async () => {
      try {
        if (b.dataset.act === "open") openLightbox(item);
        else if (b.dataset.act === "copy") {
          await navigator.clipboard.writeText(item.prompt || "");
          toast("提示词已复制", "success");
        } else if (b.dataset.act === "reuse") {
          Studio.mode = Studio.mode === "multi" ? "multi" : "img2img";
          Studio.refs.push({ path: "image_library/" + item.file, name: item.file, role: "" });
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