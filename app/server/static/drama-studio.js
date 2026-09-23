/* ============================================================================
   短剧创作工作台（系统级 · 左侧模块 Tab 列表）
   ----------------------------------------------------------------------------
   不绑定任何账号：所有 12 个创作技能已作为系统内置技能安装（app/skills/），
   任何账号都能看到并使用：
     · 短剧创作 5 个：novel-outline / novel-characters / novel-art /
                      novel-script / novel-storyboard（前导技能流水线）
     · 小说创作 7 个：sumeru-topic / sumeru-worldbuilder / sumeru-outline /
                      sumeru-write / sumeru-review / sumeru-polish /
                      sumeru-finalize（xindoo/sumeru 技能族）
   布局：左侧 = 模块 Tab 列表（分组），右侧 = 当前模块的表单卡。
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
要求：严格按该技能的 SKILL.md 逐步执行。先用 read_skill 读取技能说明，再按它的 Step 顺序走：定位输入 → seed/chunk → 逐批生成 → validate → render；所有脚本（node {baseDir}/scripts/*.mjs，零依赖）与产物都放在当前用户自己的工作区 deliverables/短剧/${title}/ 下。validate 必须全绿才 render，报了质量门就就地修正后重跑。当前环境没有 codex 内置 $imagegen，出图类步骤按技能允许的降级写法：只生成/交付提示词与关键帧描述，不做真实出图。全程用中文汇报，完成后用 2–3 句话列出各产物文件路径。`;
}

/* 公共说明尾巴（sumeru 小说写作技能指令的收尾约束） */
function SUMERU_TAIL(title) {
  return `
要求：严格按该技能的 SKILL.md 执行。先用 read_skill 读取技能说明，按其 Step 顺序走；先在当前用户自己的工作区建目录 deliverables/小说/${title}/ 作为本次操作的根目录（chapters/、.sumeru/、publish/ 等一律放在其内），全部使用工作区相对路径，不要触碰工作区之外的任何路径；涉及章节级批量操作的步骤，按 SKILL.md 规则使用子 Agent 并行处理（每个子 Agent 最多负责 3 个章节），完成后必须校验章节数与细纲一致。全程用中文汇报，完成后用 2–3 句话列出各产物文件路径。`;
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
      { key: "style", label: "出图画风", type: "select", default: "realistic", options: [["realistic", "半写实厚涂（默认）"], ["ghibli", "吉卜力手绘赛璐璐"]] },
      { key: "lang", label: "报告语言", type: "text", default: "中文", placeholder: "中文 / English / 日本語 …" },
      { key: "outlinePath", label: "可选：outline.json 路径", type: "path", placeholder: "有上一模块产物就填，如 deliverables/短剧/xxx/outline.json，会走 seed 沿用角色清单" },
      { key: "srcText", label: "原著正文（粘贴）", type: "textarea", rows: 5, placeholder: "无 outline 时需给原文。粘贴正文或直接给文件路径。" },
      { key: "srcPath", label: "或：正文文件路径", type: "path", placeholder: "工作区相对路径" },
    ],
    build(v, title) {
      if (!v.srcPath && !v.srcText && !v.outlinePath) return "";
      return `请使用 novel-characters 技能，为这部短剧拆角色设定。
项目/剧名：${title}
出图画风：${v.style || "realistic"}　报告语言：${v.lang || "中文"}
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
    desc: "把剧本切成分镜三层结构：段（≤15s）→ 分镜（认领节拍）→ 分镜图（关键帧），每段自带 MiniMax H3 视频提示词，产出 storyboard.json（17 道质量门）。script.json 是硬前提。",
    fields: [
      { key: "scriptPath", label: "script.json 路径", type: "path", placeholder: "必填（工作区相对路径），分镜离开剧本没有意义" },
      { key: "scriptText", label: "或：粘贴 script.json 内容", type: "textarea", rows: 5, placeholder: "没有路径文件就整份贴进来，先落到临时文件" },
      { key: "batch", label: "一批切几集", type: "number", default: 3, min: 1, hint: "跟剧本批次走，默认 ≤3 集" },
      { key: "upstreams", label: "可选：outline/cast/art 路径", type: "text", placeholder: "逗号分隔，如 deliverables/短剧/xxx/outline.json, .../cast.json, .../art.json" },
    ],
    build(v, title) {
      if (!v.scriptPath && !v.scriptText) return "";
      const ups = (v.upstreams || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean);
      const upsNote = ups.length
        ? `可上游（有则用，供提示词禁人名/场景名回显）：${ups.join("；")}`
        : "无额外上游（可直接用脚本自带的场景/角色名）。";
      return `请使用 novel-storyboard 技能，为这部短剧出分镜（段/分镜/分镜图 + MiniMax H3 视频提示词）。
项目/剧名：${title}
一批切 ${v.batch || 3} 集
${v.scriptPath ? `script.json（工作区相对路径，先 read_file）：${v.scriptPath}` : `script.json 内容（先落到临时 .txt 再进技能流程）：\n\n${v.scriptText}`}
${upsNote}${DRAMA_TAIL(title)}`;
    },
  },
];

/* ————————————————————— 分组二：小说创作（xindoo/sumeru 技能族） ————————————————————— */
const SUMERU_MODULES = [
  {
    key: "s_world",
    skill: "sumeru-worldbuilder",
    icon: "🌍",
    name: "全流程构建",
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
  {
    key: "s_topic",
    skill: "sumeru-topic",
    icon: "💡",
    name: "选题策划",
    color: "#5ab0ff",
    desc: "网文选题策划：市场热点分析 → 生成 3–5 套选题方案（金手指设计 / 核心卖点 / 爽点模式 / 情绪价值曲线）→ 可行性评估与推荐。输出选题策划报告.md，options.json 供下游复用。",
    fields: [
      { key: "genre", label: "题材倾向", type: "text", placeholder: "如：玄幻 / 都市脑洞 / 悬疑无限流" },
      { key: "keywords", label: "关键词（逗号分隔）", type: "text", placeholder: "如：废柴逆袭, 系统流, 穿越" },
      { key: "platform", label: "目标平台", type: "select", default: "不限", options: _PLATFORM_OPTS },
      { key: "audience", label: "目标读者", type: "select", default: "男频", options: [["男频", "男频"], ["女频", "女频"], ["不限", "不限"]] },
      { key: "count", label: "选题数量", type: "number", default: 3, min: 1 },
      { key: "extra", label: "其他要求（可选）", type: "textarea", rows: 3, placeholder: "如：偏好系统流 / 不要末世题材 / 想写轻松搞笑风…" },
    ],
    build(v, title) {
      const kw = v.keywords ? `关键词：${v.keywords}` : "";
      return `请使用 sumeru-topic 技能，做一次网文选题策划。
书名/项目名：${title}
题材倾向：${v.genre || "（未提供，由 AI 按市场热点拟定）"}　${kw ? `\n${kw}` : ""}
目标平台：${v.platform || "不限"}　目标读者：${v.audience || "男频"}
生成 ${v.count || 3} 套选题方案（含金手指设计、核心卖点、受众定位、爽点模式、开篇建议），输出选题策划报告.md 并做可行性评估与最终推荐。
${v.extra ? `其他要求：\n${v.extra}\n` : ""}${SUMERU_TAIL(title)}`;
    },
  },
  {
    key: "s_outline",
    skill: "sumeru-outline",
    icon: "📐",
    name: "大纲细纲",
    color: "#45d6b5",
    desc: "小说大纲设计：世界观设定 / 人物设定卡 / 分卷大纲 / 爽点排布 / 强制合规检查，并为全本生成完整章节细纲 chapter-outlines.json（细纲驱动下游写作）。",
    fields: [
      { key: "topicPath", label: "可选：已有选题 options.json 路径", type: "path", placeholder: "有 sumeru-topic 产物就填，复用选题数据（工作区相对路径）" },
      { key: "genre", label: "题材", type: "text", placeholder: "如：玄幻 / 都市 / 悬疑" },
      { key: "chapters", label: "目标章节数", type: "number", default: 60, min: 1, hint: "含分卷大纲与完整章节细纲" },
      { key: "settings", label: "已有设定 / 要求（可选）", type: "textarea", rows: 4, placeholder: "如：力量体系怎么设计、主角金手指、想避开的套路、人设倾向…" },
    ],
    build(v, title) {
      return `请使用 sumeru-outline 技能，为《${title}》（题材：${v.genre || "待定"}）设计完整大纲：世界观设定、人物设定卡、分卷大纲、爽点排布，并生成全本 ${v.chapters || 60} 章的完整章节细纲（chapter-outlines.json）。
${v.topicPath ? `复用已有选题数据（options.json，工作区相对路径，先 read_file）：${v.topicPath}\n` : ""}
${v.settings ? `已有设定/要求：\n${v.settings}\n` : ""}${SUMERU_TAIL(title)}`;
    },
  },
  {
    key: "s_write",
    skill: "sumeru-write",
    icon: "✍️",
    name: "章节撰写",
    color: "#ffb04f",
    desc: "网文章节创作：细纲驱动批量生成 / 续写 / 重写 / 扩写多模式，自动适配网文节奏，批量时子 Agent 并行（每 Agent ≤3 章），保持人物与剧情一致性。",
    fields: [
      { key: "mode", label: "模式", type: "select", default: "细纲驱动批量", options: [["细纲驱动批量", "细纲驱动批量（若工作目录内有 chapter-outlines.json 自动启用）"], ["续写", "续写后续章节"], ["重写", "重写指定章节"], ["扩写", "扩写章节内容"]] },
      { key: "chapters", label: "本批章节数", type: "number", default: 1, min: 1, hint: "细纲驱动时按 3 章/Agent 自动并行" },
      { key: "startFrom", label: "起始章节", type: "number", default: 1, min: 1 },
      { key: "chapterLen", label: "每章字数", type: "number", default: 4500, min: 1000, hint: "默认 4000–5000 字/章" },
      { key: "notes", label: "写作要求（可选）", type: "textarea", rows: 3, placeholder: "如：紧张感拉满、每章结尾留钩子、对话要自然…" },
    ],
    build(v, title) {
      return `请使用 sumeru-write 技能，为《${title}》撰写章节（模式：${v.mode || "细纲驱动批量"}）。
从第 ${v.startFrom || 1} 章起，写 ${v.chapters || 1} 章，每章约 ${v.chapterLen || 4500} 字；细纲驱动时自动读取操作根目录下的 chapter-outlines.json（有则自动启用、章节数与细纲对齐）。
${v.notes ? `写作要求：\n${v.notes}\n` : ""}${SUMERU_TAIL(title)}`;
    },
  },
  {
    key: "s_review",
    skill: "sumeru-review",
    icon: "🔍",
    name: "逻辑审查",
    color: "#ff7c7c",
    desc: "小说逻辑 / 一致性审查：全量信息底稿检查（时间线冲突、设定崩塌、OOC、重复情节、信息泄露、伏笔悬线）+ 章节级审查，输出剧情审查报告.md；严重问题可自动修订大纲并重写章节（自动备份原稿）。",
    fields: [
      { key: "chaptersPath", label: "章节目录（可选）", type: "path", placeholder: "工作区相对路径，默认 deliverables/小说/<书名>/chapters/" },
      { key: "scope", label: "审查范围", type: "select", default: "全部章节", options: [["全部章节", "全部章节"], ["指定区间", "仅审查指定章节区间"]] },
      { key: "range", label: "区间", type: "text", placeholder: "如 1-20（审查范围=指定区间时生效）" },
      { key: "autoFix", label: "严重问题处理", type: "radio", default: "auto", options: [["auto", "自动修订大纲并重写问题章节（推荐）"], ["report", "仅生成报告，人工处理"]] },
      { key: "focus", label: "侧重（可选）", type: "text", placeholder: "如：时间线 / OOC / 伏笔回收 / 设定一致性" },
    ],
    build(v, title) {
      const range = v.range ? `（区间：${v.range}）` : "";
      return `请使用 sumeru-review 技能，对《${title}》做逻辑与一致性审查。
审查范围：${v.scope || "全部章节"}${range}
${v.chaptersPath ? `章节目录（工作区相对路径，先 read_file 确认）：${v.chaptersPath}\n` : ""}
${v.focus ? `侧重：${v.focus}\n` : ""}严重问题处理：${v.autoFix === "auto" ? "自动修订大纲并重写严重问题章节（修改前备份到 .sumeru/write/original/），轻量问题直接在 chapters/ 修正" : "仅生成报告，不在 chapters/ 直接修改"}
审查后输出剧情审查报告.md（含全量底线问题清单，见 .sumeru/review/）。${SUMERU_TAIL(title)}`;
    },
  },
  {
    key: "s_polish",
    skill: "sumeru-polish",
    icon: "✨",
    name: "内容润色",
    color: "#ff8ac2",
    desc: "小说情感润色：轻度 / 中度 / 重度三级润色 + 多文风选择，专注文本内容层优化（节奏提速、爽点强化、对话自然化、细节丰满），直接修改 chapters/（修改前自动备份）。",
    fields: [
      { key: "level", label: "润色等级", type: "select", default: "中度润色", options: [["轻度润色", "轻度润色（优化句式措辞，保留约 80% 原表达）"], ["中度润色", "中度润色（重构节奏结构，优化场景视角）"], ["重度润色", "重度润色（逐句打磨，追求极致阅读体验）"]] },
      { key: "style", label: "文风", type: "select", default: "保持原风格", options: [["保持原风格", "保持原风格"], ["简洁爽快", "简洁爽快（短句、快节奏、情绪直接）"], ["精品文学", "精品文学（句式精美、注重氛围）"], ["垂文婉约", "垂文婉约"], ["现实向", "现实向"], ["资讯流", "资讯流"], ["科幻未来", "科幻未来"]] },
      { key: "chaptersPath", label: "章节目录（可选）", type: "path", placeholder: "工作区相对路径，默认 deliverables/小说/<书名>/chapters/" },
      { key: "focus", label: "侧重", type: "select", default: "全面优化", options: [["全面优化", "全面优化"], ["节奏提速", "节奏提速（删废笔、压缩铺垫、提速 30%-50%）"], ["爽点强化", "爽点强化（铺垫、爆发、细节放大、收尾四段式）"], ["对话自然化", "对话自然化"], ["细节丰满", "细节丰满"]] },
    ],
    build(v, title) {
      return `请使用 sumeru-polish 技能，对《${title}》进行内容润色。
润色等级：${v.level || "中度润色"}　文风：${v.style || "保持原风格"}　侧重：${v.focus || "全面优化"}
${v.chaptersPath ? `章节目录（工作区相对路径，先 read_file 确认）：${v.chaptersPath}\n` : ""}
润色结果直接修改 chapters/，修改前自动备份原稿到 .sumeru/write/original/。${SUMERU_TAIL(title)}`;
    },
  },
  {
    key: "s_finalize",
    skill: "sumeru-finalize",
    icon: "✅",
    name: "完稿校验",
    color: "#5fd68a",
    desc: "小说终审与完稿打包：全量校对（错别字 / 标点 / 语法 / 敏感违规排查）+ 格式规范统一（章节标题、排版），按目标平台输出规则整理出版 publish/ 完整作品包。",
    fields: [
      { key: "chaptersPath", label: "章节目录（可选）", type: "path", placeholder: "工作区相对路径，默认 deliverables/小说/<书名>/chapters/" },
      { key: "platform", label: "目标平台", type: "select", default: "多平台通用", options: [["多平台通用", "多平台通用"], ["起点", "起点"], ["番茄", "番茄"], ["晋江", "晋江"], ["知乎", "知乎盐选"]] },
      { key: "autoFormat", label: "排版", type: "radio", default: "format", options: [["format", "按平台格式统一编排（推荐）"], ["plain", "仅校对，保持现有排版"]] },
    ],
    build(v, title) {
      return `请使用 sumeru-finalize 技能，对《${title}》全书做终审与收尾。
${v.chaptersPath ? `章节目录（工作区相对路径，先 read_file 确认）：${v.chaptersPath}\n` : ""}
目标平台排版：${v.platform || "多平台通用"}　排版方式：${v.autoFormat === "format" ? "按平台格式统一编排" : "仅校对、保持现有排版"}
执行全量终审（错别字、标点、语法、内容合规与敏感词排查），完成后输出 publish/ 完整作品包与全书统计。${SUMERU_TAIL(title)}`;
    },
  },
];

/* ————————————————————— 分组定义（左侧 Tab 列表） ————————————————————— */
const CREATIVE_GROUPS = [
  { key: "drama", label: "短剧创作", note: "系统级 · novel-* 前导技能", modules: DRAMA_MODULES },
  { key: "novel", label: "小说创作", note: "系统级 · xindoo/sumeru 技能", modules: SUMERU_MODULES },
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
function drmRenderCmdChip(r) {
  const chip = $("#drmCmdChip");
  if (!chip) return;
  const on = !!(r && r.enabled);
  chip.className = "drm-cmd-chip" + (on ? " on" : "");
  chip.textContent = on
    ? "命令执行（受限）：本会话已开启"
    : "命令执行（受限）：本会话未开启";
}
async function drmSetCmd(enabled) {
  try {
    const r = await apiPost("/api/workbench/drama", { enabled, thread_id: State.threadId });
    drmRenderCmdChip(r);
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
        <details class="drm-doc"><summary>📖 技能说明</summary><div class="drm-doc-body" data-skill="${m.skill}">加载中…</div></details>
      </div>
      <div class="drm-desc">${m.desc}</div>
      <div class="drm-fields">
        ${m.fields.map((f) => (f.type === "radio" ? drmRadioHtml(m.key, f) : drmFieldHtml(m.key, f))).join("")}
      </div>
      <div class="drm-actions">
        <button class="d-btn primary drm-run" data-run="${m.key}">🚀 启动 ${m.name}</button>
        <span class="d-empty">启动后：首页对话页会缩小嵌入到工作台框内（同一个对话页，不新建）→ 智能体中途提问直接在框内输入框作答，产物写入 deliverables/，全程不用跳转</span>
      </div>
    </div>`;

  /* 技能说明：懒加载对应 SKILL.md */
  const d = $(".drm-doc", box);
  if (d) d.addEventListener("toggle", (ev) => {
    if (!ev.target.open || ev.target.dataset.loaded) return;
    ev.target.dataset.loaded = "1";
    const db = $(".drm-doc-body", ev.target);
    const skill = db.dataset.skill;
    apiGet(`/api/skills/${encodeURIComponent(skill)}`).then((r) => {
      const body = (r && (r.body || r.raw)) || "";
      db.innerHTML = `<pre>${escapeHtml(body.slice(0, 2600))}${body.length > 2600 ? "\n…（已截断，详情请直接与智能体对话查看）" : ""}</pre>`;
    }).catch((e) => { db.innerHTML = `<div class="d-empty">加载失败：${escapeHtml(e.message || e)}</div>`; });
  });

  /* 启动模块 → 激活本会话受限命令作用域 → 把首页对话页缩小内嵌到工作台 → 直接用首页对话页发起这一轮 */
  const runBtn = $(".drm-run", box);
  if (runBtn) runBtn.addEventListener("click", async () => {
    const mod = DrmModule(runBtn.dataset.run);
    if (!mod) return;
    if (DRM.run && DRM.run.streaming) { toast("「" + mod.name + "」：当前有模块正在生成中，请先等本轮结束或点「⏹ 停止」", "warn"); return; }
    const group = CREATIVE_GROUPS.find((g) => g.modules.some((x) => x.key === mod.key)) || CREATIVE_GROUPS[0];
    const titleBox = $("#drmTitle", el);
    const dft = group.key === "novel" ? SUMERU_TITLE_DFT : DRAMA_TITLE_DFT;
    const title = (titleBox.value || dft).trim();
    const v = drmCollect(box, mod.key, mod.fields);
    const msg = mod.build(v, title);
    if (!msg) { toast("请先填写该模块的必填输入（正文粘贴 或 产物路径）", "error"); return; }
    if (typeof sendMessage !== "function") { toast("对话发送器未就绪，请刷新页面", "error"); return; }
    await drmSetCmd(true);
    drmStartRun(mod, title);
    drmEnterChatView(el, mod, title, false);   // 嵌入首页对话页（不刷新历史，避免与首条启动消息竞态）
    if (DRM.run) DRM.run.streaming = true;
    drmSetLive(true, "◔ 执行中");
    try {
      const _sent = await sendMessage(msg);           // 复用首页对话页自己的流式渲染（token/工具/审批/待办全在框内）
      if (_sent === false && DRM.run) {
        // 上一轮还挂着（State.streaming 未复位）：本轮实际没发出去——回滚待命态，避免 ◔ 永久残影
        DRM.run.streaming = false;
        drmClearProgress(DRM.run.moduleKey);
        drmNavBadges(DRM.navEl);
        drmSetLive(false, "● 等待回复");
        drmBanner("warn", "🍃 上一轮对话仍处于执行中，本轮未启动：请先「⏹ 停止」或等它结束");
      }
    } catch (e) {
      console.warn("drm launch:", e);
    } finally {
      if (DRM.run) DRM.run.streaming = false;
    }
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

function drmProgKey() { return "drm:prog:" + ((State && State.threadId) || "default"); }
function drmLoadProgress() { try { DRM.progress = JSON.parse(localStorage.getItem(drmProgKey()) || "{}"); } catch (e) { DRM.progress = {}; } }
function drmSaveProgress() { try { localStorage.setItem(drmProgKey(), JSON.stringify(DRM.progress)); } catch (e) {} }
function drmMarkProgress(modKey, status) { DRM.progress[modKey] = { status: status, ts: Date.now() }; drmSaveProgress(); }
function drmClearProgress(modKey) { if (DRM.progress[modKey]) { delete DRM.progress[modKey]; drmSaveProgress(); } }

function drmNavBadges(el) {
  if (!el) return;
  const _K = { done: ["done", "✓", "已完成"], running: ["run", "◔", "执行中"], stopped: ["stop", "⏹", "已停止"], err: ["err", "✕", "已出错"] };
  $$(".drm-nav-item", el).forEach((b) => {
    const mk = b.dataset.mod;
    const bg = b.querySelector(".drm-nav-badge");
    if (bg) bg.remove();
    const p = DRM.progress[mk];
    if (p && _K[p.status]) {
      const [cls, ico, tip] = _K[p.status];
      const s = document.createElement("b");
      s.className = "drm-nav-badge " + cls;
      s.textContent = ico;
      s.title = tip;
      b.appendChild(s);
    }
  });
}

function drmNavStatusOf(modKey) {
  const _K = { done: `<b class="drm-nav-badge done">✓</b>`, running: `<b class="drm-nav-badge run">◔</b>`, stopped: `<b class="drm-nav-badge stop">⏹</b>`, err: `<b class="drm-nav-badge err">✕</b>` };
  const p = DRM.progress[modKey];
  return (p && _K[p.status]) || "";
}

function drmGroupOf(modKey) { return CREATIVE_GROUPS.find((g) => g.modules.some((x) => x.key === modKey)) || CREATIVE_GROUPS[0]; }
function drmDeliverDir(modKey) { const g = drmGroupOf(modKey); return g.key === "novel" ? "deliverables/小说/" : "deliverables/短剧/"; }

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

/* 回合结算：done（事件） / error（事件） / readSSE 读完（兜底）三路中先到者生效，_settled 去重 */
function drmSettleTurn() {
  if (!DRM.run || !DRM.embedded) return;
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
  drmBanner("ok", `✅ 「${m ? m.name : ""}」本轮创作已完成 · 交付已写入 ${drmDeliverDir(DRM.run.moduleKey)}。可继续在此追问完善，或点「📋 返回模块选择」`);
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

/* 不启动任何模块，直接把首页对话页嵌入工作台（自由对话 / 查看历史） */
function drmOpenChat() {
  if (DRM.embedded) { toast("已在对话区", "info"); return; }
  const titleEl = document.getElementById("drmTitle");
  const title = ((titleEl && titleEl.value.trim()) || SUMERU_TITLE_DFT);
  DRM.run = null;
  drmEnterChatView(DRM.rootEl, {
    key: "chat", icon: "💬", name: "工作台对话", skill: "自由对话",
    color: "#38bdf8", metaLabel: "创作工作台",
  }, title, true);
  if (DRM.navEl) $$(".drm-nav-item", DRM.navEl).forEach((x) => x.classList.remove("active"));
  drmBanner("ok", "💬 已进入工作台对话区（未启动模块）。直接在框内自由提问；或点左侧模块 →「🚀 启动」开始一轮创作。");
}

/* 返回模块选择：把对话页移回首页原位（关闭工作台后在首页可继续看对话），工作表回到表单 */
function drmBackToModules() {
  if (DRM.run && DRM.run.streaming) { toast("当前模块仍在执行：请先「⏹ 停止」或等本轮结束", "warn"); return; }
  const saved = (DRM.run && DRM.run.moduleKey) || _drmSel;
  drmUnembedChat();
  DRM.run = null;
  if (DRM.rootEl) {
    renderDrmModule(DRM.rootEl, saved);
    drmNavBadges(DRM.navEl);
    $$(".drm-nav-item", DRM.rootEl).forEach((x) => x.classList.toggle("active", x.dataset.mod === saved));
  }
}/* ————————————————————— 渲染工作台 ————————————————————— */
async function renderDramaStudioTab(el) {
  drmLoadProgress();
  el.innerHTML = `
    <div class="st drm">
      <div class="st-head">
        <div class="st-head-title">🎭 创作工作台</div>
        <div class="st-head-hint">系统级功能 · 左侧选择模块 → 右侧填表 → 启动后，首页对话页会缩小嵌入到右侧框内执行（同一个对话页，不新建）：提问、审批、进度、交付就地展示，不用跳转；产物落在你自己工作区的 deliverables/ 下（短剧 → deliverables/短剧/，小说 → deliverables/小说/）</div>
      </div>

<div class="drm-toolbar">
          <div class="drm-title-row">
            <span class="drm-title-lbl">项目 / 剧名（或书名）</span>
            <input class="d-input drm-title-input" id="drmTitle" placeholder="${DRAMA_TITLE_DFT}" value="" spellcheck="false">
            <button class="d-btn ghost drm-chat-btn drm-toolbar-ops" id="drmOpenChatBtn" title="不启动模块，直接把首页对话页嵌入工作台，自由对话/查看历史">💬 对话区</button>
          </div>
        <div class="drm-pipeline">
          <span class="drm-pipe-grp">短剧：${DRAMA_MODULES.map((m) => `<span class="drm-pipe"><i>${m.icon}</i>${m.name}</span>`).join('<b>→</b>')}</span>
          <span class="drm-pipe-grp">小说：${(() => { const _lead = SUMERU_MODULES[0]; const _rest = SUMERU_MODULES.slice(1); const _chip = (m) => `<span class="drm-pipe"><i>${m.icon}</i>${m.name}</span>`; return _chip(_lead) + "<b>=</b>" + _rest.map(_chip).join("<b>→</b>"); })()}</span>
        </div>
        <div class="drm-cmd-chip" id="drmCmdChip">命令执行（受限）…</div>
      </div>

      <div class="drm-note">上一个模块的产物路径，回填到下一个模块的“路径”输入框即可串联；第二批继续时，只需再点一次「启动」并说明“继续”，会沿用上一批的会话与目录。</div>

      <div class="drm-body">
        <nav class="drm-nav" id="drmNav">
          ${CREATIVE_GROUPS.map((g) => `
            <div class="drm-nav-grp">
              <div class="drm-nav-grp-hd"><span>${g.label}</span><i>${g.note.replace(" · ", " / ")}</i></div>
              ${g.modules.map((m) => `<button class="drm-nav-item" data-mod="${m.key}" style="--acc:${m.color}"><span class="drm-nav-ic">${m.icon}</span><span class="drm-nav-name">${m.name}</span>${drmNavStatusOf(m.key)}</button>`).join("")}
            </div>`).join("")}
        </nav>
        <section class="drm-module" id="drmModuleBox"></section>
      </div>
    </div>`;

  DRM.rootEl = el;
  DRM.navEl = el;
  drmNavBadges(el);

  /* 左侧 Tab：切换模块 → 重渲染右侧表单（执行中阻挡，避免脱离会话视图） */
  $$(".drm-nav-item", el).forEach((b) => b.addEventListener("click", () => {
    if (DRM.run && DRM.run.streaming) {
      toast("「" + (b.dataset.mod || "") + "」：当前有模块正在生成中，请先等本轮结束或点「⏹ 停止」", "warn");
      return;
    }
    renderDrmModule(el, b.dataset.mod);
    $$(".drm-nav-item", el).forEach((x) => x.classList.toggle("active", x.dataset.mod === b.dataset.mod));
  }));

  /* 命令作用域状态：进工作台先查一次 */
  apiGet("/api/workbench/drama").then(drmRenderCmdChip).catch(() => {});

  /* 顶部「💬 对话区」：不启动模块，直接嵌入首页对话页自由对话 */
  $("#drmOpenChatBtn", el).addEventListener("click", () => {
    if (DRM.run && DRM.run.streaming) { toast("当前有模块正在生成中，请先等本轮结束或点「⏹ 停止」", "warn"); return; }
    drmOpenChat();
  });

  /* 若存在进行中的创作会话，恢复「内嵌首页对话页」视图（消息以 DB 为准刷新到框内） */
  if (DRM.run) {
    const m = DrmModule(DRM.run.moduleKey);
    if (m) {
      DRM.run.streaming = false;
      drmEnterChatView(el, m, DRM.run.title, true);
      $$(".drm-nav-item", el).forEach((x) => x.classList.toggle("active", x.dataset.mod === m.key));
      return;
    }
  }
  renderDrmModule(el, _drmSel);
  const first = $(".drm-nav-item", el);
  if (first) first.classList.add("active");
}