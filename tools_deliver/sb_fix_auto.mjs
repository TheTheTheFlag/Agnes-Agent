#!/usr/bin/env node
// sb_fix_auto.mjs v3 — storyboard 机械补全器
// v3 变更：① 切拆分句改为「加入前预闭合」，溢出发生在末拍也能正确拆开
//         ② 拆句后按场景重打包（≤15s），h3 重建抽成函数，重打包后统一重建
//         ③ 保留 v1 的段打包按 15s 重装
// 用法: node sb_fix_auto.mjs <sb.json> --script <script.json> [--map <map.json>] [--cast --art --outline]
import { readFileSync, writeFileSync } from 'fs';
import { basename } from 'path';

const MJS = '/home/mirror/Agnes-Agent/app/skills/novel-storyboard/scripts/novel-storyboard.mjs';
const mod = await import('file://' + MJS);
const {
  H3_TOKENS, SHOT_SIZES, CAMERA_MOVES, DEFAULT_STYLE, STYLE_PRESETS,
  cutStarts, h3CutTime, h3AlignmentLine, segSeconds, expandScript,
  gateReport, paramsOf,
} = mod;

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] ?? fallback : fallback;
};
const boardPath = argv.find((a) => !a.startsWith('-'));
const scriptPath = flag('--script');
if (!boardPath || !scriptPath) {
  console.error('用法: sb_fix_auto.mjs <sb.json> --script <script.json> [--map --cast --art --outline]');
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
const beatMap = read(flag('--map'));

const params = paramsOf(board);
const lang = board.promptLang ?? 'en';
const tk = H3_TOKENS[lang] ?? H3_TOKENS.en;
const styleId = board.style ?? DEFAULT_STYLE;
const style = STYLE_PRESETS[styleId] ?? STYLE_PRESETS[DEFAULT_STYLE];
const expanded = expandScript(script);

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
const DIALOG_LIMIT = SEC_MAX - 0.2;

const fixes = { ids: 0, pack: 0, h3: 0, cut: 0, frame: 0, ref: 0, crowd: 0, dur: 0, dlg: 0, split: 0, map: 0 };
const warns = [];

/** 重建一段的 h3Prompt（结构全推导，正文保留模型的英文描述） */
function buildH3(seg, scene, board) {
  const starts = cutStarts(seg.cuts);
  const wantLine = h3AlignmentLine(seg.cuts, lang);
  const oldH3 = String(seg.h3Prompt ?? '');
  const f0 = oldH3.indexOf(tk.fields[0]);
  const f1 = oldH3.indexOf(tk.fields[1]);
  const f2 = oldH3.indexOf(tk.fields[2]);
  const oldBody = oldH3.slice(f0 >= 0 ? f0 + tk.fields[0].length : 0, f1 >= 0 ? f1 : undefined);
  const oldSound = extField(oldH3, tk.fields[1], tk.fields[2]);
  const oldMusic = extField(oldH3, tk.fields[2], null);
  const shotTexts = [];
  for (let k = 1; k <= seg.cuts.length; k++) {
    const cut = seg.cuts[k - 1];
    const sz = SHOT_SIZES[cut.size] ?? {};
    let desc = cleanEn(extShotText(oldBody, k));
    if (!desc) desc = cleanEn(cut.frame).replace(/, cinematic film still/g, '').replace(/, (extreme wide|wide|medium|close|extreme close)-?up? shot/g, '');
    if (!desc) desc = `cinematic ${sz.zh ?? 'medium'} shot framing the scene`;
    desc = desc.replace(/^[.,\s]+|[.,\s]+$/g, '');
    shotTexts.push(`[Shot ${k}] At ${h3CutTime(starts[k - 1])}, ${cut.camera ?? 'Static Shot'}, ${sz.phrase ?? cut.size}: ${desc}`);
  }
  const dBlocks = [];
  const dlgSet = new Set();
  for (const cut of seg.cuts) {
    const [from, to] = cut.beats ?? [];
    if (!Number.isInteger(from) || !Number.isInteger(to)) continue;
    for (const b of scene.beats.slice(from - 1, to)) {
      if (b.kind !== 'line' || dlgSet.has(b.n)) continue;
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

function extField(h3, field, nextField) {
  const a = h3.indexOf(field);
  if (a < 0) return '';
  const b = nextField ? h3.indexOf(nextField, a + field.length) : -1;
  return h3.slice(a + field.length, b < 0 ? undefined : b);
}
function extShotText(body, k) {
  const t = H3_TOKENS.en;
  const a = body.indexOf(t.shot(k));
  if (a < 0) return '';
  const b = body.indexOf(t.shot(k + 1));
  let s = body.slice(a + t.shot(k).length, b < 0 ? undefined : b);
  s = s.replace(/At \d{2}:\d{2}\.\d{3},/g, ' ').replace(/^[,\s:]+/, '');
  return s;
}

for (const ep of board.episodes ?? []) {
  const sEp = expanded.get(ep.ep);
  const label = `E${String(ep.ep).padStart(2, '0')}`;
  if (!sEp) { warns.push(`${label} 不在剧本（跳过）`); continue; }

  // ---- 0) 节拍重对齐（split_lines 后） ----
  const emap = beatMap ? beatMap[String(ep.ep)] ?? beatMap[ep.ep] : null;
  for (const seg of ep.segments ?? []) {
    const smap = emap ? emap[String(seg.sceneIndex)] ?? emap[seg.sceneIndex] : null;
    if (!smap) continue;
    for (const cut of seg.cuts ?? []) {
      const [f, t] = cut.beats ?? [];
      if (!Number.isInteger(f) || !Number.isInteger(t)) continue;
      const nf = smap[f]?.[0] ?? f;
      const nt = smap[t]?.[1] ?? t;
      if (nf !== f || nt !== t) { cut.beats = [nf, nt]; fixes.map++; }
    }
  }

  // ---- 1) 初始段打包（切序保持） ----
  const rawSegs = ep.segments ?? [];
  let packed = [];
  for (const seg of rawSegs) {
    let cur = null;
    for (const cut of seg.cuts ?? []) {
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

  // ---- 2a) 切拆分句：认领区间台词 >4.8s 按行边界拆成多个切 ----
  for (const seg of packed) {
    const scene = sEp.scenes[(seg.sceneIndex ?? 1) - 1];
    if (!scene) continue;
    const src = seg.cuts;
    const out = [];
    for (const cut of src) {
      const [from, to] = cut.beats ?? [];
      if (!Number.isInteger(from) || !Number.isInteger(to) || from > to) { out.push(cut); continue; }
      const beats = scene.beats.slice(from - 1, to);
      let dlg = 0;
      for (const b of beats) if (b.kind === 'line') dlg += b.seconds;
      if (dlg > DIALOG_LIMIT && beats.length > 1) {
        let lo = from;
        let acc = 0;
        for (let bi = from; bi <= to; bi++) {
          const b = scene.beats[bi - 1];
          const bsec = b.kind === 'line' ? b.seconds : 0;
          if (lo < bi && bsec > 0 && acc + bsec > DIALOG_LIMIT) {
            const d2 = scene.beats.slice(lo - 1, bi - 1).filter((x) => x.kind === 'line').reduce((n, x) => n + x.seconds, 0);
            const nc = { ...cut };
            nc.beats = [lo, bi - 1];
            nc.seconds = Math.min(SEC_MAX, Math.max(SEC, Math.round((Math.max(d2, 1.0) + 0.3) * 10) / 10));
            out.push(nc);
            fixes.split++;
            lo = bi;
            acc = 0;
          }
          acc += bsec;
          const next = bi < to ? scene.beats[bi] : null;
          const nextIsLine = next?.kind === 'line';
          if ((!nextIsLine && acc > 0) || bi === to) {
            const d2 = scene.beats.slice(lo - 1, bi).filter((x) => x.kind === 'line').reduce((n, x) => n + x.seconds, 0);
            const nc = { ...cut };
            nc.beats = [lo, bi];
            nc.seconds = Math.min(SEC_MAX, Math.max(SEC, Math.round((Math.max(d2, 1.0) + 0.3) * 10) / 10));
            out.push(nc);
            fixes.split++;
            lo = bi + 1;
            acc = 0;
          }
        }
      } else if (dlg > SEC_MAX) {
        warns.push(`${seg.id} 单拍台词 ${dlg.toFixed(1)}s 仍超上限（需剧本再拆）`);
        out.push(cut);
      } else {
        out.push(cut);
      }
    }
    seg.cuts = out;
  }

  // ---- 2b) 重打包：按场景把切塞进 ≤15s 段 ----
  const byScene = new Map();
  for (const seg of packed) {
    const si = seg.sceneIndex ?? 1;
    if (!byScene.has(si)) byScene.set(si, []);
    byScene.get(si).push(...seg.cuts);
  }
  const repacked = [];
  for (const [si, cuts] of byScene) {
    let cur = null;
    for (const cut of cuts) {
      if (!cur) {
        cur = { id: '', sceneIndex: si, cuts: [], h3Prompt: null };
        repacked.push(cur);
      }
      if (segSeconds({ cuts: [...cur.cuts, cut] }) > SEG_MAX) {
        cur = { id: '', sceneIndex: si, cuts: [], h3Prompt: null };
        repacked.push(cur);
        fixes.pack++;
      }
      cur.cuts.push(cut);
    }
  }
  if (repacked.length === 0) {
    repacked.push({ id: '', sceneIndex: 1, cuts: [], h3Prompt: null });
    warns.push(`${label} 没有任何分镜切（骨架空，模型必须补）`);
  }
  repacked.forEach((seg, i) => { seg.id = `${label}-${String(i + 1).padStart(2, '0')}`; fixes.ids++; });
  packed = repacked;

  // ---- 2c) 每段：切级修正 + h3Prompt 重建 ----
  for (const seg of packed) {
    const scene = sEp.scenes[(seg.sceneIndex ?? 1) - 1];
    for (const cut of seg.cuts) {
      const [from, to] = cut.beats ?? [];
      let dlg = 0;
      if (scene && Number.isInteger(from) && Number.isInteger(to)) {
        for (const b of scene.beats.slice(from - 1, to)) if (b.kind === 'line') dlg += b.seconds;
      }
      if (dlg > (cut.seconds ?? 0) && dlg <= SEC_MAX) {
        cut.seconds = Math.min(SEC_MAX, Math.max(SEC, Math.round((dlg + 0.3) * 10) / 10));
        fixes.dlg++;
      }
      if (!(cut.seconds >= SEC) || cut.seconds > SEC_MAX) {
        cut.seconds = Math.min(SEC_MAX, Math.max(SEC, cut.seconds ?? SEC));
      }
      if (scene) {
        const cset = new Set(scene.characters);
        cut.characters = (cut.characters ?? []).filter((c) => cset.has(c));
        const pset = new Set(scene.props);
        cut.props = (cut.props ?? []).filter((p) => pset.has(p));
        fixes.ref++;
        if ((cut.characters?.length ?? 0) > 3 && !String(cut.note ?? seg.note ?? '').trim()) {
          cut.note = `同框 ${cut.characters.length} 人拆解：焦点给 ${cut.characters[0]}，其余为背景群像`;
          fixes.crowd++;
        }
        const sz = SHOT_SIZES[cut.size];
        let fr = cleanEn(cut.frame);
        if (sz && !fr.toLowerCase().includes(sz.phrase)) fr = `${fr}, ${sz.phrase}`;
        if (style && !fr.toLowerCase().includes(style.phrase)) fr = `${fr}, ${style.phrase}`;
        if (!fr) fr = `${style?.phrase ?? 'cinematic film still'}, ${sz?.phrase ?? 'medium shot'}, ambient cinematic lighting`;
        cut.frame = fr;
        fixes.frame++;
      }
    }
    fixes.cut += seg.cuts.length;
    if (!scene) { seg.h3Prompt = ''; continue; }
    buildH3(seg, scene, board);
  }

  // ---- 3) 每集时长对齐 ----
  const target = sEp.targetSeconds ?? 120;
  const tol = params.tolerance ?? 0.15;
  const lo = target * (1 - tol), hi = target * (1 + tol);
  let total = packed.reduce((n, s) => n + segSeconds(s), 0);
  let guard = 0;
  while ((total < lo || total > hi) && guard++ < 60) {
    let cand = null;
    outer:
    for (let si = packed.length - 1; si >= 0; si--) {
      const cuts = packed[si].cuts;
      for (let ci = cuts.length - 1; ci >= 0; ci--) {
        const c = cuts[ci];
        const [from, to] = c.beats ?? [];
        let dlg = 0;
        if (Number.isInteger(from) && Number.isInteger(to)) {
          const sc = sEp.scenes[(packed[si].sceneIndex ?? 1) - 1];
          for (const b of sc.beats.slice(from - 1, to)) if (b.kind === 'line') dlg += b.seconds;
        }
        if (dlg <= 0) { cand = { seg: packed[si], cut: c }; break outer; }
      }
    }
    if (!cand) { warns.push(`${label} 无对白切可调，时长 ${total.toFixed(1)}s 不在 [${lo.toFixed(0)},${hi.toFixed(0)}]`); break; }
    if (total < lo) {
      const add = Math.min(SEC_MAX - cand.cut.seconds, SEG_MAX - segSeconds({ cuts: cand.seg.cuts.slice(0, -1) }) - cand.cut.seconds);
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

writeFileSync(boardPath, JSON.stringify(board, null, 2), 'utf8');
const gates = gateReport(board, { script, cast, outline, art: read(flag('--art')) });
const green = gates.filter((g) => g.ok).length;
console.log(`FIXED ids=${fixes.ids} pack=${fixes.pack} h3=${fixes.h3} cut=${fixes.cut} frame=${fixes.frame} ref=${fixes.ref} crowd=${fixes.crowd} dur=${fixes.dur} dlg=${fixes.dlg} split=${fixes.split} map=${fixes.map}`);
console.log(`GATES ${green}/${gates.length} 绿`);
for (const g of gates) if (!g.ok) console.log(`  ✗ ${g.label}${g.detail ? `（${String(g.detail).slice(0, 300)}）` : ''}`);
for (const w of warns.slice(0, 10)) console.log(`  WARN ${w}`);
process.exit(gates.every((g) => g.ok) ? 0 : 1);