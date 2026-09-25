/* ============================================================================
   短剧创作工作台（系统级 · 左侧模块 Tab 列表）
   ----------------------------------------------------------------------------
   不绑定任何账号：创作技能已作为系统内置技能安装（app/skills/），
   任何账号都能看到并使用，统一收在「短剧创作」一个分组：
     · 短剧创作（7 个，小说创作排第一）：sumeru-worldbuilder（小说创作）/
       novel-outline / novel-characters / novel-art / novel-script /
       novel-storyboard / agnes-media(出视频)
   布局：左侧 = 模块 Tab 列表（单分组），右侧 = 当前模块的表单卡。
   点击模块「启动」→ 激活本会话受限命令作用域 → 收集参数组指令 → 切回对话，
   由当前会话的智能体按对应 SKILL.md 流水线执行。
   依赖 app.js 全局工具：$ / $$ / apiGet / apiPost / escapeHtml / toast /
   chatInput / State / sendMessage / closeStudioView。
   ============================================================================ */

const DRAMA_TITLE_DFT = "我的AI短剧";
const SUMERU_TITLE_DFT = "我的新书";

/* 公共说明尾巴（短剧技能指令的收尾约束） */
function DRAMA_TAIL(title) {
  return `
要求：严格按该技能的 SKILL.md 逐步执行。先用 read_skill 读取技能说明，再按它的 Step 顺序走：定位输入 → seed/chunk → 逐批生成 → validate → render；所有脚本（node {baseDir}/scripts/*.mjs，零依赖）与产物都放在**交付目录绝对路径** data/users/<当前用户>/deliverables/短剧/${title}/ 下（<当前用户> 通常为 Mirror，先 ls data/users/ 确认，等于系统提示词中的交付物目录；文件工具相对路径以用户工作区为基准，shell 命令须先 cd data/users/<当前用户> 或写全 data/users/ 前缀，禁止裸写 deliverables/... 相对路径）。validate 必须全绿才 render，报了质量门就就地修正后重跑。出图步骤统一用 Agnes 图片创作台（read_skill agnes-media 后照 SKILL.md 执行）：先写 image.sheet 提示词（必须含当前画风预设的 render 句、全英文、不得出现人名），再执行 python app/skills_builtin/agnes-media/media.py image --prompt "<image.sheet>" --size 2K --ratio 16:9，stdout 出现 FILE= 即成功，把该文件复制到 images/<slug>-sheet.png；平台有 Agnes 出图能力，不允许以"没有出图环境"为由跳过出图。全程用中文汇报，完成后用 2–3 句话列出各产物文件路径。`;
}

/* 公共说明尾巴（sumeru 小说写作技能指令的收尾约束） */
function SUMERU_TAIL(title) {
  return `
要求：严格按该技能的 SKILL.md 执行。先用 read_skill 读取技能说明，按其 Step 顺序走；先在**交付目录绝对路径** data/users/<当前用户>/deliverables/小说/${title}/ 建根目录（<当前用户> 通常为 Mirror，先 ls data/users/ 确认，等于系统提示词中的交付物目录；chapters/、.sumeru/、publish/ 等一律放在其内）。文件工具相对路径以用户工作区为基准（deliverables/小说/${title}/ 即该绝对目录）；shell 命令在服务根目录执行，必须先 cd data/users/<当前用户> 或写全 data/users/ 前缀，禁止裸写 mkdir -p deliverables/...（会落到服务根目录，用户看不到）；不要触碰工作区之外的任何路径；涉及章节级批量操作的步骤，按 SKILL.md 规则使用子 Agent 并行处理（每个子 Agent 最多负责 3 个章节），完成后必须校验章节数与细纲一致。全程用中文汇报，完成后用 2–3 句话列出各产物文件路径。`;
}

/* 字段渲染（id 规则 drm-<模块健>-<字段健>） */
function drmFieldHtml(mod, f) {
  const id = `drm-${mod}-${f.key}`;
  const ph = f.placeholder ? ` placeholder="${f.placeholder}"` : "";
  let inner = "";
  if (f.type === "textarea") {
    inner = `<textarea class="d-input drm-src-text" id="${id}" rows="${f.rows || 5}"${ph}></textarea>`;
  } else if (f.type === "select") {
    const opts = (f.options || []).map((o) => {
      const v = (Array.isArray(o) ? o[0] : o);
      const l = (Array.isArray(o) ? o[1] : o);
      const sel = (`${v}` === `${f.default}` ? " selected" : "");
      return `<option value="${v}"${sel}>${l}</option>`;
    }).join("");
    inner = `<select class="d-input" id="${id}">${opts}</select>`;
  } else if (f.type === "number") {
    inner = `<input type="number" class="d-input" id="${id}" value="${f.default == null ? "" : f.default}" min="${f.min == null ? 0 : f.min}"${ph}>`;
  } else if (f.type === "path") {
    // path 字段（srcPath 等）：文本输入 + 「📎 上传并填路径」按钮。
    // #4 修复：旧缓存版前端曾把「✅ 文件已上传成功！文件信息…」提示文案错填进路径字段，
    // 导致模型 read_file 读到一堆"提示话术"而非真实文件。新版改为正规上传链：
    // 选本地文件 → POST /api/upload → 服务器返回 uploads/up_xxx.ext 真实路径 → 填入输入框。
    inner = `
      <div class="drm-path-row">
        <input type="text" class="d-input" id="${id}" value="${f.default == null ? "" : f.default}"${ph} spellcheck="false">
        <button type="button" class="d-btn ghost drm-path-up" data-up="${mod}-${f.key}" title="上传本地文件到 uploads/ 并自动填入工作区相对路径">📎 上传文件</button>
      </div>
      <div class="drm-path-note" id="drm-note-${mod}-${f.key}"></div>
      <input type="file" class="drm-file-input" id="drm-file-${mod}-${f.key}" hidden>`;
  } else {
    inner = `<input type="text" class="d-input" id="${id}" value="${f.default == null ? "" : f.default}"${ph} spellcheck="false">`;
  }
  return `<div class="st-block"><div class="st-block-hd"><span>${f.label}</span>${f.hint ? `<span class="st-block-tip">${f.hint}</span>` : ""}</div>${inner}</div>`;
}

function drmRadioHtml(mod, f) {
  const radios = (f.options || []).map((o) => {
    const v = Array.isArray(o) ? o[0] : o;
    const l = Array.isArray(o) ? o[1] : o;
    const sel = (`${v}` === `${f.default}` ? " checked" : "");
    return `<label class="drm-radio"><input type="radio" name="drm-${mod}-${f.key}" value="${v}"${sel}><span>${l}</span></label>`;
  }).join("");
  return `<div class="st-block"><div class="st-block-hd"><span>${f.label}</span></div><div class="drm-radio-row">${radios}</div></div>`;
}

function drmCollect(el, mod, fields) {
  const v = {};
  for (const f of fields) {
    const id = `drm-${mod}-${f.key}`;
    const node = $("#" + id, el);
    if (node) {
      v[f.key] = node.value ? node.value.trim() : (f.default == null ? "" : f.default);
    } else {
      const checked = $('input[name="drm-' + mod + "-" + f.key + '"]:checked', el);
      v[f.key] = checked ? checked.value : (f.default == null ? "" : f.default);
    }
  }
  return v;
}

function drmSrcNote(v, textKey, pathKey) {
  if (v[pathKey]) return `源文件（工作区相对路径，先用 read_file 读取）：${v[pathKey]}`;
  if (v[textKey]) return `源材料（直接粘贴，需先落到临时 .txt 再进技能流程）：\n\n${v[textKey]}`;
  return "";
}

/* 项目名安全化：标题会拼进交付目录路径（deliverables/小说/<书名>/ 等），
   必须去除路径分隔符/空白等非法字符，空则回退默认标题（防 `..`、反斜杠逃逸）。 */
function drmSafeTitle(t, dft) {
  const s = String(t || "").trim().replace(/[\\/:*?"<>|\s]+/g, "").slice(0, 30);
  return s || dft;
}

const _PLATFORM_OPTS = [["起点", "起点中文网"], ["番茄", "番茄小说"], ["晋江", "晋江文学城"], ["知乎", "知乎盐选"], ["刺猬猫", "刺猬猫"], ["不限", "不限平台"]];

/* ————————————————————— 分组一：短剧创作（novel-* 前导技能） ————————————————————— */
const DRAMA_MODULES = [
  {
    key: "outline",
    skill: "novel-outline",
    icon: "🧩",
    name: "改编大纲",
    color: "#7c5cff",
    desc: "把小说改编成短剧大纲五件套：改编说明 / 人物表 / 爽点表 / 分集梗概 / 资产清单，产出 outline.json + 评审报告（14 道质量门），下游全部模块的数据源。",
    fields: [
      { key: "eps", label: "总集数", type: "number", default: 60 },
      { key: "epLen", label: "单集时长（秒）", type: "number", default: 120, hint: "AI 短剧常见 60–150s" },
      { key: "genre", label: "题材", type: "text", placeholder: "如：逆袭复仇 / 现代虐恋 / 古装权谋" },
      { key: "mode", label: "改编幅度", type: "select", default: "抽核", options: [["抽核", "抽核（提取爽点重构，推荐）"], ["忠实", "忠实还原"], ["借壳", "借人物世界观原创新故事"]] },
      { key: "srcText", label: "原著正文（粘贴）", type: "textarea", rows: 6, placeholder: "粘贴整本小说或章节文本（建议 ≥ 3000 字）。若无现成正文，也可先给正文文件路径。" },
      { key: "srcPath", label: "或：正文文件路径", type: "path", placeholder: "工作区相对路径，如 uploads/我的小说.txt" },
    ],
    build(v, title) {
      if (!v.srcPath && !v.srcText) return "";
      return `请使用 novel-outline 技能，把一部小说改编成 AI 短剧大纲。
项目/剧名：${title}
总集数 × 单集时长：${v.eps} 集 × ${v.epLen} 秒　题材：${v.genre || "（未提供，请按正文判断）"}　改编幅度：${v.mode || "抽核"}
${drmSrcNote(v, "srcText", "srcPath")}${DRAMA_TAIL(title)}`;
    },
  },
  {
    key: "characters",
    skill: "novel-characters",
    icon: "👥",
    name: "拆角色",
    color: "#ff9f43",
    desc: "从小说拆出角色表 / 人物画像 / 形象提示词 / 音色提示词（含角色设定图提示词）。有 outline.json 就给出来走 seed，人物清单直接沿用大纲分档。",
    fields: [
      { key: "style", label: "出图画风", type: "select", default: "photoreal", options: [["photoreal", "真人写实（默认）"], ["inkwash", "水墨风（Ink Wash）"], ["guofeng", "国风厚涂（Guofeng）"], ["cyberpunk", "赛博朋克（Cyberpunk）"], ["photography", "写实摄影（Photorealistic）"], ["ghibli", "吉卜力手绘（Studio Ghibli）"], ["pixar3d", "皮克斯3D（Pixar）"], ["disney3d", "迪士尼3D（Disney）"], ["webtoon", "韩漫（Webtoon）"], ["shinkai", "新海诚日漫（Shinkai）"], ["chibi", "Q版（Chibi）"]] },
      { key: "lang", label: "报告语言", type: "text", default: "中文", placeholder: "中文 / English / 日本語 …" },
      { key: "outlinePath", label: "可选：outline.json 路径", type: "path", placeholder: "有上一模块产物就填，如 deliverables/短剧/<剧名>/outline.json，会走 seed 沿用角色清单" },
      { key: "srcText", label: "原著正文（粘贴）", type: "textarea", rows: 5, placeholder: "无 outline 时需给原文。粘贴正文或直接给文件路径。" },
      { key: "srcPath", label: "或：正文文件路径", type: "path", placeholder: "工作区相对路径" },
    ],
    build(v, title) {
      if (!v.srcPath && !v.srcText && !v.outlinePath) return "";
      return `请使用 novel-characters 技能，为这部短剧拆角色设定。
项目/剧名：${title}
出图画风：${v.style || "photoreal"}　报告语言：${v.lang || "中文"}
${v.outlinePath ? `已有的 outline.json（工作区相对路径，先 read_file 并 seed）：${v.outlinePath}` : ""}
${drmSrcNote(v, "srcText", "srcPath")}${DRAMA_TAIL(title)}`;
    },
  },
  {
    key: "art",
    skill: "novel-art",
    icon: "🎨",
    name: "美术设定",
    color: "#22c1a3",
    desc: "给 AI 短剧出美术设定集：场景设计意图 / 一致性锚点 / 光照时段变体 / 空景提示词 + 叙事道具，产出 art.json（11 道质量门）。输入优先级：outline.json > 小说原文 > 手写场景清单。",
    fields: [
      { key: "outlinePath", label: "①最佳：outline.json 路径", type: "path", placeholder: "有 outline 就填，场景清单与出现集直接 seed 预填" },
      { key: "srcPath", label: "②或：小说原文文件路径", type: "path", placeholder: "工作区相对路径" },
      { key: "listText", label: "③或：手写场景/道具清单（粘贴）", type: "textarea", rows: 5, placeholder: "如：\n主场景：现代别墅客厅（夜）\n叙事道具：一支旧怀表（贯穿全集）…" },
      { key: "styleHint", label: "可选：画风倾向", type: "text", placeholder: "如：都市现代 / 民国 / 古装…（影响空景提示词方向，可留空）" },
    ],
    build(v, title) {
      if (!v.outlinePath && !v.srcPath && !v.listText) return "";
      const style = v.styleHint ? `画风倾向：${v.styleHint}` : "";
      return `请使用 novel-art 技能，为这部短剧出美术设定集（场景 + 叙事道具）。
项目/剧名：${title}
${style}
${v.outlinePath ? `输入的 outline.json（工作区相对路径，先 read_file 并 seed）：${v.outlinePath}` : ""}
${v.outlinePath || v.srcPath ? "" : `手写场景/道具清单：\n\n${v.listText}`}
${!v.outlinePath && v.srcPath ? `小说原文文件路径（工作区相对路径，先 read_file）：${v.srcPath}` : ""}${DRAMA_TAIL(title)}`;
    },
  },
  {
    key: "script",
    skill: "novel-script",
    icon: "📜",
    name: "写剧本",
    color: "#f368e0",
    desc: "把大纲的分集梗概落成结构化场次 + 节拍流（动作节拍 ⇄ 台词行，逐句带说话人与语气），产出 script.json（10 道质量门）。剧本必先有 outline.json。",
    fields: [
      { key: "outlinePath", label: "outline.json 路径", type: "path", placeholder: "必填（工作区相对路径），剧本直接上游" },
      { key: "outlineText", label: "或：粘贴 outline.json 内容", type: "textarea", rows: 5, placeholder: "没有路径文件就整份贴进来，先落到临时文件" },
      { key: "batch", label: "一次写几集", type: "number", default: 3, min: 1, hint: "脚本按批写，≤3 集一批与上游对齐" },
      { key: "artPath", label: "可选：art.json 路径", type: "path", placeholder: "填了则光照/场景/道具对照 art.json 对账" },
    ],
    build(v, title) {
      if (!v.outlinePath && !v.outlineText) return "";
      return `请使用 novel-script 技能，为这部短剧写剧本（结构化场次 + 节拍流）。
项目/剧名：${title}
一次写 ${v.batch || 3} 集（若大纲更长，按批次做完再继续）
${v.outlinePath ? `outline.json（工作区相对路径，先 read_file 并 seed）：${v.outlinePath}` : `outline.json 内容（先落到临时 .txt 再进技能流程）：\n\n${v.outlineText}`}
${v.artPath ? `art.json（工作区相对路径，供光照/场景/道具对账）：${v.artPath}` : ""}${DRAMA_TAIL(title)}`;
    },
  },
  {
    key: "storyboard",
    skill: "novel-storyboard",
    icon: "🎬",
    name: "出分镜",
    color: "#ff6b6b",
    desc: "把剧本切成分镜三层结构：段（≤15s）→ 分镜（认领节拍）→ 分镜图（关键帧），每段自带 Agnes 视频模型提示词（如 agnes-video-2.5），产出 storyboard.json（17 道质量门）。script.json 是硬前提。",
    fields: [
      { key: "scriptPath", label: "script.json 路径", type: "path", placeholder: "必填（工作区相对路径），分镜离开剧本没有意义" },
      { key: "scriptText", label: "或：粘贴 script.json 内容", type: "textarea", rows: 5, placeholder: "没有路径文件就整份贴进来，先落到临时文件" },
      { key: "batch", label: "一批切几集", type: "number", default: 3, min: 1, hint: "跟剧本批次走，默认 ≤3 集" },
      { key: "upstreams", label: "可选：outline/cast/art 路径", type: "text", placeholder: "逗号分隔，如 deliverables/短剧/<剧名>/outline.json, .../cast.json, .../art.json" },
    ],
    build(v, title) {
      if (!v.scriptPath && !v.scriptText) return "";
      const ups = (v.upstreams || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean);
      const upsNote = ups.length
        ? `可上游（有则用，供提示词禁人名/场景名回显）：${ups.join("；")}`
        : "无额外上游（可直接用脚本自带的场景/角色名）。";
      return `请使用 novel-storyboard 技能，为这部短剧出分镜（段/分镜/分镜图 + Agnes 视频模型提示词（如 agnes-video-2.5））。
项目/剧名：${title}
一批切 ${v.batch || 3} 集
${v.scriptPath ? `script.json（工作区相对路径，先 read_file）：${v.scriptPath}` : `script.json 内容（先落到临时 .txt 再进技能流程）：\n\n${v.scriptText}`}
${upsNote}${DRAMA_TAIL(title)}`;
    },
  },
  {
    key: "video",
    skill: "agnes-media",
    icon: "🎥",
    name: "出视频",
    color: "#e84393",
    desc: "把 storyboard.json 里每一段的 h3Prompt 用 Agnes 视频模型逐段出视频片段（agnes-media video，按段秒数×24fps 换算帧数），再用 ffmpeg 拼成整集，落到 deliverables/短剧/<剧名>/videos/。storyboard.json 是硬前提。",
    fields: [
      { key: "storyboardPath", label: "storyboard.json 路径", type: "path", placeholder: "必填（工作区相对路径），视频逐段从分镜的 h3Prompt 出" },
      { key: "storyboardText", label: "或：粘贴 storyboard.json 内容", type: "textarea", rows: 5, placeholder: "没有路径文件就整份贴进来，先落到临时文件" },
      { key: "eps", label: "出哪些集（可选）", type: "text", placeholder: "如 1-3 / 1,4,7 / 留空=全部（默认先出 1–3 集样片）" },
    ],
    build(v, title) {
      if (!v.storyboardPath && !v.storyboardText) return "";
      return `请使用 agnes-media 技能，为这部短剧出整集视频（输入 storyboard.json → 逐段用 h3Prompt 出视频片段 → ffmpeg 拼集）。
项目/剧名：${title}
${v.storyboardPath ? `storyboard.json（工作区相对路径，先 read_file）：${v.storyboardPath}` : `storyboard.json 内容（先落到临时文件再进技能流程）：\n\n${v.storyboardText}`}
范围：${v.eps ? `仅 ${v.eps} 集` : "全部集（先出 1–3 集样片给用户确认规格，再批量）"}
要求：严格按该技能的 SKILL.md 逐步执行。先用 read_skill agnes-media 读取技能说明，再执行 python app/skills_builtin/agnes-media/media.py video --prompt "<逐字取该段 h3Prompt>" --frames <该段秒数×24 换算并取最近的 8n+1，上限 441> --fps 24 --ratio 3:2，默认走参数缺省的画面比例；stdout 出现 FILE= 即成功，把每个片段复制到 data/users/<当前用户>/deliverables/短剧/${title}/videos/ 下按帧序命名；一集各段齐了再用 ffmpeg -f concat -safe 0 拼成 videos/<集号>/E<集号>.mp4。出图之前的既有交付不受影响；每集完成后用 2–3 句话汇报片段数量 + 是否拼集成功。全程用中文汇报。`;
    },
  },
];

/* ————————————————————— 分组二：小说创作（xindoo/sumeru 技能族） ————————————————————— */
const SUMERU_MODULES = [
  {
    key: "s_world",
    skill: "sumeru-worldbuilder",
    icon: "🌍",
    name: "小说创作",
    color: "#a06bff",
    desc: "网文创作一站式主控：自动编排 选题 → 大纲 → 章节撰写 → 逻辑审查 → 内容润色 → 完稿校验 完整链路，统一的 .sumeru/session/ 工作记忆，从 0 到 1 完成一部作品。",
    fields: [
      { key: "genre", label: "题材", type: "text", placeholder: "如：玄幻 / 都市 / 悬疑 / 甜宠" },
      { key: "chapters", label: "目标章节数", type: "number", default: 20, min: 1 },
      { key: "platform", label: "目标平台", type: "select", default: "不限", options: _PLATFORM_OPTS },
      { key: "style", label: "文风 / 偏好（可选）", type: "textarea", rows: 3, placeholder: "如：快节奏爽文、系统流、日常甜宠、玄学灵异…" },
      { key: "needs", label: "一句话创意 / 需求", type: "textarea", rows: 4, placeholder: "创意、金手指、核心设定、人设倾向… 只有一个大概的想法也没关系" },
    ],
    build(v, title) {
      return `请使用 sumeru-worldbuilder 技能，从零到一完整创作一部网文（自动协调选题、大纲、写作、审查、润色、完稿全流程）。
书名/项目名：${title}
题材：${v.genre || "（未提供，由 AI 拟定）"}　目标平台：${v.platform || "不限"}　目标章节数：约 ${v.chapters || 20} 章
${v.style ? `文风/偏好：\n${v.style}\n` : ""}需求/创意如下：
${v.needs || "（无特定创意，请 AI 自主构想一个完整故事。）"}
${SUMERU_TAIL(title)}`;
    },
  },
];

/* ————————————————————— 分组定义（左侧 Tab） —————————————————————
   已合并：小说创作（s_world）并入「短剧创作」分组，排在第一位。 */
const CREATIVE_GROUPS = [
  { key: "drama", label: "短剧创作", note: "系统级 · 小说创作 + novel-* 前导 + agnes-media", modules: SUMERU_MODULES.concat(DRAMA_MODULES) },
];

/* 用（skillKey, moduleKey）唯一标识一个模块 */
function DrmModule(moduleKey) {
  for (const g of CREATIVE_GROUPS) {
    const m = g.modules.find((x) => x.key === moduleKey);
    if (m) return m;
  }
  return null;
}

/* ————————————————————— 命令执行作用域（创作工作台专属） —————————————————————
   execute_command 默认不对普通用户开放；仅当在本工作台启动模块（且处于该会话线程内）
   才按「用户×线程」签发限时作用域（2h 保鲜，模块再次启动会续期）。切走工作台/开新会话即失效。
   后台判定见 app/workbench.py，API：/api/workbench/drama。 */
async function drmSetCmd(enabled) {
  try {
    const r = await apiPost("/api/workbench/drama", { enabled, thread_id: State.threadId });
    return r;
  } catch (e) {
    console.warn("workbench cmd scope:", e);
    return null;
  }
}
/* 供 app.js 调用：切走创作 tab / 关闭工作台时收回本线程的受限命令作用域 */
window.setDramaCmdEnabled = drmSetCmd;
window.deactivateDramaScope = () => drmSetCmd(false);

/* ————————————————————— 右侧模块表单渲染 ————————————————————— */
let _drmSel = DRAMA_MODULES[0].key;

function renderDrmModule(el, moduleKey) {
  if (DRM.embedded) drmUnembedChat();   // 先归还首页 #chat，避免重渲染表单把它销毁
  const m = DrmModule(moduleKey);
  if (!m) return;
  _drmSel = m.key;
  drmNavBadges(DRM.navEl);   // 同步流程导航条的 active 高亮
  const box = $("#drmModuleBox", el);
  box.innerHTML = `
    <div class="drm-card" style="--acc:${m.color}">
      ${(DRM.progress[m.key] && DRM.progress[m.key].status === "done") ? `<div class="drm-done-banner">✅ 本模块已完成运行（${new Date(DRM.progress[m.key].ts).toLocaleTimeString()}）· 交付已写入 deliverables/，可再次「🚀 启动」继续完善</div>` : ""}
      <div class="drm-card-hd">
        <span class="drm-icon">${m.icon}</span>
        <div class="drm-titles">
          <div class="drm-name">${m.name}</div>
          <div class="drm-skill">${m.skill} · 内置技能</div>
        </div>
      </div>
      <div class="drm-desc">${m.desc}</div>
      <div class="drm-fields">
        <div class="st-block drm-title-block">
          <div class="st-block-hd"><span>${m.key === "s_world" ? "书名 / 项目名（可选）" : "剧名（可选）"}</span><span class="st-block-tip">${m.key === "s_world" ? "留空用默认「我的新书」" : "留空用默认「我的AI短剧」"}；填了则按此建独立交付目录，多部作品互不覆盖</span></div>
          <input type="text" class="d-input" id="drm-title-${m.key}" placeholder="${m.key === "s_world" ? "如：重生之都市仙尊" : "如：星际边缘的曙光"}" spellcheck="false">
        </div>
        ${m.fields.map((f) => (f.type === "radio" ? drmRadioHtml(m.key, f) : drmFieldHtml(m.key, f))).join("")}
      </div>
      <div class="drm-actions">
        <button class="d-btn primary drm-run" data-run="${m.key}">🚀 启动 ${m.name}</button>
        <span class="d-empty">启动即开新会话执行，产物自动写入 deliverables/，全程不需跳转。</span>
      </div>
    </div>`;

  /* 启动模块 → 激活本会话受限命令作用域 → 把首页对话页缩小内嵌到工作台 → 直接用首页对话页发起这一轮 */
  const runBtn = $(".drm-run", box);
  if (runBtn) runBtn.addEventListener("click", async () => {
    const mod = DrmModule(runBtn.dataset.run);
    if (!mod) return;
    if (DRM.run && DRM.run.streaming) { toast("「" + mod.name + "」：当前有模块正在生成中，请先等本轮结束或点「⏹ 停止」", "warn"); return; }
    const group = CREATIVE_GROUPS.find((g) => g.modules.some((x) => x.key === mod.key)) || CREATIVE_GROUPS[0];
    const _tEl = $("#drm-title-" + mod.key, box);
    const title = drmSafeTitle(_tEl ? _tEl.value : "", mod.key === "s_world" ? SUMERU_TITLE_DFT : DRAMA_TITLE_DFT);
    const v = drmCollect(box, mod.key, mod.fields);
    const msg = mod.build(v, title);
    if (!msg) { toast("请先填写该模块的必填输入（正文粘贴 或 产物路径）", "error"); return; }
    if (typeof sendMessage !== "function") { toast("对话发送器未就绪，请刷新页面", "error"); return; }
    /* 【每步新会话】启动模块前先 /new 开一条全新线程，模块指令发到这条新线程：
       避免长线程上下文持续累积把 human 消息挤出 LLM 窗口（400 No user query found）。
       只更新前端 State.threadId 与对话消息区（工作台视图不动）。 */
    try {
      const _nk = await apiPost("/api/command", { command: "/new" });
      if (_nk && _nk.thread_id) {
        State.threadId = _nk.thread_id;
        const _inner = messagesInner();
        if (_inner) _inner.innerHTML = "";
        if (typeof closeProcGroup === "function") closeProcGroup();
        if (typeof resetTodoPanel === "function") resetTodoPanel();
        if (typeof renderWelcome === "function") renderWelcome();
        if (typeof updateChatTitle === "function") updateChatTitle("新对话");
        if (typeof refreshApprovalMode === "function") refreshApprovalMode();
        if (typeof refreshTopbar === "function") refreshTopbar();
        if (typeof loadThreads === "function") loadThreads().catch(() => {});
        toast("已为「" + mod.name + "」开启新会话线程（每步独立，避免上下文挤爆）", "success");
      }
    } catch (e) { console.warn("drm new session:", e); }
    await drmSetCmd(true);   // 受限命令作用域按「用户 × 新线程」签发
    drmStartRun(mod, title);
    DRM.run.streaming = true;
    /* 【点击启动 → 直接跳转到主页对话页面】不把 #chat 内嵌进工作台框内；
       关掉创作工作台，返回主页对话页从首条启动消息开始看完整生成流程。 */
    if (typeof closeStudioView === "function") closeStudioView();
    try {
      const _sent = await sendMessage(msg);           // 在主页对话页发起并流式渲染（token/工具/审批/待办全在页内）
      if (_sent === false && DRM.run) {
        // 上一轮还挂着（State.streaming 未复位）：本轮实际没发出去——回滚待命态，避免 ◔ 永久残影
        DRM.run.streaming = false;
        if (DRM.embedded) { drmClearProgress(DRM.run.moduleKey); drmNavBadges(DRM.navEl); drmSetLive(false, "● 等待回复"); }
        if (typeof openStudioView === "function") openStudioView("drama");
        toast("🍃 上一轮对话仍处于执行中，本轮未启动：请先「⏹ 停止」或等它结束", "warn");
      }
    } catch (e) {
      console.warn("drm launch:", e);
    } finally {
      if (DRM.run) DRM.run.streaming = false;
    }
  });

  /* path 字段（srcPath）上传 → 填入真实 uploads/ 路径（#4）
     旧缓存版前端曾把「已上传成功+文件信息」提示文案错填进 srcPath 字段，模型 read_file 读到的是
     话术而非文件。这里走常规 /api/upload 上传链，拿到真实工作区相对路径再回填输入框。 */
  $$(".drm-path-up", box).forEach((b) => {
    b.addEventListener("click", () => {
      const fi = $("#" + "drm-file-" + b.dataset.up, box);
      if (fi) fi.click();
    });
  });
  $$(".drm-file-input", box).forEach((fi) => {
    fi.addEventListener("change", async () => {
      const file = fi.files && fi.files[0];
      if (!file) return;
      const srcKey = (fi.id || "").replace(/^drm-file-/, "");
      const inputEl = $("#" + "drm-" + srcKey, box);
      const noteEl = $("#" + "drm-note-" + srcKey, box);
      const toastMsg = (m, t) => { try { toast(m, t); } catch (e) {} };
      try {
        const fd = new FormData();
        fd.append("file", file);
        const r = await fetch("/api/upload", { method: "POST", body: fd, credentials: "include" });
        if (r.status === 401) { if (typeof onUnauthorized === "function") onUnauthorized(); return; }
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        if (inputEl) inputEl.value = d.path || "uploads/";
        if (noteEl) {
          noteEl.textContent = `✅ 已上传：${d.name || file.name}（${fmtBytes ? fmtBytes(d.size) : d.size + "B"}）→ 工作区路径 ${d.path}`;
          noteEl.style.color = "var(--ok, #2e9e5b)";
        }
        toastMsg(`已上传 ${d.name || file.name}，路径已填入（#4 修复后为真实 uploads/ 路径）`);
      } catch (e) {
        if (noteEl) noteEl.textContent = "❌ 上传失败：" + (e.message || e);
        toastMsg("上传失败: " + (e.message || e), "error");
      } finally {
        fi.value = "";
      }
    });
  });
}

/* ————————————————————— 内嵌沉浸式会话（复用首页对话页，缩小到工作台框内）————————————————————— */
const DRM = {
  run: null,        // { moduleKey, skill, title, threadId, streaming }
  rootEl: null,     // 工作台根元素 .st.drm
  navEl: null,      // 左侧导航元素
  embedded: false,  // 首页 #chat 是否已挂入工作台框内
  progress: {},     // moduleKey -> {status:'done'|'running', ts}
};

function drmProgKey() { return "drm:prog:studio"; }   // 全局键：每步新会话后流水线状态仍跨线程保留（待办面板依赖）
function drmLoadProgress() { try { DRM.progress = JSON.parse(localStorage.getItem(drmProgKey()) || "{}"); } catch (e) { DRM.progress = {}; } }
function drmSaveProgress() { try { localStorage.setItem(drmProgKey(), JSON.stringify(DRM.progress)); } catch (e) {} }
function drmMarkProgress(modKey, status) { DRM.progress[modKey] = { status: status, ts: Date.now() }; drmSaveProgress(); drmTodoRefresh(); }
function drmClearProgress(modKey) { if (DRM.progress[modKey]) { delete DRM.progress[modKey]; drmSaveProgress(); drmTodoRefresh(); } }

/* ————— 交付物筛选框：输入作品名 → 各环节交付物 ————— */
/* ————— 右栏双模式切换 —————
   form  = 平时：当前环节创作表单（默认）
   preview = 搜索命中后：HTML/MD 交付物整页渲染
   进入 preview 时把「短剧创作」整体收起：隐藏环节导航 + 顶栏提示 + 表单，
   页面变为纯交付物浏览器，左栏只剩搜索卡；返回创作时全部恢复。 */
function drmSetRightMode(mode) {
  const form = document.getElementById("drmModuleBox");
  const prev = document.getElementById("drmRenderBox");
  if (!form || !prev) return;
  const isPrev = mode === "preview";
  form.style.display = isPrev ? "none" : "";
  prev.style.display = isPrev ? "" : "none";
  /* 搜索交付物后隐藏「短剧创作」：环节导航收起，顶栏提示收起 */
  const todo = document.getElementById("drmTodoHost");
  if (todo) todo.style.display = isPrev ? "none" : "";
  const root = form.closest ? form.closest(".st.drm") : null;
  if (root) {
    const head = root.querySelector(".st-head");
    if (head) head.style.display = isPrev ? "none" : "";
  }
  const back = document.getElementById("drmRenderForm");
  if (back) back.onclick = () => { drmSetRightMode("form"); };
}

/* ————— 交付物筛选（短剧→HTML 交付物 / 小说→各目录 MD 文件 → 点击渲染到右侧大区） —————
   查询 tree 后：短剧保留 .html/.htm，小说保留 .md（chapters 等）；按「创作顺序」分组列条目；
   点条目 → drmRenderHtml 在右栏整页渲染（HTML 相对图片路径自动改写为绝对 API 路径）。
   命中后自动把右栏从「创作表单」切到「浏览」模式。 */
let _drmDelivList = [];        // 当前作品的交付物 [{path,name,size,label,idx}]
let _drmDelivKw = "";          // 当前查询的作品名
let _drmLastHtml = "";         // 最近一次改写后的 HTML 内容（「新窗口打开」用 Blob 复用）

/* 相对资源路径 → 绝对 /api/deliverables/view?path=<dir>/<rel>（修复 HTML 内图片不显示） */
function rewriteDelivHtml(html, dir) {
  if (!dir) return html;
  const toAbs = (rel) => {
    rel = rel.trim();
    if (!rel) return rel;
    if (/^(https?:)?\/\//i.test(rel) || rel.startsWith("#") || rel.startsWith("data:") || rel.startsWith("blob:") || rel.startsWith("/")) return rel;
    return "/api/deliverables/view?path=" + encodeURIComponent(dir + "/" + rel);
  };
  return html
    .replace(/(\ssrc\s*=\s*["'])([^"']+)(["'])/gi, (m, a, v, c) => a + toAbs(v) + c)
    .replace(/(\shref\s*=\s*["'])([^"']+)(["'])/gi, (m, a, v, c) => a + toAbs(v) + c)
    .replace(/(\ssrcset\s*=\s*["'])([^"']+)(["'])/gi, (m, a, v, c) => a + v.split(",").map((s) => { const [p, w] = s.trim().split(/\s+/); return toAbs(p) + (w ? " " + w : ""); }).join(", ") + c);
}

function drmDelivRefresh(el) {
  const host = document.getElementById("drmDelivFilter");
  if (!host) return;
  /* 短剧 HTML 交付物（report.html 为拆角色产出；其余按文件名前缀归环节） */
  const DRAMA_KW = [
    { re: /outline/i,                           label: "🧩 改编大纲", idx: 2 },
    { re: /(^|\/)report\.html$/i,               label: "👥 拆角色",   idx: 3 },
    { re: /cast|character/i,                    label: "👥 拆角色",   idx: 3 },
    { re: /art/i,                               label: "🎨 美术设定", idx: 4 },
    { re: /script/i,                            label: "📜 写剧本",   idx: 5 },
    { re: /storyboard/i,                        label: "🎬 出分镜",   idx: 6 },
    { re: /video|\.mp4|\.webm/i,                label: "🎥 出视频",   idx: 7 },
    { re: /image|\.png|\.jpg/i,                 label: "🖼 出图",     idx: 8 },
  ];
  /* 小说 MD 交付物：chapters 章节 / 大纲 / 报告说明（排在短剧各环节之前） */
  const NOVEL_KW = [
    { re: /大纲/i,                              label: "📖 小说大纲", idx: 0 },
    { re: /chapters\//i,                        label: "📑 小说章节", idx: 1 },
    { re: /完稿|报告|策划/i,                    label: "📝 报告",     idx: 9 },
    { re: /README/i,                            label: "📄 说明",     idx: 9 },
  ];
  host.innerHTML = `
    <div class="drm-deliv">
      <div class="drm-deliv-hd">
        <span class="drm-deliv-title">📦 交付物</span>
        <input type="text" id="drmDelivKw" class="d-input" placeholder="作品名，如：渊核" />
        <button class="d-btn sm" id="drmDelivGo">🔎 查看</button>
      </div>
      <div id="drmDelivOut" class="drm-deliv-out"></div>
    </div>`;
  const kw = $("#drmDelivKw", host);
  const go = $("#drmDelivGo", host);
  const out = $("#drmDelivOut", host);

  const paint = async () => {
    const q = (kw.value || "").trim();
    if (!q) { out.innerHTML = `<div class="d-empty">请输入作品名，如「渊核」</div>`; return; }
    out.innerHTML = `<div class="d-empty">检索「${q}」的交付物…</div>`;
    /* 两个分支都查：短剧保留 HTML 交付物；小说保留目录/章节 MD。同名作品（如 短剧/渊核 + 小说/渊核）合并展示 */
    let kept = [];
    for (const b of ["短剧", "小说"]) {
      try {
        const r = await apiGet(`/api/deliverables/tree?path=${encodeURIComponent(b + "/" + q)}`);
        if (r && Array.isArray(r.files) && r.files.length) {
          const batch = b === "小说"
            ? r.files.filter((f) => /\.md$/i.test(f.path))
            : r.files.filter((f) => /\.(html?|htm)$/i.test(f.path));
          kept = kept.concat(batch);
        }
      } catch (e) { /* 试试下一个分支 */ }
    }
    if (!kept.length) {
      out.innerHTML = `<div class="d-empty">「${q}」暂无交付物（已查 短剧/${q} 的 HTML 与 小说/${q} 的 MD）</div>`;
      _drmDelivList = []; _drmDelivKw = q; _drmLastHtml = "";
      drmSetRightMode("form");   // 无命中 → 右栏维持创作表单
      return;
    }
    const KW = (p) => (/\.md$/i.test(p) ? NOVEL_KW : DRAMA_KW);
    _drmDelivList = kept.map((f) => {
      const m = KW(f.path).find((x) => x.re.test(f.path));
      return { path: f.path, name: f.path.split("/").pop(), size: f.size, label: m ? m.label : "📎 其他", idx: m ? m.idx : 99 };
    });
    _drmDelivKw = q;

    const grp = (label, idx) => {
      const items = _drmDelivList.filter((x) => x.label === label);
      if (!items.length) return "";
      return `<div class="drm-deliv-grp">
        <div class="drm-deliv-grp-hd"><span>${label}</span><i>${items.length}</i></div>
        ${items.map((f) => `<button class="drm-deliv-item" data-path="${escapeHtml(f.path)}" title="${escapeHtml(f.path)}">
          <span class="dv-name">${escapeHtml(f.name)}</span>
          <span class="dv-size">${(f.size / 1024).toFixed(1)}KB</span>
        </button>`).join("")}
      </div>`;
    };
    /* 按创作顺序排：idx 升序，其余（📎 其他）垫底 */
    const labels = [...new Set(_drmDelivList.map((x) => x.label))];
    labels.sort((a, b) => {
      const ia = _drmDelivList.find((x) => x.label === a).idx;
      const ib = _drmDelivList.find((x) => x.label === b).idx;
      return ia - ib;
    });
    out.innerHTML = labels.map((l) => grp(l)).join("");
    $$(".drm-deliv-item", out).forEach((b) => b.addEventListener("click", () => {
      $$(".drm-deliv-item", out).forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      drmSetRightMode("preview");   // 点条目 → 切浏览模式并渲染
      drmRenderHtml(b.dataset.path);
    }));
    /* 默认渲染第一份交付物（右栏切预览） */
    const first = out.querySelector(".drm-deliv-item");
    if (first) { first.classList.add("active"); drmSetRightMode("preview"); drmRenderHtml(first.dataset.path); }
  };
  go.addEventListener("click", paint);
  kw.addEventListener("keydown", (e) => { if (e.key === "Enter") paint(); });
}

/* ————— 右栏整页渲染交付物（HTML 相对图片路径改写为绝对 API 路径；MD 直接转义预览） ————— */
async function drmRenderHtml(path) {
  const frame = document.getElementById("drmRenderFrame");
  const meta = document.getElementById("drmRenderMeta");
  const open = document.getElementById("drmRenderOpen");
  if (!frame) return;
  const abs = "/api/deliverables/view?path=" + encodeURIComponent(path);
  if (meta) meta.textContent = path;
  frame.innerHTML = `<div class="d-empty" style="padding:48px 20px;text-align:center">加载中…</div>`;
  try {
    const r = await fetch(abs, { credentials: "include" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const txt = await r.text();
    const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    /* MD：服务端 view 已返回 <pre> 转义文本，直接用；HTML：改写相对资源为绝对 API 路径 */
    const html = /\.md$/i.test(path) ? txt : rewriteDelivHtml(txt, dir);
    _drmLastHtml = html;
    frame.innerHTML = "";
    const iframe = document.createElement("iframe");
    iframe.className = "drm-render-frame-iframe";
    iframe.srcdoc = html;
    frame.appendChild(iframe);
    if (open) {
      open.style.display = "";
      open.href = "#";
      open.onclick = (e) => {
        e.preventDefault();
        const u = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
        window.open(u, "_blank");
        setTimeout(() => URL.revokeObjectURL(u), 60000);
      };
    }
  } catch (e) {
    frame.innerHTML = `<div class="d-empty">加载失败：${escapeHtml((e && e.message) || e)}</div>`;
  }
}

/* 环节导航联动：切到某环节时自动渲染该环节对应的 HTML（按文件名关键词） */
function drmRenderForModule(modKey) {
  if (!_drmDelivList.length) return;
  const kwMap = {
    outline: /outline/i, chars: /cast|character/i, art: /art/i,
    script: /script/i, storyboard: /storyboard/i, video: /video/i, s_world: /outline/i,
  };
  const re = kwMap[modKey];
  if (!re) return;
  const hit = _drmDelivList.find((x) => re.test(x.path));
  if (hit) {
    drmRenderHtml(hit.path);
    const out = document.getElementById("drmDelivOut");
    if (out) $$(".drm-deliv-item", out).forEach((x) => x.classList.toggle("active", x.dataset.path === hit.path));
  }
}

/* ————————————————————— 流水线待办面板 —————————————————————
   把两大创作流水线（短剧 5 步 / 小说 7 步）渲染成待办清单：
   每步一个条目，状态由 DRM.progress 驱动（todo/running/done/stopped/err），
   点击可跳到对应模块表单。数据存 localStorage（全局 key，跨线程保留）。 */
function drmTodoHtml() {
  const _K = {
    done:    ["done", "✓", "已完成"],
    running: ["run", "◔", "执行中"],
    stopped: ["stop", "⏹", "已停止"],
    err:     ["err", "✕", "已出错"],
    todo:    ["todo", "○", "待办"],
  };
  const step = (idx, m) => {
    const p = DRM.progress[m.key];
    const st = (p && _K[p.status]) ? _K[p.status] : _K.todo;
    const [cls] = st;
    const active = (m.key === _drmSel) ? " active" : "";
    return `<button class="drm-todo-step ${cls}${active}" data-mod="${m.key}" data-st="${p ? p.status : "todo"}" title="${m.name}" style="--acc:${m.color}"><span class="drm-todo-ico">${m.icon}</span><span class="drm-todo-name">${m.name}</span></button>`;
  };
  const chain = (mods) => mods.map((m, i) => `${step(i, m)}${i < mods.length - 1 ? '<b class="drm-todo-arrow">→</b>' : ""}`).join("");
  const grp = (label, mods) => `
    <div class="drm-todo-grp">
      <div class="drm-todo-grp-hd"><span>${label}</span></div>
      <div class="drm-todo-chain">${chain(mods)}</div>
    </div>`;
  return `
    <div class="drm-todo" id="drmTodo">
      ${grp("短剧创作", CREATIVE_GROUPS[0].modules)}
    </div>`;
}

function drmTodoRefresh() {
  const host = document.getElementById("drmTodoHost");
  if (!host) return;
  host.innerHTML = drmTodoHtml();
  // 点击条目 → 跳到对应模块表单（执行中不允许切换）；右栏回到「创作表单」模式
  $$(".drm-todo-step", host).forEach((b) => b.addEventListener("click", () => {
    if (DRM.run && DRM.run.streaming) { toast("当前有模块正在生成中，请先等本轮结束或点「⏹ 停止」", "warn"); return; }
    drmSetRightMode("form");   // 点环节导航 = 创作动作 → 右栏回表单
    renderDrmModule(DRM.rootEl, b.dataset.mod);
    $$(".drm-todo-step", host).forEach((x) => x.classList.toggle("active", x.dataset.mod === b.dataset.mod));
  }));
}
window.drmTodoRefresh = drmTodoRefresh;

function drmNavBadges(el) {
  if (!el) return;
  // 流程条即导航：同步各步骤按钮的 active 高亮
  $$(".drm-todo-step", el).forEach((b) => b.classList.toggle("active", b.dataset.mod === _drmSel));
}

function drmGroupOf(modKey) { return CREATIVE_GROUPS.find((g) => g.modules.some((x) => x.key === modKey)) || CREATIVE_GROUPS[0]; }
function drmDeliverDir(modKey) { return modKey === "s_world" ? "deliverables/小说/" : "deliverables/短剧/"; }

function drmStartRun(mod, title) {
  DRM.run = { moduleKey: mod.key, skill: mod.skill, title: title || "", threadId: (State && State.threadId) || "default", streaming: false };
  DRM._settled = null;   // 本轮结算标记：done / ask / err，三路兜底中先到先结算
  drmMarkProgress(mod.key, "running");
  drmNavBadges(DRM.navEl);
}

function drmSetLive(streaming, label) {
  const el = document.getElementById("drmLive");
  if (!el) return;
  el.className = "drm-chat-live " + (streaming ? "living" : "idle");
  el.textContent = label || (streaming ? "◔ 执行中" : "● 等待回复");
}

/* 把首页对话页(#chat)整体挂入工作台右栏框内——缩小尺寸展示，还是同一个页面，不新建 */
function drmEmbedChat(el, mod, title) {
  const box = $("#drmModuleBox", el);
  if (!box) return;
  const chat = document.getElementById("chat");
  if (!chat) return;
  if (DRM.embedded) {
    const cur = box.querySelector(".drm-chat-frame");
    if (cur && cur.dataset.mod === mod.key) return;   // 已是该视图（模块/对话区）：幂等
    drmUnembedChat();                                 // 残留异视图（#chat 被销毁/已切其它模块）：先归还首页再重嵌
  }
  const g = mod.metaLabel ? { label: mod.metaLabel } : drmGroupOf(mod.key);
  box.innerHTML = `
    <div class="drm-chat-frame" data-mod="${mod.key}" style="--acc:${mod.color}">
      <div class="drm-embed-hd">
        <span class="drm-chat-ic">${mod.icon}</span>
        <div class="drm-chat-titles">
          <div class="drm-chat-name">${mod.name} · 创作会话</div>
          <div class="drm-chat-meta">${g.label} · 技能 ${mod.skill} · 项目：${escapeHtml(title || "未命名")} · 线程 ${escapeHtml((State && State.threadId) || "default")}</div>
        </div>
        <span class="drm-chat-live idle" id="drmLive">● 等待回复</span>
        <button class="d-btn ghost drm-chat-btn" id="drmStopBtn">⏹ 停止</button>
        <button class="d-btn ghost drm-chat-btn" id="drmBackModBtn">📋 返回模块选择</button>
        <button class="d-btn ghost drm-chat-btn" id="drmGotoChatBtn">对话页</button>
      </div>
      <div class="drm-banner hidden" id="drmBanner"></div>
      <div class="drm-chat-embed" id="drmChatHost"></div>
    </div>`;
  document.getElementById("app").classList.add("chat-embedded");
  document.getElementById("drmChatHost").appendChild(chat);
  DRM.embedded = true;
  DRM.rootEl = el;
  $("#drmStopBtn", box).addEventListener("click", drmEmbedStop);
  $("#drmGotoChatBtn", box).addEventListener("click", () => { if (window.closeStudioView) closeStudioView(); });
  $("#drmBackModBtn", box).addEventListener("click", drmBackToModules);
  drmSetLive(!!(DRM.run && DRM.run.streaming));
}

function drmEmbedStop() {
  if (State && State.streaming && typeof window.stopChat === "function") {
    stopChat();
  } else {
    toast("当前没有正在执行的回合", "warn");
  }
  if (DRM.run) {
    DRM.run.streaming = false;
    drmMarkProgress(DRM.run.moduleKey, "stopped");   // 停止后不再显示“执行中”残影
    drmNavBadges(DRM.navEl);
  }
  drmSetLive(false, "● 等待回复");
  drmBanner("warn", "⏹ 已停止本轮生成");
}
window.drmEmbedStop = drmEmbedStop;

/* 把 #chat 从工作台框内移回首页原位置 */
function drmUnembedChat() {
  if (!DRM.embedded) return;
  const chat = document.getElementById("chat");
  const app = document.getElementById("app");
  const studio = document.getElementById("studio");
  if (chat && app) { if (studio) app.insertBefore(chat, studio); else app.appendChild(chat); }
  app.classList.remove("chat-embedded");
  DRM.embedded = false;
}
window.drmUnembedChat = drmUnembedChat;
/* 是否阻止离开创作 tab（正在流式执行时） */
window.drmWouldLeaveBlocked = () => !!(DRM.run && DRM.run.streaming);

function drmBanner(kind, html) {
  const b = document.getElementById("drmBanner");
  if (!b) return;
  if (!html) { b.className = "drm-banner hidden"; b.innerHTML = ""; return; }
  b.className = "drm-banner " + (kind || "");
  b.innerHTML = `<span class="b-grow">${html}</span>`;
}

function drmLastAssistantText() {
  const inner = document.querySelector("#messages .messages-inner");
  if (!inner) return "";
  const msgs = inner.querySelectorAll(".msg.assistant");
  if (!msgs.length) return "";
  const t = msgs[msgs.length - 1].querySelector(".msg-text");
  return t ? (t.innerText || "") : "";
}

/* 回合结算：done（事件） / error（事件） / readSSE 读完（兜底）三路中先到者生效，_settled 去重
   启动模块后即跳回主页对话页（不内嵌），因此不要求 DRM.embedded；仅校验是本模块线程。 */
function drmSettleTurn() {
  if (!DRM.run) return;
  if (State && State.threadId && DRM.run.threadId && DRM.run.threadId !== State.threadId) return;   // 已切到别的线程，不误标
  DRM.run.streaming = false;
  drmSetLive(false, "● 等待回复");
  const last = drmLastAssistantText();
  if (/[？?]\s*$/.test(last.trim())) {
    DRM._settled = "ask";
    drmBanner("warn", "🛎 智能体在等你回答：直接在下方输入框作答即可（全程不用离开创作页）");
    return;
  }
  DRM._settled = "done";
  drmMarkProgress(DRM.run.moduleKey, "done");
  drmNavBadges(DRM.navEl);
  const m = DrmModule(DRM.run.moduleKey);
  if (DRM.embedded) drmBanner("ok", `✅ 「${m ? m.name : ""}」本轮创作已完成 · 交付已写入 ${drmDeliverDir(DRM.run.moduleKey)}。可继续在此追问完善，或点「📋 返回模块选择」`);
}
window.drmOnTurnDone = function () {
  if (DRM._settled) return;
  drmSettleTurn();
};
window.drmOnTurnError = function (msg) {
  DRM._settled = "err";
  if (!DRM.run) return;
  DRM.run.streaming = false;
  drmMarkProgress(DRM.run.moduleKey, "err");
  drmNavBadges(DRM.navEl);
  drmSetLive(false, "● 等待回复");
  drmBanner("err", "⚠️ 本轮出错：" + escapeHtml(msg || "未知错误"));
};
/* SSE 流读完兜底：若 done/error 事件因故未达，同样结算（不改成”执行中“残影） */
window.drmOnTurnStreamEnd = function () {
  if (!DRM.run) return;
  if (!DRM._settled) drmSettleTurn();
};

/* 进入沉浸式会话：嵌入首页对话页。
   refresh=true 表示「重开工作台恢复」场景：消息以 DB 为准刷新到框内对话页；
   启动场景（refresh=false）复用当前线程已在屏幕上的对话内容，避免与首条启动消息竞态。 */
function drmEnterChatView(el, mod, title, refresh) {
  drmEmbedChat(el, mod, title);
  if (refresh) {
    if (DRM.run) {
      DRM.run.streaming = false;
      drmSetLive(false, "● 等待回复");
    }
    if (typeof loadHistory === "function") loadHistory((State && State.threadId) || "default").catch(() => {});
  }
}

/* 不启动任何模块，直接把首页对话页嵌入工作台（自由对话 / 查看历史）——已移除「💬 对话区」入口 */

/* 返回模块选择：把对话页移回首页原位（关闭工作台后在首页可继续看对话），工作表回到表单 */
function drmBackToModules() {
  if (DRM.run && DRM.run.streaming) { toast("当前模块仍在执行：请先「⏹ 停止」或等本轮结束", "warn"); return; }
  const saved = (DRM.run && DRM.run.moduleKey) || _drmSel;
  drmUnembedChat();
  DRM.run = null;
  if (DRM.rootEl) {
    renderDrmModule(DRM.rootEl, saved);
    drmNavBadges(DRM.navEl);
    $$(".drm-todo-step", DRM.rootEl).forEach((x) => x.classList.toggle("active", x.dataset.mod === saved));
  }
}/* ————————————————————— 渲染工作台（图片创作式双栏布局 · 右栏双模式） —————————————————————
   左栏（窄）：交付物搜索（输入作品名→查 HTML） + 环节导航
   右栏（宽）：平时 = 当前环节创作表单；搜索命中后 = HTML 交付物整页渲染（可返回创作）
   ============================================================================ */
async function renderDramaStudioTab(el) {
  drmLoadProgress();
  el.innerHTML = `
    <div class="st drm">
      <div class="st-head">
        <div class="st-head-title">🎭 短剧创作</div>
        <div class="st-head-hint">左栏：选环节填表启动（点「🚀 启动」自动跳主页对话页执行）；输入作品名浏览交付物：短剧=各环节 HTML，小说=目录/章节 MD。</div>
      </div>
      <div class="st-cols">
        <section class="st-left">
          <div id="drmDelivFilter"></div>
          <div id="drmTodoHost"></div>
        </section>
        <section class="st-right">
          <div id="drmModuleBox"></div>
          <div id="drmRenderBox" class="drm-render" style="display:none">
            <div class="drm-render-bar">
              <span class="drm-render-title">📄 交付物预览</span>
              <span class="drm-render-meta" id="drmRenderMeta">未选择</span>
              <div style="flex:1"></div>
              <button class="d-btn sm" id="drmRenderForm">✎ 返回创作</button>
              <a class="d-btn sm" id="drmRenderOpen" target="_blank" rel="noopener" style="display:none">↗ 新窗口打开</a>
            </div>
            <div id="drmRenderFrame" class="drm-render-frame">
              <div class="d-empty" style="padding:64px 20px;text-align:center">
                左侧输入作品名（如 <b>渊核</b>）→ 这里整页渲染交付物<br>
                短剧：role 各环节 HTML（<code>storyboard-report.html / art-report.html …</code>）<br>
                小说：目录 MD（<code>题目大纲 / chapters/章节</code>）
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>`;

  DRM.rootEl = el;
  DRM.navEl = el;
  drmNavBadges(el);
  drmDelivRefresh(el);   // 交付物筛选（只列 HTML，点击渲染到右侧）
  drmTodoRefresh();   // 环节导航（状态追踪 + 模块切换 + 联动渲染）

  /* 若存在进行中的创作会话，恢复「内嵌首页对话页」视图（消息以 DB 为准刷新到框内） */
  if (DRM.run) {
    const m = DrmModule(DRM.run.moduleKey);
    if (m) {
      DRM.run.streaming = false;
      drmEnterChatView(el, m, DRM.run.title, true);
      $$(".drm-todo-step", el).forEach((x) => x.classList.toggle("active", x.dataset.mod === m.key));
      return;
    }
  }
  renderDrmModule(el, _drmSel);
  drmNavBadges(el);   // 同步流程条高亮（_drmSel 已更新）
}