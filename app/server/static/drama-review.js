/* ============================================================================
   短剧审核台（工作台「短剧审核」tab）
   ----------------------------------------------------------------------------
   资产审核：列出项目的 角色/场景/道具 资产（提示词 + 出图），可改提示词重新生成
   （后端 media.py image --ref 锁形象，产物回写 manifest）。
   分镜审核：逐集解析投喂稿，列出每段分镜提示词与已产出视频；可「直接重渲」或
   「修改提示词后重渲」单段；全部确认后「合并成完整视频」（整集 + N集连播）。

   性能约定（2026-09-29）：
   - 列表一律走 ?thumb=1&w=N 缩略图（服务端 JPEG 缓存），不直出 2624×1472 PNG / 整段 mp4
   - 视频 preload="none" + poster 缩略图，点播放才拉流
   - 原图/原视频一律点开灯箱（drvLightbox）才请求
   依赖全局工具：$ / $$ / apiGet / apiPost / escapeHtml / toast。
   ============================================================================ */
"use strict";

/* 独立样式（不污染全局 style.css）；全部走 design token，自动适配明暗主题 */
(function () {
  if (document.getElementById("drvStyle")) return;
  const st = document.createElement("style");
  st.id = "drvStyle";
  st.textContent = `
    .drv {
      --drv-gap: 12px;
      padding: 16px 18px 40px;
      color: var(--text, #e8e9ec);
    }

    /* ---------- 页头 ---------- */
    .drv-head {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      margin-bottom: 14px; padding-bottom: 12px;
      border-bottom: 1px solid var(--border, #2c2f38);
    }
    .drv-head h1 { margin: 0; font-size: 17px; font-weight: 700; letter-spacing: .3px; }
    .drv-head p { margin: 0; font-size: 12px; color: var(--text-faint, #6c7079); }
    .drv-head .woo-type-wrap { margin-left: auto; }

    /* ---------- 主体两栏 ---------- */
    .drv .st-cols {
      display: grid; grid-template-columns: 264px minmax(0, 1fr);
      gap: 16px; align-items: start;
    }
    .drv .st-left { position: sticky; top: 12px; }
    .drv .st-left > .st-block { margin-bottom: 12px; }
    .drv .drv-proj-sel { width: 100%; }
    .drv .d-row { display: flex; gap: 6px; flex-wrap: wrap; }
    .drv-body { padding: 0; min-width: 0; }

    /* ---------- 状态徽章 ---------- */
    .drv-chip {
      display: inline-flex; align-items: center; gap: 4px;
      font-size: 11px; line-height: 1.6; padding: 1px 9px; border-radius: 999px;
      border: 1px solid var(--border, #2c2f38); color: var(--text-soft, #a2a6b0);
      background: var(--bg-input, #23252c); white-space: nowrap;
    }
    .drv-chip.ok   { color: var(--success, #34c77b); background: var(--success-bg, rgba(52,199,123,.12)); border-color: transparent; }
    .drv-chip.wait { color: var(--warning, #f5a524); background: var(--warning-bg, rgba(245,165,36,.14)); border-color: transparent; }
    .drv-chip.meta { color: var(--text-faint, #6c7079); }

    /* ---------- 卡片（对齐 st-card 视觉语言）---------- */
    .drv-card, .drv-seg {
      border: 1px solid var(--border, #2c2f38); border-radius: var(--radius, 12px);
      background: var(--bg-elev, #1e2026); padding: 12px; margin-bottom: 12px;
      transition: border-color .15s, transform .15s, box-shadow .15s;
    }
    .drv-card:hover, .drv-seg:hover {
      border-color: color-mix(in srgb, var(--accent, #7c5cff) 55%, var(--border, #2c2f38));
      box-shadow: 0 6px 22px rgba(0, 0, 0, .22);
    }
    .drv-card-top {
      display: flex; justify-content: space-between; align-items: center;
      gap: 8px; margin-bottom: 10px; font-size: 13px; min-width: 0;
    }
    .drv-card-top b {
      font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .drv-card-top > :last-child { flex: 0 0 auto; }

    /* ---------- 媒体 ---------- */
    .drv-card-img { margin-bottom: 10px; }
    .drv-card-img img, .drv-video video {
      display: block; width: 100%; max-height: 340px; object-fit: contain;
      border-radius: var(--radius-sm, 8px); background: #000; cursor: zoom-in;
    }
    .drv-video video { cursor: default; }

    /* 关键帧：等比网格，卡片化 */
    .drv-kfs {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
      gap: 10px; margin-bottom: 10px;
    }
    .drv-kf {
      border-radius: var(--radius-sm, 8px); overflow: hidden; background: var(--bg-input, #23252c);
      border: 1px solid var(--border, #2c2f38); display: flex; flex-direction: column;
      transition: border-color .15s, transform .15s;
    }
    .drv-kf:hover { border-color: var(--accent, #7c5cff); transform: translateY(-2px); }
    .drv-kf .drv-shot { position: relative; background: #000; aspect-ratio: 16 / 9; }
    .drv-kf img { width: 100%; height: 100%; object-fit: contain; display: block; cursor: zoom-in; }
    .drv-kf img[data-miss] { visibility: hidden; }
    .drv-kf-label {
      font-size: 10.5px; color: var(--text-soft, #a2a6b0); text-align: center;
      padding: 4px 0; background: var(--bg-elev, #1e2026);
      border-top: 1px solid var(--border-soft, #262931);
    }
    .drv-kf[data-miss="1"] .drv-shot::after {
      content: "帧未就绪"; position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      color: var(--text-faint, #6c7079); font-size: 12px;
    }
    .drv-kf-regen {
      width: 100%; font-size: 11px; padding: 5px 0; border: 0;
      border-top: 1px solid var(--border-soft, #262931); border-radius: 0;
      background: var(--bg-hover, #262932); color: var(--text-soft, #a2a6b0);
      cursor: pointer; transition: color .15s, background .15s;
    }
    .drv-kf-regen:hover { background: var(--accent, #7c5cff); color: #fff; }
    .drv-kf-prompt {
      width: 100%; min-height: 70px; font-size: 11px; line-height: 1.4;
      border-radius: 0; border: 0; border-top: 1px solid var(--border-soft, #262931);
      background: var(--bg-input, #23252c); color: var(--text, #e8e9ec);
      resize: vertical; padding: 6px 8px;
    }
    .drv-kf-note {
      font-size: 10px; color: var(--text-faint, #6c7079); text-align: center;
      padding: 6px; background: var(--bg-hover, #262932);
      border-top: 1px solid var(--border-soft, #262931); line-height: 1.4;
    }

    .drv-prompt {
      width: 100%; font-size: 12px; line-height: 1.55; resize: vertical;
      background: var(--bg-input, #23252c); color: var(--text, #e8e9ec);
    }
    .drv-card-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
    .drv-tasklog {
      font-size: 11px; color: var(--text-faint, #6c7079); flex: 1 1 100%;
      word-break: break-all; min-height: 15px;
    }

    /* ---------- 段头 ---------- */
    .drv-segbar {
      display: flex; justify-content: space-between; align-items: center;
      gap: 10px; margin-bottom: 12px; font-weight: 600; font-size: 13px; flex-wrap: wrap;
    }
    .drv-segbar-title { color: var(--text, #e8e9ec); }
    .drv-segs { display: grid; gap: 12px; }

    /* ---------- 灯箱 ---------- */
    .drv-lb {
      position: fixed; inset: 0; z-index: 9999; display: flex;
      align-items: center; justify-content: center; padding: 32px;
      background: var(--mask, rgba(0,0,0,.55)); backdrop-filter: blur(3px);
      animation: drv-lb-in .14s ease-out;
    }
    @keyframes drv-lb-in { from { opacity: 0 } to { opacity: 1 } }
    .drv-lb img, .drv-lb video {
      max-width: 100%; max-height: 100%; border-radius: var(--radius, 12px);
      box-shadow: 0 20px 60px rgba(0, 0, 0, .5); background: #000;
    }
    .drv-lb-x {
      position: absolute; top: 16px; right: 20px; width: 34px; height: 34px;
      border-radius: 50%; border: 1px solid var(--border, #2c2f38);
      background: var(--bg-elev, #1e2026); color: var(--text, #e8e9ec);
      font-size: 15px; cursor: pointer; line-height: 1;
    }
    .drv-lb-x:hover { border-color: var(--danger, #f2555a); color: var(--danger, #f2555a); }
    .drv-lb-cap {
      position: absolute; bottom: 18px; left: 50%; transform: translateX(-50%);
      font-size: 12px; color: var(--text-soft, #a2a6b0);
      background: var(--bg-elev, #1e2026); border: 1px solid var(--border, #2c2f38);
      padding: 4px 12px; border-radius: 999px;
    }

    /* ---------- 骨架屏 ---------- */
    .drv-sk {
      border: 1px solid var(--border, #2c2f38); border-radius: var(--radius, 12px);
      background: var(--bg-elev, #1e2026); padding: 12px; margin-bottom: 12px;
    }
    .drv-sk-bar {
      height: 11px; border-radius: 6px; margin-bottom: 9px;
      background: linear-gradient(90deg, var(--bg-hover, #262932) 25%,
                  color-mix(in srgb, var(--accent, #7c5cff) 18%, var(--bg-hover, #262932)) 37%,
                  var(--bg-hover, #262932) 63%);
      background-size: 400% 100%; animation: drv-sk 1.3s ease-in-out infinite;
    }
    .drv-sk-shot { height: 110px; border-radius: var(--radius-sm, 8px); margin-bottom: 9px; }
    @keyframes drv-sk { 0% { background-position: 100% 50% } 100% { background-position: 0 50% } }

    /* ---------- 窄屏 ---------- */
    @media (max-width: 1100px) {
      .drv .st-cols { grid-template-columns: 1fr; }
      .drv .st-left { position: static; }
    }
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

/* ==================== 媒体 URL / 灯箱 ==================== */
const DRV_THUMB_W = 440;   // 列表缩略图宽度（关键帧栅格较窄，用 360）
const DRV_THUMB_W_SM = 360;

function drvUrl(path, v) {
  return "/api/deliverables/view?path=" + encodeURIComponent(path) + (v ? "&v=" + encodeURIComponent(v) : "");
}
function drvThumbUrl(path, w, v) {
  return drvUrl(path, v) + "&thumb=1&w=" + (w || DRV_THUMB_W);
}

function drvLightbox(src, cap, isVideo) {
  const old = document.getElementById("drvLb");
  if (old) old.remove();
  const lb = document.createElement("div");
  lb.className = "drv-lb";
  lb.id = "drvLb";
  const e = escapeHtml(src);
  lb.innerHTML =
    '<button class="drv-lb-x" title="关闭 (Esc)">✕</button>' +
    (isVideo
      ? '<video src="' + e + '" controls autoplay playsinline></video>'
      : '<img src="' + e + '" alt="">') +
    (cap ? '<div class="drv-lb-cap">' + escapeHtml(cap) + "</div>" : "");
  const close = function () {
    lb.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = function (ev) { if (ev.key === "Escape") close(); };
  lb.addEventListener("click", function (ev) { if (ev.target === lb) close(); });
  lb.querySelector(".drv-lb-x").addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  document.body.appendChild(lb);
  return lb;
}

// 列表内所有「点图看原图 / 点视频看原片」走事件委托，避免逐个绑定
function drvBindLightbox(box) {
  $$("[data-lb]", box).forEach(function (n) {
    n.addEventListener("click", function () {
      drvLightbox(n.dataset.lb, n.dataset.lbcap || "", n.dataset.lbtype === "video");
    });
  });
}

function drvSkeleton(n) {
  let h = "";
  for (let i = 0; i < n; i++) {
    h += '<div class="drv-sk"><div class="drv-sk-bar" style="width:38%"></div>'
      + '<div class="drv-sk-bar drv-sk-shot"></div>'
      + '<div class="drv-sk-bar" style="width:72%"></div></div>';
  }
  return h;
}

/* ==================== 项目选择 ==================== */
async function drvLoadProjects() {
  const sel = $("#drvProjectSel");
  try {
    const data = await apiGet("/api/drama-review/projects");
    const list = data.projects || [];
    sel.innerHTML = '<option value="">— 选择作品 —</option>' + list.map((p) =>
      `<option value="${escapeHtml(p.name)}" ${p.name === DRV.project ? "selected" : ""}>${escapeHtml(p.name)}` +
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
  box.innerHTML = drvSkeleton(3);
  try {
    const data = await apiGet("/api/drama-review/assets?name=" + encodeURIComponent(DRV.project));
    const list = data.assets || [];
    if (!list.length) {
      box.innerHTML = '<div class="d-empty">未找到 assets/manifest.json</div>';
      return;
    }
    const st = DRV.project ? DRV.project + " · " + list.length + " 项资产" : "";
    box.innerHTML = '<div class="drv-segbar"><span class="drv-segbar-title">' + escapeHtml(st) + "</span></div>"
      + list.map((a) => {
        const chip = a.status === "ready"
          ? '<span class="drv-chip ok">✅ ready</span>'
          : '<span class="drv-chip wait">' + escapeHtml(a.status || "未标") + "</span>";
        const img = a.path
          ? `<img loading="lazy" decoding="async" src="${drvThumbUrl(a.path, DRV_THUMB_W, a.md5)}"
               data-lb="${drvUrl(a.path, a.md5)}" data-lbcap="${escapeHtml(a.id)}"
               alt="${escapeHtml(a.id)}" title="点击查看原图">`
          : '<div class="d-empty">无出图</div>';
        return `
      <div class="drv-card" data-id="${escapeHtml(a.id)}">
        <div class="drv-card-top">
          <b>${escapeHtml(a.type)} · ${escapeHtml(a.id)}</b>
          <span class="drv-chip meta" id="drvSt_${escapeHtml(a.id)}">${chip}</span>
        </div>
        <div class="drv-card-img">${img}</div>
        <textarea id="drvP_${escapeHtml(a.id)}" class="d-input drv-prompt" rows="4" spellcheck="false">${escapeHtml(a.prompt || "")}</textarea>
        <div class="drv-card-actions">
          <button class="d-btn sm primary" data-act="regen" data-id="${escapeHtml(a.id)}">🖼 用此提示词重新生成</button>
          <span class="drv-tasklog" id="drvLog_${escapeHtml(a.id)}"></span>
        </div>
      </div>`;
      }).join("");
    $$(".drv-card [data-act=regen]", box).forEach((b) =>
      b.addEventListener("click", () => drvRegenAsset(b.dataset.id)));
    drvBindLightbox(box);
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
  box.innerHTML = drvSkeleton(4);
  try {
    const data = await apiGet(
      "/api/drama-review/storyboard?name=" + encodeURIComponent(DRV.project) + "&ep=" + encodeURIComponent(DRV.ep));
    const segs = data.segments || [];
    // 灯箱看原图时用 cache-bust，避免 max-age 命中旧帧；缩略图由服务端按 mtime 失效，无需 bust
    const vb = Date.now();
    box.innerHTML = `
      <div class="drv-segbar" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px">
        <span class="drv-segbar-title">${escapeHtml(DRV.ep)} · ${segs.length} 段 · ${segs.filter((s) => s.has_video).length} 已出片</span>
        <label class="drv-segsel-all" style="display:inline-flex;align-items:center;gap:4px;cursor:pointer"><input type="checkbox" id="drvSelAll"> 全选</label>
        <button class="d-btn sm" id="drvBatchSelNone">清空勾选</button>
        <button class="d-btn sm primary" id="drvBatchKf">🖼 批量重出勾选段缺帧</button>
        <button class="d-btn sm primary" id="drvBatchRnd">🎬 批量重渲勾选段</button>
        <button class="d-btn sm primary" id="drvBatchMissing">⚡ 一键补齐缺片段</button>
        <button class="d-btn sm primary" id="drvMergeAll">✅ 确认合并本集（${escapeHtml(DRV.ep)}）</button>
        <span class="drv-tasklog" id="drvBatchLog"></span>
      </div>
      <div class="drv-segs">
        ${segs.map((s) => {
          const chip = s.has_video
            ? '<span class="drv-chip ok">✅ 已出片</span>'
            : '<span class="drv-chip wait">⏳ 未出片</span>';
          const v = s.video
            ? `<div class="drv-card-img drv-video">
                 <video controls preload="none"
                   poster="${drvThumbUrl(s.video, DRV_THUMB_W, s.vmtime)}"
                   data-lb="${drvUrl(s.video, s.vmtime)}" data-lbtype="video"
                   data-lbcap="${escapeHtml(s.seg)} · ${escapeHtml(s.title || "")}"
                   src="${drvUrl(s.video, s.vmtime)}"></video>
               </div>`
            : "";
          let kfs = "";
          if (s.kfs && s.kfs.length) {
            kfs = '<div class="drv-card-img drv-kfs">' + s.kfs.map((kf, i) => {
              const isRelay = i === 0 && s.kf1_origin === "relay";
              const tag = i === 0 ? (isRelay ? "首帧 ⬅ 上游末帧" : "首帧") : i === 1 ? "尾帧" : "帧" + (i + 1);
              const fr = "kf" + (i + 1);
              const shot = `<div class="drv-shot">
                  <img loading="lazy" decoding="async"
                    src="${drvThumbUrl(kf, DRV_THUMB_W_SM)}"
                    data-lb="${drvUrl(kf, vb)}" data-lbcap="${escapeHtml(s.seg)} · ${tag}"
                    data-kfi="${i}" alt="${tag}"
                    title="点击查看原图（2624×1472）">
                </div>`;
              if (isRelay) {
                return `<div class="drv-kf" id="drvKf_${encodeURIComponent(s.seg)}_${i}">
              ${shot}
              <span class="drv-kf-label">${tag}</span>
              <div class="drv-kf-note">尾帧接力：渲染时自动抽上段视频末帧</div>
            </div>`;
              }
              const pf = i === 0 ? (s.kf1_prompt || "") : i === 1 ? (s.kf2_prompt || "") : "";
              return `<div class="drv-kf" id="drvKf_${encodeURIComponent(s.seg)}_${i}">
            ${shot}
            <span class="drv-kf-label">${tag}</span>
            <textarea class="d-input drv-kf-prompt" rows="5" spellcheck="false" id="drvKfP_${encodeURIComponent(s.seg)}_${i}">${escapeHtml(pf)}</textarea>
            <button class="d-btn sm drv-kf-regen" data-act="kfreg" data-seg="${escapeHtml(s.seg)}" data-frame="${fr}" data-i="${i}">🔄 改词重出${i === 0 ? "首帧" : "尾帧"}</button>
          </div>`;
            }).join("") + "</div>";
          } else if (!s.has_video) {
            kfs = '<div class="drv-card-img"><div class="d-empty">缺关键帧</div></div>';
          }
          return `
        <div class="drv-seg" data-seg="${escapeHtml(s.seg)}">
          <div class="drv-card-top">
            <label class="drv-segsel" title="勾选后可批量重出/重渲"><input type="checkbox" class="drv-segsel-box" data-seg="${escapeHtml(s.seg)}"></label>
            <b>${escapeHtml(s.seg)} · ${escapeHtml(s.title || "")}</b>
            <span class="drv-chip meta">${s.duration}s ${chip}</span>
          </div>
          ${v}
          ${kfs}
          <textarea id="drvSegP_${escapeHtml(s.seg)}" class="d-input drv-prompt" rows="4" spellcheck="false">${escapeHtml(s.prompt || "")}</textarea>
          <div class="drv-card-actions">
            <button class="d-btn sm" data-act="rerender" data-seg="${escapeHtml(s.seg)}">🎬 直接重渲此段</button>
            <button class="d-btn sm primary" data-act="rerender2" data-seg="${escapeHtml(s.seg)}">✏ 按上方提示词重渲</button>
            <button class="d-btn sm warn" data-act="cascade" data-seg="${escapeHtml(s.seg)}">⛓ 自此段级联重渲（到尾）</button>
            <span class="drv-tasklog" id="drvSegLog_${escapeHtml(s.seg)}"></span>
          </div>
        </div>`;
        }).join("")}
      </div>`;
    $$("[data-act=rerender]", box).forEach((b) =>
      b.addEventListener("click", () => drvRerender(b.dataset.seg, "")));
    $$("[data-act=rerender2]", box).forEach((b) =>
      b.addEventListener("click", () => drvRerender(b.dataset.seg, "use_prompt")));
    $$("[data-act=kfreg]", box).forEach((b) =>
      b.addEventListener("click", () => drvKfRegen(b.dataset.seg, b.dataset.frame, b.dataset.i)));
    $$("[data-act=cascade]", box).forEach((b) =>
      b.addEventListener("click", () => drvCascade(b.dataset.seg)));
    const selAll = $("#drvSelAll", box);
    if (selAll) {
      selAll.addEventListener("change", () =>
        $$(".drv-segsel-box", box).forEach((c) => { c.checked = selAll.checked; }));
    }
    const none = $("#drvBatchSelNone", box);
    if (none) {
      none.addEventListener("click", () => {
        $$(".drv-segsel-box", box).forEach((c) => { c.checked = false; });
        if (selAll) selAll.checked = false;
      });
    }
    const bkf = $("#drvBatchKf", box);
    if (bkf) bkf.addEventListener("click", () => drvBatchKeyframes());
    const brnd = $("#drvBatchRnd", box);
    if (brnd) brnd.addEventListener("click", () => drvBatchRender());
    const bmiss = $("#drvBatchMissing", box);
    if (bmiss) bmiss.addEventListener("click", () => drvBatchMissing());
    $("#drvMergeAll", box).addEventListener("click", () => drvMerge([DRV.ep]));
    drvBindLightbox(box);
  } catch (e) {
    box.innerHTML = `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

/* ==================== 批量操作（一次全量提交，不设并发硬上限） ==================== */
function drvSelectedSegs() {
  return $$(".drv-segsel-box").filter((c) => c.checked).map((c) => c.dataset.seg);
}

function drvMissingVideoSegs() {
  return $$(".drv-seg").filter((el) => {
    const seg = el.dataset.seg;
    return !(el.querySelector(".drv-video"));
  }).map((el) => el.dataset.seg);
}

function drvMissingKfSegs() {
  return $$(".drv-seg").filter((el) => {
    const n = el.querySelectorAll(".drv-kfs .drv-kf").length;
    return n < 2;
  }).map((el) => el.dataset.seg);
}

async function drvBatchKeyframes() {
  const sel = drvSelectedSegs();
  if (!sel.length) { toast("请先勾选要批量重出帧的段", "warn"); return; }
  if (!confirm(`批量重出 ${sel.length} 段的关键帧（kf1+kf2，一次全量提交）？\n` +
    `注意：换帧只更新静态图，已出片段需另做重渲才生效。`)) return;
  try {
    const r = await apiPost("/api/drama-review/keyframes/batch", {
      name: DRV.project, ep: DRV.ep, segs: sel, frames: ["kf1", "kf2"], workers: 0 });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    if (!r.task_id) { toast(r.msg || "无需出帧（筛选后为空）", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "batch", id: "Batch", cb: drvRenderSegments });
    toast(`已启动批量出帧：${r.jobs} 张，并发 ${r.workers}`, "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvBatchRender() {
  const sel = drvSelectedSegs();
  if (!sel.length) { toast("请先勾选要批量重渲的段", "warn"); return; }
  if (!confirm(`批量重渲 ${sel.length} 段？（一次全量提交，各段互不依赖）`)) return;
  try {
    const r = await apiPost("/api/drama-review/segments/batch", {
      name: DRV.project, segs: sel, workers: 0 });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "batch", id: "Batch", cb: drvRenderSegments });
    toast(`已启动批量渲影：${r.jobs} 段，并发 ${r.workers}`, "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvBatchMissing() {
  const sel = drvMissingVideoSegs();
  if (!sel.length) { toast("本集已全部出片，无需补齐", "ok"); return; }
  if (!confirm(`一键补齐本集 ${sel.length} 个缺片段：\n${sel.join(", ")}\n` +
    `（一次全量提交）`)) return;
  try {
    const r = await apiPost("/api/drama-review/segments/batch", {
      name: DRV.project, segs: sel, workers: 0 });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, { kind: "batch", id: "Batch", cb: drvRenderSegments });
    toast(`已启动补齐 ${r.jobs} 段，并发 ${r.workers}`, "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
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
      const target = meta.kind === "asset" ? `#drvLog_${meta.id}`
        : meta.kind === "batch" ? "#drvBatchLog"
        : `#drvSegLog_${meta.id}`;
      const el = $(target);
      if (el) {
        const last = (t.log || []).slice(-1)[0] || "";
        el.textContent = (t.status === "running" ? "⏳" : t.status === "done" ? "✅" : t.status === "failed" ? "❌" : "") + " " + last;
        el.title = (t.log || []).slice(-30).join("\n");
      }
      if (t.status === "done" || t.status === "failed") {
        if (meta.kind === "batch") {
          // 重渲染会抹掉日志区，先把汇总行弹出来，否则用户看不到结果
          const sum = (t.log || []).slice().reverse()
            .find((l) => l.includes("结束")) || (t.log || []).slice(-1)[0] || "";
          toast(sum || "批量任务结束", t.status === "done" ? "ok" : "warn");
        }
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
    <div class="st drv woo woo-review">
      <div class="drv-head">
        <div>
          <h1>🎬 短剧审核台</h1>
          <p>资产审核 · 分镜审核 · 重渲 / 改词重渲 · 合并成片 · 全程保留模型自带配音</p>
        </div>
        ${studioShell.typeButton("drvType", "review")}
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
  studioShell.bindTypeButton("drvType", "review");
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
