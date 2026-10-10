/* ============================================================================
 * studio-shell.js — 创作工作台 v2 共享组件
 *   欢迎区 / 创作类型切换（图片生成 · 视频生成 · 短剧审核）/ 生成设置弹窗
 *   （比例 · 分辨率 · 生成数量 · 自定义像素预览）
 *   供 image-studio.js / video-studio.js 复用；短剧审核页复用 woo-* 样式。
 * ============================================================================ */
(function () {
  "use strict";
  const $ = (s, c) => (c || document).querySelector(s);
  const $$ = (s, c) => Array.from((c || document).querySelectorAll(s));

  const TYPE_OPTS = [
    { tab: "image", icon: "🖼", label: "图片生成" },
    { tab: "video", icon: "🎬", label: "视频生成" },
    { tab: "review", icon: "🎬", label: "短剧审核" },
  ];

  const SIZE_FACTOR = { "1K": 1, "2K": 2, "3K": 3, "4K": 4 };
  const SIZE_LABEL = { "1K": "标清 1K", "2K": "高清 2K", "3K": "超清 3K", "4K": "超清 4K" };

  /* ---------- 欢迎区 ---------- */
  function hero(title, sub) {
    return (
      '<div class="woo-hero">' +
      "<h1>" + title + "</h1>" +
      (sub ? "<p>" + sub + "</p>" : "") +
      "</div>"
    );
  }

  /* ---------- 创作类型切换 ---------- */
  function typeButton(id, curTab) {
    const cur = TYPE_OPTS.find((o) => o.tab === curTab) || TYPE_OPTS[0];
    return (
      '<div class="woo-type-wrap" id="' + id + 'Wrap">' +
      '<button type="button" class="woo-type-btn" id="' + id + '">' +
      cur.icon + " <b>" + cur.label + '</b><span class="woo-caret">▾</span>' +
      "</button>" +
      '<div class="woo-type-menu hidden" id="' + id + 'Menu">' +
      TYPE_OPTS.map(function (o) {
        return (
          '<button type="button" data-tab="' + o.tab + '" class="' + (o.tab === curTab ? "active" : "") + '">' +
          o.icon + " " + o.label +
          "</button>"
        );
      }).join("") +
      "</div>" +
      "</div>"
    );
  }

  function bindTypeButton(id, curTab) {
    const btn = $("#" + id);
    const menu = $("#" + id + "Menu");
    if (!btn || !menu) return;
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      menu.classList.toggle("hidden");
    });
    document.addEventListener("click", function (e) {
      const wrap = $("#" + id + "Wrap");
      if (wrap && !wrap.contains(e.target)) menu.classList.add("hidden");
    });
    $$("button[data-tab]", menu).forEach(function (b) {
      b.addEventListener("click", function () {
        menu.classList.add("hidden");
        const tab = b.dataset.tab;
        if (typeof window.switchStudioTab === "function") window.switchStudioTab(tab);
        else if (window.location) window.location.reload();
      });
    });
  }

  /* ---------- 生成设置弹窗 ---------- */
  /* opts: {
   *   title, note,
   *   ratios: ["1:1", ...],                必填
   *   px: { "16:9": "1312×736", ... },     比例 → 像素 @1K（可选，用于尺寸预览）
   *   sizes: ["1K","2K",...],              可选；缺省隐藏「分辨率」节
   *   counts: [1,2,...],                   可选；缺省隐藏「生成数量」节
   *   countLimit: 4,                       可选；>limit 的档位禁用并置灰
   *   cur: { ratio, size, count },         初始值
   *   sizeTitle, countTitle,
   *   onSubmit: fn({ratio,size,count})     确认回调（必填）
   * }
   */
  function ratioModal(opts) {
    const cur = opts.cur || {};
    const sel = {
      ratio: cur.ratio || (opts.ratios && opts.ratios[0]) || "16:9",
      size: cur.size || "2K",
      count: cur.count || 1,
    };

    const ratioPx = function (r) { return (opts.px && opts.px[r]) || ""; };
    const dims = function () {
      if (!opts.sizes) return null;
      const k = SIZE_FACTOR[sel.size] || 2;
      const parts = String(sel.ratio).split(":").map(Number);
      const hint = ratioPx(sel.ratio);
      const base = hint ? hint.split("×").map(function (x) { return parseInt(x, 10); }) : null;
      if (base && base.length === 2 && base[0] && base[1]) {
        return [Math.round(base[0] * k), Math.round(base[1] * k)];
      }
      if (parts.length === 2 && parts[0] && parts[1]) {
        const h = sel.size === "4K" ? 2160 : sel.size === "3K" ? 1620 : sel.size === "1K" ? 720 : 1440;
        return [Math.round(h * parts[0] / parts[1]), h];
      }
      return [1920, 1080];
    };

    const mask = document.createElement("div");
    mask.className = "rag-modal-mask";
    mask.innerHTML =
      '<div class="rag-modal woo-pop">' +
      '<div class="rag-modal-hd"><h4>' + (opts.title || "生成设置") + "</h4>" +
      '<button type="button" class="icon-btn rg-modal-x">✕</button></div>' +
      '<div class="rag-modal-bd woo-pop-bd">' +
      '<div class="woo-pop-sec"><div class="woo-pop-label">' + (opts.ratioTitle || "选择比例") + "</div>" +
      '<div class="woo-pop-chips">' +
      opts.ratios.map(function (r) {
        return '<button type="button" class="woo-pop-chip" data-ratio="' + r + '">' + r + "</button>";
      }).join("") +
      "</div></div>" +
      (opts.sizes ? '<div class="woo-pop-sec"><div class="woo-pop-label">' + (opts.sizeTitle || "分辨率") + "</div>" +
        '<div class="woo-pop-chips">' +
        opts.sizes.map(function (s) {
          return '<button type="button" class="woo-pop-chip" data-size="' + s + '">' + (SIZE_LABEL[s] || s) + "</button>";
        }).join("") +
        "</div></div>" : "") +
      (opts.counts ? '<div class="woo-pop-sec"><div class="woo-pop-label">' + (opts.countTitle || "生成数量") + "</div>" +
        '<div class="woo-pop-chips">' +
        opts.counts.map(function (c) {
          const over = opts.countLimit && c > opts.countLimit;
          return '<button type="button" class="woo-pop-chip" data-count="' + c + '"' + (over ? ' disabled title="单次最多 ' + opts.countLimit + " 张" + '"' : "") + ">" + c + "</button>";
        }).join("") +
        "</div></div>" : "") +
      (opts.sizes && opts.counts ? '<div class="woo-pop-sec"><div class="woo-pop-label">尺寸</div>' +
        '<div class="woo-pop-row">' +
        '<span class="woo-pop-inlabel">W</span><input type="number" class="woo-pop-input" id="wooDimW" min="128">' +
        '<span class="woo-pop-inlabel">H</span><input type="number" class="woo-pop-input" id="wooDimH" min="128">' +
        '<span class="woo-pop-hint">px（按所选比例与档位预览，实际画布以比例 + 档位为准）</span>' +
        "</div></div>" : "") +
      (opts.note ? '<div class="woo-pop-note">' + opts.note + "</div>" : "") +
      "</div>" +
      '<div class="woo-pop-foot">' +
      '<button type="button" class="d-btn" data-close>取消</button>' +
      '<button type="button" class="d-btn primary" data-ok>完成</button>' +
      "</div>" +
      "</div>";
    document.body.appendChild(mask);

    const close = function () { mask.remove(); };
    mask.addEventListener("click", function (e) { if (e.target === mask) close(); });
    mask.querySelector(".rg-modal-x").addEventListener("click", close);
    $$("[data-close]", mask).forEach(function (b) { b.addEventListener("click", close); });

    const paint = function () {
      $$("[data-ratio]", mask).forEach(function (b) { b.classList.toggle("active", b.dataset.ratio === sel.ratio); });
      if (opts.sizes) {
        $$("[data-size]", mask).forEach(function (b) { b.classList.toggle("active", b.dataset.size === sel.size); });
      }
      if (opts.counts) {
        $$("[data-count]", mask).forEach(function (b) { b.classList.toggle("active", Number(b.dataset.count) === sel.count); });
      }
      if (opts.sizes && opts.counts) {
        const d = dims();
        const w = $("#wooDimW", mask), h = $("#wooDimH", mask);
        if (w && d) w.value = d[0];
        if (h && d) h.value = d[1];
      }
    };
    $$("[data-ratio]", mask).forEach(function (b) {
      b.addEventListener("click", function () { sel.ratio = b.dataset.ratio; paint(); });
    });
    $$("[data-size]", mask).forEach(function (b) {
      b.addEventListener("click", function () { if (!b.disabled) { sel.size = b.dataset.size; paint(); } });
    });
    $$("[data-count]", mask).forEach(function (b) {
      b.addEventListener("click", function () { if (!b.disabled) { sel.count = Number(b.dataset.count); paint(); } });
    });
    mask.querySelector("[data-ok]").addEventListener("click", function () {
      if (typeof opts.onSubmit === "function") opts.onSubmit({ ratio: sel.ratio, size: sel.size, count: sel.count });
      close();
    });
    paint();
  }

  function costTag(html) {
    return '<span class="woo-cost">' + html + "</span>";
  }

  window.studioShell = {
    hero: hero,
    typeButton: typeButton,
    bindTypeButton: bindTypeButton,
    ratioModal: ratioModal,
    costTag: costTag,
  };
})();