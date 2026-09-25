#!/usr/bin/env node
// sb_fix_auto.mjs — storyboard 机械补全器（对标 fix_script_auto 之于剧本）
// 作用：把模型提供的创作内容（切分 beats/seconds/size/camera/frame/画面描述）保留，
//       机械重建所有可由结构推导的门：段号、h3Prompt 对齐行/切点时刻/<d> 块/字段序、
//       景别/风格/运镜短语、人物道具对账、同框拆解、段长/集长/台词装载。
// 用法: node sb_fix_auto.mjs <storyboard.json> --script <script.json> [--cast x.json --art x.json --outline x.json]
import { readFileSync, writeFileSync } from 'fs';
import { basename } from 'path';

const MJS = '/home/mirror/Agnes-Agent/app/skills/novel-storyboard/scripts/novel-storyboard.mjs';
const mod = await import('file://' + MJS);
const {
  H3_TOKENS, SHOT_SIZES, CAMERA_MOVES, DEFAULT_STYLE, STYLE_PRESETS,
  cutStarts, h3CutTime, h3AlignmentLine, segSeconds, expandScript,
  gateReport, paramsOf, validateStoryboard,
} = mod;

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] ?? fallback : fallback;
};
const boardPath = argv.find((a) => !a.startsWith('-'));
const scriptPath = flag('--script');
if (!boardPath || !scriptPath) {
  console.error('用法: sb_fix_auto.mjs <sb.json> --script <script.json> [--cast --art --outline]');
  process.exit(2);
}
const btn = basename(boardPath);

const read = (p) => {
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; }
};
const board = read(boardPath);
const script = read(scriptPath);
if (!board) { console.log(`E_NOBOARD ${btn}`); process.exit(1); }
if (!script) { console.log(`E_NOSCRIPT ${btn}`); process.exit(1); }

const params = paramsOf(board);
const lang = board.promptLang ?? 'en';
const tk = H3_TOKENS[lang] ?? H3_TOKENS.en;
const styleId = board.style ?? DEFAULT_STYLE;
const style = STYLE_PRESETS[styleId] ?? STYLE_PRESETS[DEFAULT_STYLE];
const expanded = expandScript(script);

// 禁名清单（英文正文之外不得出现角色名）
const banned = new Set();
const cast = read(flag('--cast'));
const outline = read(flag('--outline'));
for (const c of outline?.characters ?? []) if (c?.name) banned.add(c.name);
for (const c of cast?.characters ?? []) {
  if (c?.name) banned.add(c.name);
  for (const a of c?.aliases ?? []) banned.add(a);
}

const CJK = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/g;
const stripCJK = (s) => String(s ?? '').replace(CJK, '');
const stripBanned = (s) => {
  let out = String(s ?? '');
  for (const n of banned) out = out.split(n).join('');
  return out;
};
const cleanEn = (s) => stripBanned(stripCJK(String(s ?? ''))).replace(/\s+/g, ' ').trim();

const SEC = params.minCutSeconds ?? 2;
const SEC_MAX = params.maxCutSeconds ?? 5;
const SEG_MAX = params.maxSegmentSeconds ?? 15;

const fixes = { ids: 0, pack: 0, h3: 0, cut: 0, frame: 0, ref: 0, crowd: 0, dur: 0, dlg: 0 };
const warns = [];

for (const ep of board.episodes ?? []) {
  const sEp = expanded.get(ep.ep);
  const label = `E${String(ep.ep).padStart(2, '0')}`;
  if (!sEp) { warns.push(`${label} 不在剧本（跳过）`); continue; }

  // ---- 1) 段打包：切点顺序保持，按 15s 上限重装，段号重编 ----
  const rawSegs = ep.segments ?? [];
  const packed = [];
  for (let si = 0; si < rawSegs.length; si++) {
    const seg = rawSegs[si];
    const scene = sEp.scenes[(seg.sceneIndex ?? 1) - 1];
    // 把该段所有切按 order 排好（模型切序必须已是认领顺序）
    const order = [...Array(seg.cuts?.length ?? 0).keys()];
    let cur = null;
    for (const ci of order) {
      const cut = seg.cuts[ci];
      if (cut && (segSeconds({ cuts: [cut] }) > SEG_MAX || (cut.seconds ?? 0) > SEG_MAX)) {
        cut.seconds = SEG_MAX;
      }
      if (!cur) {
        cur = { id: '', sceneIndex: seg.sceneIndex ?? 1, cuts: [], h3Prompt: null };
        packed.push(cur);
      }
      if (segSeconds({ cuts: [...cur.cuts, cut] }) > SEG_MAX) {
        cur = { id: '', sceneIndex: seg.sceneIndex ?? 1, cuts: [], h3Prompt: null };
        packed.push(cur);
        fixes.pack++;
      }
      cur.cuts.push(cut);
    }
  }
  // 空段兜底：整场无分镜时留一个空段（切留空，交给模型/汇报点名）
  if (packed.length === 0) {
    packed.push({ id: '', sceneIndex: 1, cuts: [], h3Prompt: null });
    warns.push(`${label} 没有任何分镜切（生成骨架但 cuts 空，模型必须补）`);
  }
  packed.forEach((seg, i) => {
    seg.id = `${label}-${String(i + 1).padStart(2, '0')}`;
    fixes.ids++;
  });

  // ---- 2) 每段：切修正 + h3Prompt 重建 ----
  for (const seg of packed) {
    const scene = sEp.scenes[(seg.sceneIndex ?? 1) - 1];
    const cuts = seg.cuts;
    // 2a) cut 级机械修正
    for (const cut of cuts) {
      const [from, to] = cut.beats ?? [];
      // 台词装载：line 秒数总和 ≤ cut.seconds（≤5）
      let dlg = 0;
      if (scene && Number.isInteger(from) && Number.isInteger(to)) {
        for (const b of scene.beats.slice(from - 1, to)) if (b.kind === 'line') dlg += b.seconds;
      }
      if (dlg > 0 && dlg > (cut.seconds ?? 0)) {
        const t = Math.min(SEG_MAX, SEC_MAX, Math.ceil((dlg + 0.05) * 10) / 10);
        if (t > (cut.seconds ?? 0)) { cut.seconds = t; fixes.dlg++; }
        if (dlg > cut.seconds) warns.push(`${seg.id} 台词 ${dlg.toFixed(1)}s 超切秒上限`);
      }
      if (!(cut.seconds >= SEC) || cut.seconds > SEC_MAX) {
        if (cuts.length === 1 && scene) cut.seconds = SEG_MAX; else cut.seconds = Math.min(SEC_MAX, Math.max(SEC, cut.seconds ?? SEC));
      }
      // 人物/道具对账：只留剧本该场有的
      if (scene) {
        const cset = new Set(scene.characters);
        cut.characters = (cut.characters ?? []).filter((c) => cset.has(c));
        const pset = new Set(scene.props);
        cut.props = (cut.props ?? []).filter((p) => pset.has(p));
        fixes.ref++;
        // 同框 ≤3，超了强制补拆解说明
        if ((cut.characters?.length ?? 0) > 3 && !String(cut.note ?? seg.note ?? '').trim()) {
          cut.note = `同框 ${cut.characters.length} 人拆解：焦点给 ${cut.characters[0]}，其余为背景群像`;
          fixes.crowd++;
        }
        // 景别短语 + 风格短语 + 全英文无禁名
        const sz = SHOT_SIZES[cut.size];
        let fr = cleanEn(cut.frame);
        if (sz && !fr.toLowerCase().includes(sz.phrase)) fr = `${fr}, ${sz.phrase}`;
        if (style && !fr.toLowerCase().includes(style.phrase)) fr = `${fr}, ${style.phrase}`;
        if (!fr) fr = `${style?.phrase ?? 'cinematic film still'}, ${sz?.phrase ?? 'medium shot'}, ambient cinematic lighting`;
        cut.frame = fr;
        fixes.frame++;
      }
    }
    // 2b) h3Prompt 重建（结构全部推导，正文尽量保留模型的英文描述）
    if (!scene) { seg.h3Prompt = ''; continue; }
    const starts = cutStarts(cuts);
    const wantLine = h3AlignmentLine(cuts, lang);
    const oldH3 = String(seg.h3Prompt ?? '');
    const oldBody = oldH3.slice(oldH3.indexOf(tk.fields[0]) >= 0 ? oldH3.indexOf(tk.fields[0]) + tk.fields[0].length : 0,
                              oldH3.indexOf(tk.fields[1]) >= 0 ? oldH3.indexOf(tk.fields[1]) : undefined);
    const oldSound = extField(oldH3, tk.fields[1], tk.fields[2]);
    const oldMusic = extField(oldH3, tk.fields[2], null);
    const shotTexts = [];
    for (let k = 1; k <= cuts.length; k++) {
      const cut = cuts[k - 1];
      const sz = SHOT_SIZES[cut.size] ?? {};
      let desc = cleanEn(extShotText(oldBody, k));
      if (!desc) desc = cleanEn(cut.frame).replace(/, cinematic film still/g, '').replace(/, (extreme wide|wide|medium|close|extreme close)-?up? shot/g, '');
      if (!desc) desc = `cinematic ${sz.zh ?? 'medium'} shot framing the scene`;
      desc = desc.replace(/^[,.\s]+|[,.\s]+$/g, '');
      shotTexts.push(`[Shot ${k}] At ${h3CutTime(starts[k - 1])}, ${cut.camera ?? 'Static Shot'}, ${sz.phrase ?? cut.size}: ${desc}`);
    }
    // <d> 台词块：认领节拍里的 line 逐字进 <d>[说话人] 台词</d>
    const dBlocks = [];
    const dlgSet = new Set();
    for (const cut of cuts) {
      const [from, to] = cut.beats ?? [];
      if (!Number.isInteger(from) || !Number.isInteger(to)) continue;
      for (const b of scene.beats.slice(from - 1, to)) {
        if (b.kind !== 'line') continue;
        if (dlgSet.has(b.n)) continue;
        dlgSet.add(b.n);
        dBlocks.push(`<d>[${b.speaker ?? '?'}] ${b.text}</d>`);
      }
    }
    const sound = cleanEn(oldSound) || 'ambient room tone, light murmur and footsteps';
    const music = cleanEn(oldMusic) || 'subtle tension score, low strings';
    const body = shotTexts.join('\n');
    seg.h3Prompt = [
      wantLine,
      '',
      `${tk.fields[0]}\n${body}`,
      `${tk.fields[1]} ${sound}`,
      `${tk.fields[2]} ${music}`,
      dBlocks.length ? `dialogue:\n${dBlocks.join('\n')}` : '',
    ].filter(Boolean).join('\n');
    fixes.h3++;
  }

  // ---- 3) 每集时长对齐目标 ±15%（调非对白切秒数） ----
  const target = sEp.targetSeconds ?? 120;
  const tol = params.tolerance ?? 0.15;
  const lo = target * (1 - tol), hi = target * (1 + tol);
  let total = packed.reduce((n, s) => n + segSeconds(s), 0);
  let guard = 0;
  while ((total < lo || total > hi) && guard++ < 60) {
    // 找可调切
    let cand = null;
    outer:
    for (let si = packed.length - 1; si >= 0; si--) {
      const cuts = packed[si].cuts;
      for (let ci = cuts.length - 1; ci >= 0; ci--) {
        const c = cuts[ci];
        const [from, to] = c.beats ?? [];
        let dlg = 0;
        if (scene2(packed[si], sEp) && Number.isInteger(from) && Number.isInteger(to)) {
          const sc = sEp.scenes[(packed[si].sceneIndex ?? 1) - 1];
          for (const b of sc.beats.slice(from - 1, to)) if (b.kind === 'line') dlg += b.seconds;
        }
        if (dlg <= 0) { cand = { seg: packed[si], cut: c }; break outer; }
      }
    }
    if (!cand) { warns.push(`${label} 无对白切可调，时长 ${total.toFixed(1)}s 不在 [${lo.toFixed(0)},${hi.toFixed(0)}]`); break; }
    if (total < lo) {
      const add = Math.min(SEG_MAX - segSeconds({ cuts: cand.seg.cuts.slice(0, -1) }) - cand.cut.seconds, SEG_MAX - cand.cut.seconds, SEC_MAX - cand.cut.seconds);
      if (add <= 0) break;
      cand.cut.seconds = Math.round((cand.cut.seconds + add) * 10) / 10;
      fixes.dur++;
    } else {
      const sub = Math.min(cand.cut.seconds - SEC, total - hi);
      if (sub <= 0) break;
      cand.cut.seconds = Math.round((cand.cut.seconds - sub) * 10) / 10;
      fixes.dur++;
    }
    total = packed.reduce((n, s) => n + segSeconds(s), 0);
  }
  ep.segments = packed;
}

// ---- 4) 保存 + 汇总 ----
writeFileSync(boardPath, JSON.stringify(board, null, 2), 'utf8');
const gates = gateReport(board, { script, cast, outline, art: read(flag('--art')) });
const green = gates.filter((g) => g.ok).length;
console.log(`FIXED ids=${fixes.ids} pack=${fixes.pack} h3=${fixes.h3} cut=${fixes.cut} frame=${fixes.frame} ref=${fixes.ref} crowd=${fixes.crowd} dur=${fixes.dur} dlg=${fixes.dlg}`);
console.log(`GATES ${green}/${gates.length} 绿`);
for (const g of gates) if (!g.ok) console.log(`  ✗ ${g.label}${g.detail ? `（${String(g.detail).slice(0, 300)}）` : ''}`);
for (const w of warns.slice(0, 8)) console.log(`  WARN ${w}`);
process.exit(gates.every((g) => g.ok) ? 0 : 1);

// ---- 工具函数 ----
function extField(h3, field, nextField) {
  const a = h3.indexOf(field);
  if (a < 0) return '';
  const b = nextField ? h3.indexOf(nextField, a + field.length) : -1;
  return h3.slice(a + field.length, b < 0 ? undefined : b);
}
function extShotText(body, k) {
  const tk = H3_TOKENS.en;
  const a = body.indexOf(tk.shot(k));
  if (a < 0) return '';
  const b = body.indexOf(tk.shot(k + 1));
  let s = body.slice(a + tk.shot(k).length, b < 0 ? undefined : b);
  s = s.replace(/At \d{2}:\d{2}\.\d{3},/g, ' ').replace(/^[,\s:]+/, '');
  return s;
}
function scene2(seg, sEp) { return sEp.scenes[(seg.sceneIndex ?? 1) - 1]; }