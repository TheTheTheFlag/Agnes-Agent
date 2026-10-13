/* ============================================================================
   短剧审核台（工作台「短剧审核」tab）v2
   ----------------------------------------------------------------------------
   剧本审核：列出项目剧本文档，Markdown 阅读器（字体可调）。
   资产审核：按 角色/道具/场景/怪物 分类展示，可改提示词重新生成；
             每个资产可自定义上传替换图片，支持新增/移除资产。
   分镜审核：每段左侧显示「绘制本段视频所用参考图」（≤5 张：kf_plan refs + kf2
             构图图），支持上传/替换/移除；右侧显示视频与提示词，可重渲/级联。

   性能约定（2026-09-29）：
   - 列表一律走 ?thumb=1&w=N 缩略图（服务端 JPEG 缓存，按源 mtime 自动失效）
   - 视频 preload="none" + poster 缩略图，点播放才拉流
   - 原图/原视频一律点开灯箱（drvLightbox）才请求
   依赖全局工具：$ / $$ / apiGet / apiPost / escapeHtml / toast。
   ============================================================================ */
"use strict";

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

    .drv .st-cols {
      display: grid; grid-template-columns: 264px minmax(0, 1fr);
      gap: 16px; align-items: start;
    }
    .drv .st-cols > * { min-width: 0; }
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

    /* ---------- 卡片 ---------- */
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
    .drv-card-img { margin-bottom: 10px; }
    .drv-card-img img, .drv-video video {
      display: block; width: 100%; max-height: 340px; object-fit: contain;
      border-radius: var(--radius-sm, 8px); background: #000; cursor: zoom-in;
    }
    .drv-video video { cursor: default; }
    .drv-prompt {
      width: 100%; font-size: 12px; line-height: 1.55; resize: vertical;
      background: var(--bg-input, #23252c); color: var(--text, #e8e9ec);
    }
    .drv-card-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
    .drv-tasklog { font-size: 11px; color: var(--text-faint, #6c7079); flex: 1 1 100%; word-break: break-all; min-height: 15px; }

    /* ---------- 资产分类标签 ---------- */
    .drv-cats { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
    .drv-cat {
      border: 1px solid var(--border, #2c2f38); background: var(--bg-input, #23252c);
      color: var(--text-soft, #a2a6b0); border-radius: 999px; padding: 5px 14px;
      font-size: 12px; cursor: pointer; transition: all .15s;
    }
    .drv-cat:hover { border-color: var(--accent, #7c5cff); color: var(--text, #e8e9ec); }
    .drv-cat.on { background: var(--accent, #7c5cff); border-color: transparent; color: #fff; }
    .drv-cat .count { opacity: .72; margin-left: 3px; font-size: 11px; }

    /* ---------- 新增资产表单 ---------- */
    .drv-addbox {
      display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
      border: 1px dashed var(--border, #2c2f38); border-radius: var(--radius-sm, 8px);
      padding: 10px 12px; margin-bottom: 12px; background: var(--bg-input, #23252c);
    }
    .drv-addbox .d-input { width: auto; min-width: 120px; }
    .drv-file-name { font-size: 11px; color: var(--text-faint, #6c7079); max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    /* ---------- 段：左图右视频 ---------- */
    .drv-segbar {
      display: flex; justify-content: space-between; align-items: center;
      gap: 10px; margin-bottom: 12px; font-weight: 600; font-size: 13px; flex-wrap: wrap;
    }
    .drv-segbar-title { color: var(--text, #e8e9ec); }
    .drv-segs { display: grid; gap: 12px; }
    .drv-seg-body { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; }
    .drv-seg-left { min-width: 0; }
    .drv-seg-left .drv-fr-hd { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 8px; }
    .drv-seg-right { min-width: 0; }

    /* ---------- 参考图网格（≤5 张） ---------- */
    .drv-fr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 8px; }
    .drv-fr {
      border: 1px solid var(--border, #2c2f38); border-radius: 10px; overflow: hidden;
      background: var(--bg-input, #23252c); display: flex; flex-direction: column;
    }
    .drv-fr.is-kf2 { border-color: color-mix(in srgb, var(--accent, #7c5cff) 60%, var(--border, #2c2f38)); }
    .drv-fr-shot { position: relative; aspect-ratio: 1 / 1; background: #000; }
    .drv-fr-shot img { width: 100%; height: 100%; object-fit: cover; display: block; cursor: zoom-in; }
    .drv-fr-shot .drv-fr-miss { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 10px; color: var(--text-faint, #6c7079); text-align: center; padding: 0 6px; }
    .drv-fr-idx {
      position: absolute; top: 6px; left: 6px; width: 18px; height: 18px; border-radius: 50%;
      background: rgba(0, 0, 0, .62); color: #fff; font-size: 10px; line-height: 18px;
      text-align: center; font-weight: 600;
    }
    .drv-fr-info { padding: 6px 8px 2px; min-height: 34px; }
    .drv-fr-label { font-size: 11px; color: var(--text, #e8e9ec); line-height: 1.35; word-break: break-all; }
    .drv-fr-role { font-size: 10px; color: var(--text-faint, #6c7079); line-height: 1.3; margin-top: 2px; }
    .drv-fr-ops { display: flex; gap: 4px; padding: 6px 8px; flex-wrap: wrap; }
    .drv-fr-ops .d-btn { font-size: 10px; padding: 3px 7px; }
    .drv-fr-add {
      border: 1px dashed var(--border, #2c2f38); border-radius: 10px; min-height: 92px;
      display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 4px;
      color: var(--text-faint, #6c7079); cursor: pointer; font-size: 12px;
      background: color-mix(in srgb, var(--bg-input, #23252c) 60%, transparent);
      transition: all .15s;
    }
    .drv-fr-add:hover { border-color: var(--accent, #7c5cff); color: var(--accent, #7c5cff); }
    .drv-fr-note { font-size: 10px; color: var(--text-faint, #6c7079); margin-top: 8px; line-height: 1.5; }

    /* ---------- 剧本审核 ---------- */
    .drv-script { display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 14px; align-items: start; }
    .drv-script-docs { display: flex; flex-direction: column; gap: 6px; position: sticky; top: 12px; }
    .drv-script-docs .d-btn { text-align: left; }
    .drv-script-reader { min-width: 0; }
    .drv-script-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
    .drv-script-toolbar b { font-size: 13px; }
    .drv-script-art {
      border: 1px solid var(--border, #2c2f38); border-radius: var(--radius, 12px);
      background: var(--bg-elev, #1e2026); padding: 18px 22px; font-size: 14px;
      line-height: 1.9; color: var(--text, #e8e9ec); overflow-wrap: break-word;
    }
    .drv-script-art h1 { font-size: 1.5em; margin: .9em 0 .5em; line-height: 1.4; border-bottom: 1px solid var(--border, #2c2f38); padding-bottom: .25em; }
    .drv-script-art h2 { font-size: 1.28em; margin: .9em 0 .4em; line-height: 1.4; }
    .drv-script-art h3 { font-size: 1.12em; margin: .8em 0 .4em; }
    .drv-script-art p { margin: .5em 0; }
    .drv-script-art table { border-collapse: collapse; margin: .7em 0; width: 100%; }
    .drv-script-art th, .drv-script-art td { border: 1px solid var(--border, #2c2f38); padding: 5px 9px; font-size: .95em; }
    .drv-script-art th { background: var(--bg-hover, #262932); }
    .drv-script-art code { background: var(--bg-hover, #262932); padding: .15em .4em; border-radius: 5px; font-size: .92em; }
    .drv-script-art pre { background: var(--bg-hover, #262932); padding: 10px 12px; border-radius: 8px; overflow: auto; }
    .drv-script-art blockquote { border-left: 3px solid var(--accent, #7c5cff); margin: .7em 0; padding: .3em .9em; color: var(--text-soft, #a2a6b0); background: var(--bg-input, #23252c); border-radius: 4px; }
    .drv-script-art hr { border: 0; border-top: 1px solid var(--border, #2c2f38); margin: 1em 0; }
    .drv-script-art img { max-width: 100%; border-radius: 8px; }

    /* ---------- 弹窗 ---------- */
    .drv-modal {
      position: fixed; inset: 0; z-index: 9998; display: flex; align-items: center; justify-content: center;
      background: var(--mask, rgba(0,0,0,.55)); backdrop-filter: blur(3px);
    }
    .drv-modal-box { width: min(600px, 92vw); background: var(--bg-elev, #1e2026); border: 1px solid var(--border, #2c2f38); border-radius: var(--radius, 12px); padding: 14px; }
    .drv-modal-box h4 { margin: 0 0 8px; font-size: 13px; }
    .drv-modal-box textarea { width: 100%; min-height: 130px; }
    .drv-modal-box .drv-pd-prev { margin: 0 0 8px; border: 1px solid var(--border, #2c2f38); border-radius: var(--radius-sm, 8px); overflow: hidden; background: #000; }
    .drv-modal-box .drv-pd-prev img { display: block; width: 100%; max-height: 240px; object-fit: contain; }
    .drv-modal-ops { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; }

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

    @media (max-width: 1250px) {
      .drv .st-cols { grid-template-columns: minmax(0, 1fr); }
      .drv .st-left { position: static; }
      .drv-seg-body { grid-template-columns: 1fr; }
      .drv-script { grid-template-columns: 1fr; }
      .drv-script-docs { position: static; }
      .drv-dock { width: min(320px, 88vw); }
    }

    /* 任务进度浮窗（固定右下，跨滚动/模式常驻） */
    .drv-dock { position: fixed; right: 18px; bottom: 18px; width: 340px;
      max-height: 48vh; overflow: auto; z-index: 950; display: flex;
      flex-direction: column; gap: 8px; }
    .drv-dock[hidden] { display: none; }
    .drv-dock-card { background: rgba(20,21,26,.96); border: 1px solid var(--line,#33363f);
      border-radius: 10px; padding: 8px 10px; box-shadow: 0 10px 30px rgba(0,0,0,.45);
      cursor: pointer; }
    .drv-dock-hd { display: flex; align-items: center; gap: 6px; font-size: 12px;
      color: var(--text,#e8e9ec); }
    .drv-dock-name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; }
    .drv-dock-pct { color: var(--text-faint,#6c7079); font-variant-numeric: tabular-nums; }
    .drv-dock-dur { color: var(--text-faint,#6c7079); font-variant-numeric: tabular-nums; }
    .drv-dock-bar { height: 5px; border-radius: 3px; background: rgba(255,255,255,.1);
      margin: 6px 0 4px; overflow: hidden; }
    .drv-dock-bar > i { display: block; height: 100%; background: var(--accent,#5b8cff);
      transition: width .35s ease; }
    .drv-dock-bar > i.is-indet { width: 35%; animation: drvIndet 1.1s ease-in-out infinite; }
    @keyframes drvIndet { 0% { margin-left: -35%; } 100% { margin-left: 100%; } }
    .drv-dock-log { font-size: 11px; color: var(--text-faint,#6c7079); line-height: 1.35;
      word-break: break-all; max-height: 30px; overflow: hidden; }
    .drv-dock-card.is-done .drv-dock-name { color: #7ddc8f; }
    .drv-dock-card.is-failed .drv-dock-name { color: #ff7b7b; }

    /* ---------- 批量渲影内联进度面板（创作台式：提交成功即内联显示 + 轮询） ---------- */
    .drv-bp {
      border: 1px solid var(--border,#2c2f38); border-radius: var(--radius,12px);
      background: var(--bg-elev,#1e2026); padding: 10px 12px; margin-bottom: 12px;
      box-shadow: 0 6px 22px rgba(0,0,0,.22);
      box-sizing: border-box; max-width: 100%; min-width: 0; overflow: hidden;
    }
    .drv-bp[hidden] { display: none; }
    .drv-bp-hd { display: flex; align-items: center; gap: 8px 10px; flex-wrap: wrap; font-size: 12px; min-width: 0; }
    .drv-bp-title { font-weight: 700; color: var(--text,#e8e9ec); min-width: 0; overflow-wrap: anywhere; }
    .drv-bp-meta, .drv-bp-pct { color: var(--text-faint,#6c7079); font-variant-numeric: tabular-nums; }
    .drv-bp-x { margin-left: auto; border: 1px solid var(--border,#2c2f38); border-radius: 6px;
      background: transparent; color: var(--text-faint,#6c7079); cursor: pointer; line-height: 1;
      padding: 4px 9px; font-size: 12px; }
    .drv-bp-x:hover { color: var(--danger,#f2555a); border-color: var(--danger,#f2555a); }
    .drv-bp-x.cancel { color: #e3b341; border-color: rgba(227,179,65,.4); }
    .drv-bp-x.cancel:hover { color: #f2c94c; border-color: #f2c94c; background: rgba(227,179,65,.08); }
    .drv-bp-bar { height: 5px; border-radius: 3px; background: rgba(255,255,255,.1);
      margin: 8px 0 8px; overflow: hidden; }
    .drv-bp-bar.big { height: 8px; }
    .drv-bp-bar > i { display: block; height: 100%; width: 0; background: var(--accent,#5b8cff);
      transition: width .35s ease; }
    .drv-bp-bar.indet > i { width: 35%; animation: drvIndet 1.1s ease-in-out infinite; }
    .drv-bp-list { display: grid; gap: 6px; min-width: 0; }
    .drv-bp-row { display: flex; flex-wrap: wrap; align-items: center;
      gap: 4px 8px; font-size: 12px; padding: 6px 8px; border-radius: 8px;
      background: var(--bg-input,#23252c); min-width: 0; }
    .drv-bp-row > b { flex: 0 0 auto; font-weight: 600; color: var(--text,#e8e9ec);
      white-space: nowrap; }
    .drv-bp-row > .drv-bp-tag { flex: 1 1 auto; min-width: 0; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis; }
    .drv-bp-row > .drv-bp-sub { flex: 0 1 auto; min-width: 0; max-width: 45%; margin-left: auto;
      color: var(--text-faint,#6c7079); font-size: 10px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; text-align: right; }
    .drv-bp-row > .drv-bp-bar { flex: 1 0 100%; margin: 4px 0 0; }
    .drv-bp-row.ok .drv-bp-tag { color: var(--success,#34c77b); }
    .drv-bp-row.run .drv-bp-tag { color: #7cc0ff; }
    .drv-bp-row.warn .drv-bp-tag { color: var(--warning,#f5a524); }
    .drv-bp-row.fail .drv-bp-tag { color: var(--danger,#f2555a); }

    /* ---------- 页面内联任务面板（所有任务，取代右下浮窗） ---------- */
    .drv-tasks { display: grid; gap: 10px; margin-bottom: 14px; min-width: 0; }
    .drv-tasks[hidden] { display: none; }
    .drv-tasks > * { min-width: 0; }
    .drv-tk.done .drv-bp-title { color: var(--success,#34c77b); }
    .drv-tk.failed .drv-bp-title { color: var(--danger,#f2555a); }
    .drv-tk-stat { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; min-width: 0; }
    .drv-tk-tag { font-weight: 600; white-space: nowrap; flex: 0 0 auto; }
    .drv-tk-tag.ok { color: var(--success,#34c77b); }
    .drv-tk-tag.run { color: #7cc0ff; }
    .drv-tk-tag.warn { color: var(--warning,#f5a524); }
    .drv-tk-tag.fail { color: var(--danger,#f2555a); }
    .drv-tk-log { color: var(--text-faint,#6c7079); font-size: 11px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; flex: 1 1 auto; min-width: 0; text-align: right; }
  `;
  document.head.appendChild(st);
})();

let DRV = { project: "", mode: "assets", ep: "", tasks: new Map(), dock: new Map(), segs: [], cat: "", scriptFont: 14, scriptDocs: {}, batchPid: null, batchSegs: [], batchGap: 0 };

function drvPlugin() {
  return { tab: "review" };
}
window.drvPlugin = drvPlugin;

/* ==================== 媒体 URL / 灯箱 / 上传 ==================== */
const DRV_THUMB_W = 440;
const DRV_THUMB_W_SM = 300;

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

function drvBindLightbox(box) {
  $$("[data-lb]", box).forEach(function (n) {
    n.addEventListener("click", function () {
      drvLightbox(n.dataset.lb, n.dataset.lbcap || "", n.dataset.lbtype === "video");
    });
  });
}

function drvPickImage(cb) {
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = "image/png,image/jpeg,image/webp,image/gif,image/*";
  inp.style.display = "none";
  document.body.appendChild(inp);
  inp.addEventListener("change", function () {
    const f = inp.files && inp.files[0];
    inp.remove();
    if (f) cb(f);
  });
  inp.click();
}

async function drvUpload(url, fd) {
  const r = await fetch(url, { method: "POST", body: fd, credentials: "include" });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.error || data.detail || ("HTTP " + r.status));
  }
  return r.json();
}

/* 轻量级模态 prompt 编辑（kf 改词重出用） */
function drvPromptDialog(title, def, imgSrc) {
  return new Promise((resolve) => {
    const m = document.createElement("div");
    m.className = "drv-modal";
    m.innerHTML =
      '<div class="drv-modal-box"><h4>' + escapeHtml(title) + "</h4>" +
      (imgSrc ? '<div class="drv-pd-prev"><img src="' + escapeHtml(imgSrc) + '" alt="当前图片"></div>' : "") +
      '<textarea class="drv-prompt" id="drvPdInput" spellcheck="false">' + escapeHtml(def || "") + "</textarea>" +
      '<div class="drv-modal-ops">' +
      '<button class="d-btn" data-act="cancel">取消</button>' +
      '<button class="d-btn primary" data-act="ok">🔄 重出图片</button>' +
      "</div></div>";
    document.body.appendChild(m);
    const close = function (val) { m.remove(); resolve(val); };
    m.addEventListener("click", function (e) { if (e.target === m) close(null); });
    m.querySelector('[data-act="cancel"]').addEventListener("click", function () { close(null); });
    m.querySelector('[data-act="ok"]').addEventListener("click", function () {
      close(m.querySelector("#drvPdInput").value.trim() || null);
    });
    m.querySelector("#drvPdInput").focus();
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
  DRV.cat = "";
  DRV.segs = [];
  DRV.scriptDocs = {};
  drvRender();
}

/* ==================== 剧本审核 ==================== */
async function drvListScriptDocs() {
  try {
    const data = await apiGet("/api/drama-review/script?name=" + encodeURIComponent(DRV.project));
    return data.docs || [];
  } catch (e) {
    return [];
  }
}

async function drvRenderScript() {
  const box = $("#drvBody");
  box.innerHTML = drvSkeleton(2);
  const docs = await drvListScriptDocs();
  if (!docs.length) {
    box.innerHTML = '<div class="d-empty">该项目未提供剧本文档（剧本/*.md、小说-*.md、*剧本*.md）。</div>';
    return;
  }
  if (!DRV.scriptDoc) DRV.scriptDoc = docs[0].path;
  const cur = DRV.scriptDoc;
  box.innerHTML = `
    <div class="drv-script">
      <div class="drv-script-docs">
        <div class="st-block-hd" style="margin-bottom:4px"><span>剧本文档</span></div>
        ${docs.map((d) =>
          `<button class="d-btn sm ${d.path === cur ? "primary" : ""}" data-doc="${escapeHtml(d.path)}"
             title="${escapeHtml(d.name)} · ${(d.size / 1024).toFixed(0)}KB">📄 ${escapeHtml(d.name)}</button>`
        ).join("")}
      </div>
      <div class="drv-script-reader">
        <div class="drv-script-toolbar">
          <b id="drvScriptDocName"></b>
          <span style="flex:1"></span>
          <button class="d-btn sm" id="drvScriptSmall">A−</button>
          <button class="d-btn sm" id="drvScriptBig">A＋</button>
        </div>
        <article class="drv-script-art" id="drvScriptArt"></article>
      </div>
    </div>`;
  $$("[data-doc]", box).forEach((b) =>
    b.addEventListener("click", () => {
      DRV.scriptDoc = b.dataset.doc;
      $$("[data-doc]", box).forEach((x) => x.classList.toggle("primary", x === b));
      drvLoadScriptContent();
    }));
  $("#drvScriptBig", box).addEventListener("click", () => drvScriptFont(1));
  $("#drvScriptSmall", box).addEventListener("click", () => drvScriptFont(-1));
  drvLoadScriptContent();
}

function drvScriptFont(dir) {
  DRV.scriptFont = Math.max(12, Math.min(22, DRV.scriptFont + dir * 1));
  const art = $("#drvScriptArt");
  if (art) art.style.fontSize = DRV.scriptFont + "px";
}

async function drvLoadScriptContent() {
  const art = $("#drvScriptArt");
  const nameEl = $("#drvScriptDocName");
  if (!art || !DRV.scriptDoc) return;
  nameEl.textContent = decodeURIComponent(DRV.scriptDoc.split("/").pop() || "");
  art.innerHTML = '<div class="drv-sk"><div class="drv-sk-bar" style="width:55%"></div><div class="drv-sk-bar" style="width:82%"></div><div class="drv-sk-bar" style="width:40%"></div></div>';
  try {
    let content = DRV.scriptDocs[DRV.scriptDoc];
    if (content === undefined) {
      const data = await apiGet("/api/drama-review/script/read?name=" + encodeURIComponent(DRV.project)
        + "&path=" + encodeURIComponent(DRV.scriptDoc));
      content = data.content || "（空文档）";
      DRV.scriptDocs[DRV.scriptDoc] = content;
    }
    let html = escapeHtml(content);
    if (typeof window.marked !== "undefined") {
      try {
        html = typeof window.marked.parse === "function" ? window.marked.parse(content) : window.marked(content);
      } catch (err) { /* 保持纯文本 */ }
    }
    art.innerHTML = html;
    art.style.fontSize = DRV.scriptFont + "px";
  } catch (e) {
    art.innerHTML = `<div class="d-empty">读取失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

/* ==================== 资产模式下标（分类名） ==================== */
const DRV_CATS = [
  { key: "character", label: "👤 角色" },
  { key: "prop", label: "🧰 道具" },
  { key: "scene", label: "🏙 场景" },
  { key: "monster", label: "👾 怪物" },
];

function drvCatLabel(key) {
  const c = DRV_CATS.find((x) => x.key === key);
  return (c && c.label) || key;
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
    const byCat = {};
    list.forEach((a) => { (byCat[a.type] = byCat[a.type] || []).push(a); });
    const order = DRV_CATS.filter((c) => byCat[c.key] && byCat[c.key].length);
    if (!DRV.cat || !(DRV.cat in byCat)) DRV.cat = order.length ? order[0].key : "";

    box.innerHTML = `<div class="drv-cats">
        ${order.map((c) =>
          `<button class="drv-cat ${c.key === DRV.cat ? "on" : ""}" data-cat="${c.key}">${c.label}<span class="count">${byCat[c.key].length}</span></button>`
        ).join("")}
        <button class="drv-cat" data-cat="__all__">🗂 全部<span class="count">${list.length}</span></button>
      </div>
      <div class="drv-addbox hidden" id="drvAddBox">
        <select id="drvAddType" class="d-input">
          ${DRV_CATS.map((c) => `<option value="${c.key}">${c.label.replace(/^..\s*/, "")}</option>`).join("")}
        </select>
        <input id="drvAddName" class="d-input" placeholder="资产名（例如 宝石吊坠）" spellcheck="false">
        <button class="d-btn sm" id="drvAddPick">🖼 选择图片</button>
        <span class="drv-file-name" id="drvAddFileName"></span>
        <button class="d-btn sm primary" id="drvAddOk">保存新资产</button>
        <button class="d-btn sm" id="drvAddCancel">取消</button>
      </div>
      <div class="drv-addbox" style="border-style:solid">
        <span style="font-size:12px;color:var(--text-soft,#a2a6b0)">当前审核：<b style="color:var(--text,#e8e9ec)">${DRV.cat === "__all__" ? "全部" : drvCatLabel(DRV.cat)}</b></span>
        <span style="flex:1"></span>
        <button class="d-btn sm primary" id="drvToggleAdd">＋ 自定义上传新资产</button>
        <span class="drv-tasklog" id="drvAssetLog"></span>
      </div>${drvRenderAssetCards(byCat)}`;

    $$(".drv-cat", box).forEach((b) =>
      b.addEventListener("click", () => { DRV.cat = b.dataset.cat; drvRenderAssets(); }));
    const toggleAdd = $("#drvToggleAdd", box);
    toggleAdd.addEventListener("click", () => $("#drvAddBox", box).classList.toggle("hidden"));
    $("#drvAddCancel", box).addEventListener("click", () => $("#drvAddBox", box).classList.add("hidden"));
    $("#drvAddPick", box).addEventListener("click", () => drvPickImage((f) => {
      DRV.addFile = f;
      $("#drvAddFileName", box).textContent = f.name;
    }));
    $("#drvAddOk", box).addEventListener("click", () => drvAddAsset(box));
    $$("[data-act=regen]", box).forEach((b) =>
      b.addEventListener("click", () => drvRegenAsset(b.dataset.id)));
    $$("[data-act=arep]", box).forEach((b) =>
      b.addEventListener("click", () => drvReplaceAsset(b.dataset.id, b.dataset.aid, box)));
    $$("[data-act=adel]", box).forEach((b) =>
      b.addEventListener("click", () => drvDeleteAsset(b.dataset.id, box)));
    drvBindLightbox(box);
  } catch (e) {
    box.innerHTML = `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

function drvRenderAssetCards(byCat) {
  const show = DRV.cat === "__all__"
    ? Object.values(byCat).flat()
    : (byCat[DRV.cat] || []);
  return `<div class="drv-cards">` + show.map((a) => {
    const chip = a.status === "ready"
      ? '<span class="drv-chip ok">✅ ready</span>'
      : '<span class="drv-chip wait">' + escapeHtml(a.status || "未标") + "</span>";
    const img = a.path
      ? `<img loading="lazy" decoding="async" src="${drvThumbUrl(a.path, DRV_THUMB_W, a.md5)}"
           data-lb="${drvUrl(a.path, a.md5)}" data-lbcap="${escapeHtml(a.name || a.id)}"
           alt="${escapeHtml(a.name || a.id)}" title="点击查看原图">`
      : '<div class="d-empty">无出图</div>';
    return `
      <div class="drv-card" data-id="${escapeHtml(a.id)}">
        <div class="drv-card-top">
          <b>${drvCatLabel(a.type)} · ${escapeHtml(a.name || a.id)}</b>
          <span class="drv-chip meta" id="drvSt_${escapeHtml(a.id)}">${chip}</span>
        </div>
        <div class="drv-card-img">${img}</div>
        <textarea id="drvP_${escapeHtml(a.id)}" class="d-input drv-prompt" rows="3" spellcheck="false">${escapeHtml(a.prompt || "")}</textarea>
        <div class="drv-card-actions">
          <button class="d-btn sm primary" data-act="regen" data-id="${escapeHtml(a.id)}">🖼 用此提示词重新生成</button>
          <button class="d-btn sm" data-act="arep" data-id="${escapeHtml(a.id)}" data-aid="${escapeHtml(a.name || a.id)}">⬆ 替换图片（自定义上传）</button>
          <button class="d-btn sm" data-act="adel" data-id="${escapeHtml(a.id)}" title="仅从清单移除，不删原图文件">🗑 移除</button>
          <span class="drv-tasklog" id="drvLog_${escapeHtml(a.id)}"></span>
        </div>
      </div>`;
  }).join("") + "</div>";
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

function drvReplaceAsset(id, aid, box) {
  drvPickImage(async (f) => {
    const fd = new FormData();
    fd.append("name", DRV.project);
    fd.append("id", id);
    fd.append("file", f);
    try {
      const r = await drvUpload("/api/drama-review/assets/upload", fd);
      const el = box ? $("#drvSt_" + id) : null;
      const log = box ? $("#drvAssetLog") : null;
      if (log) log.textContent = `✅ ${aid} 已替换 -> ${r.path || ""}`;
      toast("资产图片已替换", "ok");
      drvRenderAssets();
      if (el) el.textContent = "";
    } catch (e) {
      toast("替换失败: " + (e.message || e), "warn");
    }
  });
}

async function drvDeleteAsset(id, box) {
  if (!confirm(`确认从清单移除资产「${id}」？（仅改 manifest，不删原始图片文件）`)) return;
  try {
    const r = await apiPost("/api/drama-review/assets/delete", { name: DRV.project, id });
    if (!r.ok) { toast(r.error || "失败", "warn"); return; }
    toast("已移除 " + id, "ok");
    drvRenderAssets();
  } catch (e) {
    toast("移除失败: " + (e.message || e), "warn");
  }
}

async function drvAddAsset(box) {
  const atype = $("#drvAddType", box).value;
  const aname = ($("#drvAddName", box).value || "").trim();
  const file = DRV.addFile;
  if (!aname) { toast("请填写资产名", "warn"); return; }
  if (!file) { toast("请选择图片", "warn"); return; }
  const fd = new FormData();
  fd.append("name", DRV.project);
  fd.append("type", atype);
  fd.append("asset_name", aname);
  fd.append("file", file);
  try {
    const r = await drvUpload("/api/drama-review/assets/add", fd);
    if (!r.ok) { toast(r.error || "失败", "warn"); return; }
    DRV.cat = atype;
    DRV.addFile = null;
    toast(`已新增 ${aname}`, "ok");
    drvRenderAssets();
  } catch (e) {
    toast("新增失败: " + (e.message || e), "warn");
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

function drvFrameTile(s, seg) {
  const fr = s.label ? `${escapeHtml(s.label)}${s.role && s.role !== s.label ? " · " + escapeHtml(s.role) : ""}` : escapeHtml(s.role || "参考图");
  const ops = [];
  ops.push(`<button class="d-btn sm" data-act="fr-rep" data-seg="${escapeHtml(seg)}" data-idx="${s.idx}" data-kind="${s.kind}">⬆ 替换</button>`);
  if (s.kind === "ref") {
    ops.push(`<button class="d-btn sm" data-act="fr-del" data-seg="${escapeHtml(seg)}" data-idx="${s.idx}">✕ 移除</button>`);
  }
  if (s.kind === "kf2") {
    ops.push(`<button class="d-btn sm" data-act="fr-kf2" data-seg="${escapeHtml(seg)}" data-idx="${s.idx}">🔄 重出</button>`);
  }
  return `
    <div class="drv-fr ${s.kind === "kf2" ? "is-kf2" : ""}">
      <div class="drv-fr-shot">
        <img loading="lazy" decoding="async" src="${drvThumbUrl(s.path, DRV_THUMB_W_SM)}"
          data-lb="${drvUrl(s.path)}" data-lbcap="${escapeHtml(seg)} · ${escapeHtml(s.label || s.role)}"
          alt="${escapeHtml(s.label || s.role)}" title="点击查看原图">
        <span class="drv-fr-idx">${s.idx + 1}</span>
      </div>
      <div class="drv-fr-info"><div class="drv-fr-label">${fr}</div></div>
      <div class="drv-fr-ops">${ops.join("")}</div>
    </div>`;
}

async function drvRenderSegments() {
  const box = $("#drvBody");
  box.innerHTML = drvSkeleton(4);
  try {
    const data = await apiGet(
      "/api/drama-review/storyboard?name=" + encodeURIComponent(DRV.project) + "&ep=" + encodeURIComponent(DRV.ep));
    const segs = data.segments || [];
    DRV.segs = segs;
    box.innerHTML = `
      <div class="drv-segbar" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px">
        <span class="drv-segbar-title">${escapeHtml(DRV.ep)} · ${segs.length} 段 · ${segs.filter((s) => s.has_video).length} 已出片</span>
        <label style="display:inline-flex;align-items:center;gap:4px;cursor:pointer"><input type="checkbox" id="drvSelAll"> 全选</label>
        <button class="d-btn sm" id="drvBatchSelNone">清空勾选</button>
        <button class="d-btn sm primary" id="drvBatchKf">🖼 批量重出勾选段关键帧</button>
        <button class="d-btn sm primary" id="drvBatchRnd">🎬 批量重渲勾选段</button>
        <button class="d-btn sm primary" id="drvBatchMissing">⚡ 一键补齐缺片段</button>
        <button class="d-btn sm primary" id="drvMergeAll">✅ 确认合并本集（${escapeHtml(DRV.ep)}）</button>
      </div>
      <div class="drv-segs">
        ${segs.map((s) => {
          const chip = s.has_video
            ? '<span class="drv-chip ok">✅ 已出片</span>'
            : '<span class="drv-chip wait">⏳ 未出片</span>';
          const frames = s.frames || [];
          const kf2Missing = frames.some((f2) => f2.kind === "kf2") ? "" : `
            <div class="drv-fr is-kf2">
              <div class="drv-fr-shot"><div class="drv-fr-miss">构图图未就绪</div></div>
              <div class="drv-fr-info"><div class="drv-fr-label">kf2 构图图 · 本段分镜构图</div></div>
              <div class="drv-fr-ops"><button class="d-btn sm" data-act="fr-kf2" data-seg="${escapeHtml(s.seg)}">🔄 重出</button></div>
            </div>`;
          const emptyN = Math.max(0, 5 - frames.length - (kf2Missing ? 1 : 0));
          const video = s.video
            ? `<div class="drv-card-img drv-video">
                 <video controls preload="none"
                   poster="${drvThumbUrl(s.video, DRV_THUMB_W, s.vmtime)}"
                   data-lb="${drvUrl(s.video, s.vmtime)}" data-lbtype="video"
                   data-lbcap="${escapeHtml(s.seg)} · ${escapeHtml(s.title || "")}"
                   src="${drvUrl(s.video, s.vmtime)}"></video>
               </div>`
            : `<div class="drv-card-img" style="border:1px dashed var(--border,#2c2f38);border-radius:8px;padding:26px 0;text-align:center;color:var(--text-faint,#6c7079);font-size:12px">🎬 尚未出片 —— 确认参考图与提示词后重渲</div>`;
          const relayNote = s.kf1_origin === "relay"
            ? `<span class="drv-chip meta">首帧 ⬅ 上游末帧（自动）</span>` : "";
          return `
        <div class="drv-seg" data-seg="${escapeHtml(s.seg)}">
          <div class="drv-card-top">
            <label title="勾选后可批量重出/重渲"><input type="checkbox" class="drv-segsel-box" data-seg="${escapeHtml(s.seg)}"></label>
            <b>${escapeHtml(s.seg)} · ${escapeHtml(s.title || "")}</b>
            <span class="drv-chip meta">${s.duration}s ${chip} ${relayNote}</span>
          </div>
          <div class="drv-seg-body">
            <div class="drv-seg-left">
              <div class="drv-fr-hd">
                <b style="font-size:12px">🎨 用于本段视频的参考图</b>
                <span class="drv-chip meta">${frames.length}/5</span>
              </div>
              <div class="drv-fr-grid">
                ${frames.map((s2) => drvFrameTile(s2, s.seg)).join("")}
                ${kf2Missing}
                ${emptyN ? Array.from({ length: emptyN }).map(() =>
                  `<button class="drv-fr-add" data-act="fr-add" data-seg="${escapeHtml(s.seg)}">＋ 添加图片</button>`
                ).join("") : ""}
              </div>
              <div class="drv-fr-note">参考图 = kf_plan 中该段的 refs + kf2 构图图，参与下一次渲影；替换某张只影响本段。</div>
            </div>
            <div class="drv-seg-right">
              ${video}
              <textarea id="drvSegP_${escapeHtml(s.seg)}" class="d-input drv-prompt" rows="4" spellcheck="false">${escapeHtml(s.prompt || "")}</textarea>
              <div class="drv-card-actions">
                <button class="d-btn sm" data-act="rerender" data-seg="${escapeHtml(s.seg)}">🎬 直接重渲此段</button>
                <button class="d-btn sm primary" data-act="rerender2" data-seg="${escapeHtml(s.seg)}">✏ 按上方提示词重渲</button>
                <button class="d-btn sm warn" data-act="cascade" data-seg="${escapeHtml(s.seg)}">⛓ 自此段级联重渲（到尾）</button>
                <span class="drv-tasklog" id="drvSegLog_${escapeHtml(s.seg)}"></span>
              </div>
            </div>
          </div>
        </div>`;
        }).join("")}
      </div>`;
    $$("[data-act=rerender]", box).forEach((b) =>
      b.addEventListener("click", () => drvRerender(b.dataset.seg, "")));
    $$("[data-act=rerender2]", box).forEach((b) =>
      b.addEventListener("click", () => drvRerender(b.dataset.seg, "use_prompt")));
    $$("[data-act=cascade]", box).forEach((b) =>
      b.addEventListener("click", () => drvCascade(b.dataset.seg)));
    $$("[data-act=fr-rep]", box).forEach((b) =>
      b.addEventListener("click", () => drvReplaceFrame(b.dataset.seg, b.dataset.idx, b.dataset.kind)));
    $$("[data-act=fr-add]", box).forEach((b) =>
      b.addEventListener("click", () => drvReplaceFrame(b.dataset.seg, "", "")));
    $$("[data-act=fr-del]", box).forEach((b) =>
      b.addEventListener("click", () => drvRemoveFrame(b.dataset.seg, b.dataset.idx)));
    $$("[data-act=fr-kf2]", box).forEach((b) =>
      b.addEventListener("click", () => drvKfRegen(b.dataset.seg, "kf2")));
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
    $("#drvBatchKf", box).addEventListener("click", () => drvBatchKeyframes());
    $("#drvBatchRnd", box).addEventListener("click", () => drvBatchRender());
    $("#drvBatchMissing", box).addEventListener("click", () => drvBatchMissing());
    $("#drvMergeAll", box).addEventListener("click", () => drvMerge([DRV.ep]));
    drvBindLightbox(box);
    drvRenderTasks();
  } catch (e) {
    box.innerHTML = `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

async function drvReplaceFrame(seg, idx, kind) {
  drvPickImage(async (f) => {
    const fd = new FormData();
    fd.append("name", DRV.project);
    fd.append("seg", seg);
    fd.append("replace", idx);
    fd.append("file", f);
    try {
      const r = await drvUpload("/api/drama-review/storyboard/frames/upload", fd);
      toast(idx === "" ? `${seg} 已新增参考图` : `${seg} 第 ${Number(idx) + 1} 张已替换`, "ok");
      drvRenderSegments();
    } catch (e) {
      toast("操作失败: " + (e.message || e), "warn");
    }
  });
}

async function drvRemoveFrame(seg, idx) {
  if (!confirm(`确认移除 ${seg} 的第 ${Number(idx) + 1} 张参考图？（只影响本段）`)) return;
  try {
    const r = await apiPost("/api/drama-review/storyboard/frames/remove", { name: DRV.project, seg, slot: Number(idx) });
    if (!r.ok) { toast(r.error || "失败", "warn"); return; }
    toast(seg + " 已移除参考图", "ok");
    drvRenderSegments();
  } catch (e) {
    toast("移除失败: " + (e.message || e), "warn");
  }
}

/* ==================== 批量操作 ==================== */
function drvSelectedSegs() {
  return $$(".drv-segsel-box").filter((c) => c.checked).map((c) => c.dataset.seg);
}

function drvMissingVideoSegs() {
  return DRV.segs.filter((s) => !s.has_video).map((s) => s.seg);
}

function drvMissingKfSegs() {
  return DRV.segs.filter((s) => (s.frames || []).length === 0).map((s) => s.seg);
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
  try {
    const r = await apiPost("/api/drama-review/segments/batch", {
      name: DRV.project, segs: sel, workers: 0 });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.batchPid = r.task_id;
    DRV.batchSegs = sel.slice();
    DRV.batchGap = r.submit_gap || 0;
    DRV.tasks.set(r.task_id, { kind: "batch", id: "Batch", cb: drvRenderSegments });
    toast(`已提交批量渲影 ${r.jobs} 段，串行提交、提交成功即后台轮询`, "ok");
    drvRenderTasks();
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvBatchMissing() {
  const sel = drvMissingVideoSegs();
  if (!sel.length) { toast("本集已全部出片，无需补齐", "ok"); return; }
  try {
    const r = await apiPost("/api/drama-review/segments/batch", {
      name: DRV.project, segs: sel, workers: 0 });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.batchPid = r.task_id;
    DRV.batchSegs = sel.slice();
    DRV.batchGap = r.submit_gap || 0;
    DRV.tasks.set(r.task_id, { kind: "batch", id: "Batch", cb: drvRenderSegments });
    toast(`已提交补齐 ${r.jobs} 段，串行提交、提交成功即后台轮询`, "ok");
    drvRenderTasks();
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

async function drvKfRegen(seg, frame) {
  const segObj = DRV.segs.find((s) => s.seg === seg);
  const def = (frame === "kf2" && segObj) ? (segObj.kf2_prompt || "")
    : (segObj ? (segObj.kf1_prompt || "") : "");
  const fr = segObj && (segObj.frames || []).find((f) => f.kind === frame);
  const img = fr && fr.path ? drvThumbUrl(fr.path, 440) : "";
  const label = frame === "kf2" ? "kf2 构图图 · 本段分镜构图" : frame;
  const prompt = await drvPromptDialog(`${seg} · ${label}（提示词修改 / 图片重出）`, def, img);
  if (!prompt) return;
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
    DRV.tasks.set(r.task_id, { kind: "merge", id: eps.join(","), cb: drvRender });
    toast("已启动合并 " + eps.join(","), "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

async function drvMergeEp(eps) {
  const single = eps.length === 1;
  try {
    const r = await apiPost("/api/drama-review/merge", { name: DRV.project, eps });
    if (!r.ok) { toast(r.error || "启动失败", "warn"); return; }
    DRV.tasks.set(r.task_id, {
      kind: single ? "epmerge" : "epmergeall",
      id: single ? eps[0] : "__all", cb: drvRenderEpisodes,
    });
    toast("已启动合成 " + eps.join(","), "ok");
    drvPoll(r.task_id);
  } catch (e) {
    toast("启动失败: " + (e.message || e), "warn");
  }
}

/* ==================== 剧集审核（完整整集视频） ==================== */
function drvFmtSize(b) {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + u[i];
}
function drvFmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function drvEpCard(e) {
  const fully = e.segments_total > 0 && e.segments_done >= e.segments_total;
  const segChip = e.segments_total
    ? `<span class="drv-chip ${fully ? "ok" : "wait"}">${e.segments_done}/${e.segments_total} 段</span>`
    : (e.segments_done ? `<span class="drv-chip meta">${e.segments_done} 段</span>` : "");
  const vchip = e.has_video
    ? '<span class="drv-chip ok">✅ 已成片</span>'
    : '<span class="drv-chip wait">⏳ 未合成</span>';
  const video = e.has_video
    ? `<video controls preload="none"
         poster="${drvThumbUrl(e.video, DRV_THUMB_W, e.vmtime)}"
         data-lb="${drvUrl(e.video, e.vmtime)}" data-lbtype="video"
         data-lbcap="${escapeHtml(e.ep)} · 完整整集"
         src="${drvUrl(e.video, e.vmtime)}"></video>`
    : `<div class="drv-card-img" style="border:1px dashed var(--border,#2c2f38);border-radius:8px;padding:34px 0;text-align:center;color:var(--text-faint,#6c7079);font-size:12px">🎬 尚未合成整集 —— 本集 ${e.segments_done}/${e.segments_total || "?"} 段已出片</div>`;
  const meta = [segChip, vchip,
    e.has_video ? drvFmtSize(e.size) : "", e.has_video ? drvFmtTime(e.vmtime) : ""
  ].filter(Boolean).join(" ");
  return `
    <div class="drv-seg" data-ep="${escapeHtml(e.ep)}">
      <div class="drv-card-top">
        <b>${escapeHtml(e.ep)}</b>
        <span class="drv-chip meta">${meta}</span>
      </div>
      <div class="drv-card-img drv-video">${video}</div>
      <div class="drv-card-actions">
        <button class="d-btn sm primary" data-act="ep-merge" data-ep="${escapeHtml(e.ep)}">${e.has_video ? "♻ 重新合成整集" : "✅ 合成整集"}</button>
        ${e.has_video ? `<a class="d-btn sm" href="${drvUrl(e.video, e.vmtime)}" download="${escapeHtml(e.ep)}.mp4">⬇ 下载</a>` : ""}
        <span class="drv-tasklog" id="drvEpLog_${escapeHtml(e.ep)}"></span>
      </div>
    </div>`;
}

async function drvRenderEpisodes() {
  const box = $("#drvBody");
  box.innerHTML = drvSkeleton(3);
  try {
    const data = await apiGet("/api/drama-review/episodes?name=" + encodeURIComponent(DRV.project));
    const eps = data.episodes || [];
    const full = data.full || null;
    const vb = Date.now();
    const doneN = eps.filter((e) => e.has_video).length;
    const head = `
      <div class="drv-segbar" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px">
        <span class="drv-segbar-title">📺 剧集审核 · 共 ${eps.length} 集 · ${doneN} 集已成片</span>
        <button class="d-btn sm primary" id="drvEpMergeAll">✅ 一键合成所有集 + 连播</button>
        <button class="d-btn sm" id="drvEpRefresh">⟳ 刷新</button>
        <span class="drv-tasklog" id="drvEpLog"></span>
      </div>`;
    if (!eps.length) {
      box.innerHTML = head + '<div class="d-empty">暂无剧集（投喂稿目录为空）</div>';
    } else {
      const fullCard = full
        ? `<div class="drv-seg" data-ep="__full">
             <div class="drv-card-top">
               <b>${escapeHtml(full.name)}</b>
               <span class="drv-chip meta"><span class="drv-chip ok">✅ 全剧连播</span> ${full.count} 集 · ${drvFmtSize(full.size)} · ${drvFmtTime(full.vmtime)}</span>
             </div>
             <div class="drv-card-img drv-video">
               <video controls preload="none"
                 poster="${drvThumbUrl(full.video, DRV_THUMB_W, full.vmtime)}"
                 data-lb="${drvUrl(full.video, full.vmtime)}" data-lbtype="video"
                 data-lbcap="${escapeHtml(full.name)}"
                 src="${drvUrl(full.video, full.vmtime)}"></video>
             </div>
             <div class="drv-card-actions">
               <a class="d-btn sm" href="${drvUrl(full.video, full.vmtime)}" download="${escapeHtml(full.name)}">⬇ 下载连播</a>
             </div>
           </div>`
        : "";
      box.innerHTML = head + '<div class="drv-segs">' + fullCard + eps.map(drvEpCard).join("") + "</div>";
    }
    $$('[data-act="ep-merge"]', box).forEach((b) =>
      b.addEventListener("click", () => drvMergeEp([b.dataset.ep])));
    const all = $("#drvEpMergeAll", box);
    if (all) all.addEventListener("click", async () => {
      const list = eps.map((e) => e.ep);
      if (!list.length) { toast("无集可合成", "warn"); return; }
      drvMergeEp(list);
    });
    const rf = $("#drvEpRefresh", box);
    if (rf) rf.addEventListener("click", () => drvRenderEpisodes());
    if (vb) drvBindLightbox(box);
  } catch (e) {
    box.innerHTML = `<div class="d-empty">加载失败: ${escapeHtml(e.message || e)}</div>`;
  }
}

/* ==================== 任务轮询 ==================== */
/* ==================== 任务进度浮窗（固定右下，跨滚动/模式常驻） ==================== */
function drvTaskLabel(meta) {
  const kind = (meta && meta.kind) || "", id = meta && meta.id;
  const map = {
    seg: "🎬 重渲 " + (id || ""),
    cascade: "🎬 级联重渲 " + (id || ""),
    batch: "🎬 批量渲影",
    keyframe: "🖼 关键帧 " + (id || ""),
    "keyframe-batch": "🖼 批量关键帧",
    asset: "🖼 资产重出 " + (id || ""),
    merge: "🔗 合并 " + (id || ""),
    epmerge: "📺 整集合成 " + (id || ""),
    epmergeall: "📺 全部集合成",
  };
  return map[kind] || ("任务 " + (id || kind || ""));
}

function drvFmtDur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + "s";
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + "m" + String(s).padStart(2, "0") + "s";
}

function drvTaskPhase(t) {
  const status = (t && t.status) || "queued";
  if (status === "cancelled") return { cls: "warn", txt: "⏹ 已取消", pct: 0 };
  if (status === "failed") return { cls: "fail", txt: "❌ 失败", pct: 0 };
  if (status === "done") return { cls: "ok", txt: "✅ 完成", pct: 100 };
  const log = (t && t.log) || [];
  for (let i = log.length - 1; i >= 0; i--) {
    const l = log[i];
    if (/: OK ->/.test(l)) return { cls: "ok", txt: "✅ 已出片", pct: 100 };
    if (/: FAIL\b/.test(l)) return { cls: "fail", txt: "❌ 失败", pct: 0 };
    if (/FILE=/.test(l)) return { cls: "ok", txt: "✅ 已出片 · 落盘中", pct: 100 };
    if (/任务状态:\s*completed/.test(l)) return { cls: "ok", txt: "✅ 渲染完成 · 待落盘", pct: 100 };
    const mp = l.match(/任务状态:\s*in_progress\D*(\d+)%/);
    if (mp) return { cls: "run", txt: `⏳ 渲染中 ${mp[1]}%`, pct: +mp[1] || 0 };
    if (/任务状态:\s*pending/.test(l)) return { cls: "run", txt: "⏳ 已进队 · 排队等位", pct: 0 };
    if (/TASK_ID=/.test(l)) return { cls: "run", txt: "⏳ 提交成功 · 轮询中", pct: 0 };
    const mr = l.match(/第\s*(\d+)\s*次提交失败/);
    if (mr) return { cls: "warn", txt: `🔄 提交失败 · 第 ${mr[1]} 次重试`, pct: 0 };
    if (/\b提交\s|提交 agnes-|提交 /.test(l)) return { cls: "warn", txt: "⏳ 提交中", pct: 0 };
    if (/渲染开始|待渲|参考图|构图图|关键帧/.test(l)) return { cls: "run", txt: "⏳ 准备中", pct: 0 };
  }
  return { cls: status === "running" ? "run" : "", txt: status === "running" ? "⏳ 处理中" : "⏸ 排队中", pct: 0 };
}

function drvTaskCardHtml(tid, d) {
  const t = d.t || {};
  const status = t.status || "queued";
  const icon = status === "running" ? "⏳" : status === "done" ? "✅"
    : status === "failed" ? "❌" : status === "cancelled" ? "⏹" : "⏸";
  const jobs = t.jobs || 0, done = t.done || 0;
  const dur = drvFmtDur(((d.finishedAt || Date.now()) - d.startedAt) / 1000);
  const pct = jobs > 1 ? Math.min(100, Math.round(done * 100 / jobs)) : 0;
  const phase = drvTaskPhase(t);
  let bar = "", indet = false, barPct = 0;
  if (jobs > 1) { barPct = pct; }
  else if (phase.pct > 0) { barPct = phase.pct; }
  else if (phase.cls === "ok" || status === "done") { barPct = 100; }
  else if (status === "running" || status === "queued" || phase.cls === "run"
           || (phase.cls === "warn" && status !== "cancelled")) { indet = true; }
  if (barPct > 0) bar = `<div class="drv-bp-bar big"><i style="width:${barPct}%"></i></div>`;
  else if (indet) bar = '<div class="drv-bp-bar big indet"><i></i></div>';
  const pctTxt = jobs > 1 ? `${done}/${jobs} · ${pct}%` : (phase.pct > 0 ? `${phase.pct}%` : "");
  const proj = d.meta && d.meta.proj;
  const metaTxt = [proj, pctTxt].filter(Boolean).join(" · ");
  const log = t.log || [];
  const last = log.slice(-1)[0] || (status === "queued" ? "已提交，等待开始…" : "");
  const canCancel = status === "running" || status === "queued";
  return `<div class="drv-bp drv-tk ${status}" data-tid="${escapeHtml(tid)}" title="${escapeHtml(log.slice(-40).join("\n"))}">
    <div class="drv-bp-hd">
      <span class="drv-bp-title">${icon} ${escapeHtml(drvTaskLabel(d.meta))}</span>
      ${metaTxt ? `<span class="drv-bp-meta">${escapeHtml(metaTxt)}</span>` : ""}
      <span class="drv-bp-pct">${escapeHtml(dur)}</span>
      ${canCancel ? `<button class="drv-bp-x cancel drv-task-cancel" data-tid="${escapeHtml(tid)}" title="取消任务">⏹ 取消</button>` : ""}
      <button class="drv-bp-x drv-task-x" data-tid="${escapeHtml(tid)}" title="关闭">×</button>
    </div>
    ${bar}
    <div class="drv-tk-stat">
      <span class="drv-tk-tag ${phase.cls}">${phase.txt}</span>
      <span class="drv-tk-log" title="${escapeHtml(last)}">${escapeHtml(last)}</span>
    </div>
  </div>`;
}

function drvBatchPanelHtml(tid, d) {
  const meta = d.meta || {};
  const t = d.t || {};
  const segs = (DRV.batchPid === tid && DRV.batchSegs.length)
    ? DRV.batchSegs : (meta.segs || []);
  if (!segs.length) return drvTaskCardHtml(tid, d);
  const status = t.status || "queued";
  const jobs = segs.length, done = t.done || 0;
  const states = drvBatchSegStates(t.log || []);
  const pct = jobs ? Math.min(100, Math.round(done * 100 / jobs)) : 0;
  const gap = (DRV.batchPid === tid ? DRV.batchGap : 0) || meta.submit_gap || 0;
  const gapTxt = gap > 0 ? `串行提交 · 间隔 ${gap}s` : "";
  const rows = segs.map((seg) => {
    const s0 = states[seg] || { submitting: false, retry: 0, submitted: false, tid: "", status: "", pct: 0, deliver: "" };
    const s = drvBatchSegStatus(seg, s0);
    let bar = "";
    if (s.pct > 0) bar = `<div class="drv-bp-bar"><i style="width:${s.pct}%"></i></div>`;
    else if (s0.submitting || s.cls === "run") bar = '<div class="drv-bp-bar indet"><i></i></div>';
    return `<div class="drv-bp-row ${s.cls}" title="${escapeHtml(seg)}">
      <b>${escapeHtml(seg)}</b>
      <span class="drv-bp-tag">${s.txt}</span>
      <span class="drv-bp-sub">${s0.tid ? "task " + escapeHtml(s0.tid) : "&nbsp;"}</span>
      ${bar}
    </div>`;
  }).join("");
  const stTxt = status === "done" ? "✅ 批量完成" : status === "failed" ? "❌ 批量失败"
    : status === "cancelled" ? "⏹ 已取消" : status === "running" ? "⏳ 进行中" : "⏸ 已提交";
  const projTxt = meta.proj ? `· ${escapeHtml(meta.proj)} ` : "";
  const canCancel = status === "running" || status === "queued";
  return `<div class="drv-bp" data-tid="${escapeHtml(tid)}">
    <div class="drv-bp-hd">
      <span class="drv-bp-title">🎬 批量渲影 ${projTxt}${stTxt}</span>
      <span class="drv-bp-meta">${gapTxt ? escapeHtml(gapTxt) : ""}</span>
      <span class="drv-bp-pct">${done}/${jobs} · ${pct}%</span>
      ${canCancel ? `<button class="drv-bp-x cancel drv-task-cancel" data-tid="${escapeHtml(tid)}" title="取消任务">⏹ 取消</button>` : ""}
      <button class="drv-bp-x drv-task-x" data-tid="${escapeHtml(tid)}" title="关闭">×</button>
    </div>
    <div class="drv-bp-bar big"><i style="width:${pct}%"></i></div>
    <div class="drv-bp-list">${rows}</div>
  </div>`;
}

function drvRenderTasks() {
  const box = $("#drvTasks");
  if (!box) return;
  const now = Date.now();
  for (const [tid, d] of [...DRV.dock.entries()]) {
    if (!DRV.tasks.has(tid) && d.finishedAt && now - d.finishedAt > 60000) DRV.dock.delete(tid);
  }
  const items = [];
  for (const [tid, d] of [...DRV.dock.entries()].reverse()) {
    items.push(d.meta && d.meta.kind === "batch" ? drvBatchPanelHtml(tid, d) : drvTaskCardHtml(tid, d));
  }
  const html = items.filter(Boolean).join("");
  box.innerHTML = html;
  box.hidden = !html;
  $$(".drv-task-x", box).forEach((b) => b.addEventListener("click", () => {
    const tid = b.dataset.tid;
    DRV.tasks.delete(tid);
    DRV.dock.delete(tid);
    if (DRV.batchPid === tid) { DRV.batchPid = null; DRV.batchSegs = []; }
    drvRenderTasks();
  }));
  $$(".drv-task-cancel", box).forEach((b) => b.addEventListener("click", async () => {
    const tid = b.dataset.tid;
    if (!confirm("确认取消该任务？运行中的生成进程将被终止。")) return;
    try {
      const r = await apiPost("/api/drama-review/tasks/" + encodeURIComponent(tid) + "/cancel", {});
      if (!r.ok) { toast(r.error || "取消失败", "warn"); return; }
      if (!r.already) toast("⏹ 已取消任务", "info");
    } catch (e) {
      toast("取消失败: " + (e.message || e), "warn");
    }
  }));
}

function drvPoll(tid) {
  const meta = DRV.tasks.get(tid);
  if (meta && !DRV.dock.has(tid)) DRV.dock.set(tid, { meta, t: null, startedAt: Date.now(), finishedAt: 0 });
  drvRenderTasks();
  if (typeof window.__drvPollingActive === "undefined") window.__drvPollingActive = setInterval(drvPollStep, 2000);
}

/* ---------- 批量渲影内联进度（解析任务日志，逐段展示提交/轮询状态） ---------- */
function drvBatchSegStates(log) {
  const st = {};
  for (const line of (log || [])) {
    const mSeg = line.match(/E\d+-\d+/);
    if (!mSeg) continue;
    const seg = mSeg[0];
    const s0 = st[seg] || { submitting: false, retry: 0, submitted: false, tid: "", status: "", pct: 0, deliver: "" };
    if (/\]\s*提交 /.test(line)) s0.submitting = true;
    const mRetry = line.match(/第\s*(\d+)\s*次提交失败/);
    if (mRetry) { s0.retry = +mRetry[1] || 0; s0.submitted = false; }
    const mTid = line.match(/TASK_ID=([0-9a-f]+)/);
    if (mTid) { s0.submitted = true; s0.tid = mTid[1]; s0.retry = 0; s0.submitting = false; }
    if (/任务状态:\s*pending/.test(line)) { s0.status = "pending"; s0.pct = 0; }
    const mPct = line.match(/任务状态:\s*in_progress\D*(\d+)%/);
    if (mPct) { s0.status = "progress"; s0.pct = +mPct[1] || 0; }
    if (/任务状态:\s*completed/.test(line)) { s0.status = "completed"; s0.pct = 100; }
    if (/: OK ->/.test(line)) s0.deliver = "ok";
    if (/: FAIL\b/.test(line)) s0.deliver = "fail";
    st[seg] = s0;
  }
  return st;
}

function drvBatchSegStatus(seg, s0) {
  if (s0.deliver === "ok") return { cls: "ok", txt: "✅ 已出片", pct: 100 };
  if (s0.deliver === "fail") return { cls: "fail", txt: "❌ 失败", pct: 0 };
  if (s0.status === "completed") return { cls: "ok", txt: "✅ 渲染完成 · 待落盘", pct: 100 };
  if (s0.status === "progress") return { cls: "run", txt: `⏳ 渲染中 ${s0.pct}%`, pct: s0.pct };
  if (s0.status === "pending") return { cls: "run", txt: "⏳ 已进队 · 排队等位", pct: 0 };
  if (s0.submitted) return { cls: "ok", txt: "✅ 提交成功 · 轮询中", pct: 0 };
  if (s0.retry > 0) return { cls: "warn", txt: `🔄 提交失败 · 第 ${s0.retry} 次重试`, pct: 0 };
  if (s0.submitting) return { cls: "warn", txt: "⏳ 提交中", pct: 0 };
  return { cls: "", txt: "⏸ 等待提交", pct: 0 };
}

function drvServerMeta(t) {
  const meta = t.meta || {};
  const proj = meta.name || "";
  const base = { cb: null, proj };
  switch (t.kind) {
    case "segment-batch": return { ...base, kind: "batch", id: "Batch", segs: meta.segs || [], submit_gap: meta.submit_gap || 0 };
    case "keyframe-batch": return { ...base, kind: "keyframe-batch", id: "KeyframeBatch" };
    case "segment": return { ...base, kind: "seg", id: meta.seg };
    case "cascade": return { ...base, kind: "cascade", id: meta.start || "cascade" };
    case "keyframe": return { ...base, kind: "keyframe", id: meta.seg };
    case "asset": return { ...base, kind: "asset", id: meta.id };
    case "merge": return { ...base, kind: "merge", id: (meta.eps || []).join(",") };
    default: return { ...base, kind: t.kind, id: meta.id || meta.seg || "Task" };
  }
}

async function drvAdoptRunning() {
  try {
    const r = await apiGet("/api/drama-review/tasks");
    const list = (r && r.tasks) || [];
    for (const t of list) {
      if (t.status !== "running" && t.status !== "queued") continue;
      const meta = drvServerMeta(t);
      if (!DRV.tasks.has(t.id)) DRV.tasks.set(t.id, meta);
      const d = DRV.dock.get(t.id) || { meta, startedAt: Date.now(), finishedAt: 0 };
      d.meta = d.meta || meta;
      d.t = { status: t.status, jobs: t.jobs || 0, done: t.done || 0, log: t.log || [] };
      DRV.dock.set(t.id, d);
      if (t.kind === "segment-batch" && (t.meta && t.meta.name) === DRV.project) {
        DRV.batchPid = t.id;
        DRV.batchSegs = ((t.meta && t.meta.segs) || []).slice();
        DRV.batchGap = (t.meta && t.meta.submit_gap) || 0;
      }
    }
    if (DRV.tasks.size && typeof window.__drvPollingActive === "undefined") {
      window.__drvPollingActive = setInterval(drvPollStep, 2000);
    }
    drvRenderTasks();
  } catch (e) { /* 忽略 */ }
}

async function drvPollStep() {
  if (!DRV.tasks.size) { clearInterval(window.__drvPollingActive); window.__drvPollingActive = undefined; drvRenderTasks(); return; }
  for (const [tid, meta] of [...DRV.tasks.entries()]) {
    try {
      const t = await apiGet("/api/drama-review/tasks/" + tid);
      const d = DRV.dock.get(tid) || { meta, startedAt: Date.now(), finishedAt: 0 };
      d.meta = d.meta || meta;
      d.t = t;
      if ((t.status === "done" || t.status === "failed") && !d.finishedAt) d.finishedAt = Date.now();
      DRV.dock.set(tid, d);
      const target = meta.kind === "asset" ? `#drvLog_${meta.id}`
        : meta.kind === "epmergeall" ? "#drvEpLog"
        : meta.kind === "epmerge" ? `#drvEpLog_${meta.id}`
        : `#drvSegLog_${meta.id}`;
      const el = $(target);
      if (el) {
        const last = (t.log || []).slice(-1)[0] || "";
        el.textContent = (t.status === "running" ? "⏳" : t.status === "done" ? "✅" : t.status === "failed" ? "❌" : "") + " " + last;
        el.title = (t.log || []).slice(-30).join("\n");
      }
      if (t.status === "done" || t.status === "failed" || t.status === "cancelled") {
        if (meta.kind === "batch") {
          const sum = (t.log || []).slice().reverse()
            .find((l) => l.includes("结束")) || (t.log || []).slice(-1)[0] || "";
          toast(t.status === "cancelled" ? "⏹ 批量任务已取消" : (sum || "批量任务结束"),
                t.status === "done" ? "ok" : "warn");
        } else if (t.status === "failed") {
          toast(drvTaskLabel(meta) + " 失败", "warn");
        } else if (t.status === "cancelled") {
          toast("⏹ " + drvTaskLabel(meta) + " 已取消", "info");
        }
        if (meta.cb) meta.cb();
        DRV.tasks.delete(tid);
      }
    } catch (e) { /* 单任务失败不阻断 */ }
  }
  drvRenderTasks();
}

/* ==================== 主渲染 ==================== */
function drvRender() {
  const box = $("#drvBody");
  if (!DRV.project) { box.innerHTML = '<div class="d-empty">请先选择作品</div>'; return; }
  if (DRV.mode === "script") {
    drvRenderScript();
  } else if (DRV.mode === "assets") {
    drvRenderAssets();
  } else if (DRV.mode === "episodes") {
    drvRenderEpisodes();
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
          <p>剧本审核 · 资产审核（角色/道具/场景/怪物）· 分镜审核（左图右视频）· 剧集审核（整集成片）</p>
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
              <button class="d-btn sm primary" data-mode="script">📜 剧本审核</button>
              <button class="d-btn sm" data-mode="assets">🖼 资产审核</button>
              <button class="d-btn sm" data-mode="segments">🎬 分镜审核</button>
              <button class="d-btn sm" data-mode="episodes">📺 剧集审核</button>
            </div>
            <div class="d-row" id="drvEpRow" style="margin-top:8px;flex-wrap:wrap"></div>
          </div>
          <button class="d-btn sm" id="drvMergeFull" style="margin-top:8px">✅ 合并全部集为连播</button>
        </section>
        <section class="st-right">
          <div id="drvTasks" class="drv-tasks" hidden></div>
          <div id="drvBody" class="drv-body"></div>
        </section>
      </div>
    </div>`;

  DRV.mode = "script";
  studioShell.bindTypeButton("drvType", "review");
  drvLoadProjects().then(() => {
    drvSetupModes(el);
    drvSetupEps(el).then(() => { drvRender(); drvRenderTasks(); drvAdoptRunning(); });
    if (!window.__drvAdoptTimer) window.__drvAdoptTimer = setInterval(drvAdoptRunning, 8000);
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