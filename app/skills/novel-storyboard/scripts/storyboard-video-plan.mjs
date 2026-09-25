#!/usr/bin/env node
// storyboard-video-plan.mjs — 从 storyboard.json 生成「逐段 agnes-media video 命令单」
// 用法: node scripts/storyboard-video-plan.mjs <storyboard.json> [--episode E]
//   --episode E  : 只输出该集（E 形如 "E01" / "1"；不传则全部）
// 输出 markdown 命令单到 stdout；段文件夹按 <段号>/f<切序>.png 定位（相对 storyboard.json 所在目录）
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';

const args = process.argv.slice(2);
const flag = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const positional = args.filter(a => !a.startsWith('--'));
const sbPath = positional[0];
if (!sbPath) { console.error("用法: storyboard-video-plan.mjs <storyboard.json> [--episode E01|1]"); process.exit(2); }
const epFilter = (flag('--episode', '') || '').toUpperCase().replace(/^EP/, 'E');
const baseDir = dirname(sbPath) === '.' ? process.cwd() : dirname(sbPath);

let sb;
try { sb = JSON.parse(readFileSync(sbPath, 'utf-8')); }
catch (e) { console.error("无法解析 storyboard.json:", e.message); process.exit(1); }

const PRESETS = [3, 5, 10, 18];
const near = s => PRESETS.reduce((b, a) => (Math.abs(a - s) < Math.abs(b - s) ? a : b), PRESETS[0]);
const eps = Array.isArray(sb.episodes) ? sb.episodes : [];
const rows = [];
let warn = 0;

for (const ep of eps) {
  const epId = String(ep.ep || ep.episodeIndex || ep.id || '').trim();
  const epTag = (epId.startsWith('E') ? epId : 'E' + String(epId).padStart(2, '0'));
  if (epFilter && !(epTag === epFilter || epTag.startsWith(epFilter + '-') || epId === epFilter)) continue;
  const segs = Array.isArray(ep.segments) ? ep.segments : [];
  for (const s of segs) {
    const segId = String(s.id || '');
    if (!segId) { warn++; console.log(`⚠️ 段缺少 id（集 ${epTag}），跳过`); continue; }
    const cuts = Array.isArray(s.cuts) ? s.cuts : [];
    const segSecs = cuts.reduce((t, c) => t + Number(c && c.seconds || 0), 0) || 5;
    const durP = near(segSecs);
    const dir = join(baseDir, segId);
    const f1 = join(dir, 'f1.png');
    if (!existsSync(f1)) { warn++; rows.push({ warn: 'f1 缺', segId, tag: epTag }); continue; }
    const kfs = [];
    for (let i = 2; i <= 30; i++) { const p = join(dir, `f${i}.png`); if (existsSync(p)) kfs.push(p); else break; }
    const prompt = String(s.h3Prompt || s.prompt || '').trim();
    const cmdParts = ["python", "app/skills_builtin/agnes-media/media.py", "video",
      `--duration ${durP}`, '--timeout 356400', `--image "${f1}"`];
    for (const k of kfs) cmdParts.push(`--keyframes "${k}"`);
    const promptArg = prompt
      ? `--prompt "${prompt.replace(/"/g, '\\"').slice(0, 6000)}"`
      : "# ⚠️ 段缺 h3Prompt，需先在 SKILL.md Step 4.5 抽成自然语言动作/镜头描述";
    rows.push({ segId, tag: epTag, segSecs, durP, cmd: cmdParts.join(' \\\n  ') + " \\\n  " + promptArg, f1, kfCount: kfs.length + 1 });
  }
}

const epTag0 = epFilter || '全部';
console.log(`# 视频命令单 · ${epTag0}（来自 ${sbPath}）`);
console.log(`共 ${rows.filter(r => !r.warn).length} 段将生成；缺失 f1 跳过 ${rows.filter(r => r.warn).length} 段。`);
console.log(`> 用法：逐段执行（每段一次调用，别批量）；stdout 出现 \`FILE=\` 后复制到 \`<段号>/<段号>.mp4\`。`);
console.log(`> H3 提示词若视频网关不吃 <d>/镜号 结构，先按 SKILL.md Step 4.5 抽成自然语言。\n`);
for (const r of rows) {
  if (r.warn) { console.log(`## ${r.segId} — ⚠️ ${r.warn}（该段文件夹缺少 f1.png，先出分镜图再回来）\n`); continue; }
  console.log(`## ${r.segId} · 段长 ${r.segSecs}s → --duration ${r.durP} · 关键帧 ${r.kfCount} 张`);
  console.log('```bash');
  console.log(r.cmd);
  console.log('```');
  console.log(`成功后：\`cp "<FILE 路径>" "${join(r.segId, r.segId + '.mp4').replace(/\\/g, '/')}"\`\n`);
}
console.log('VIDEO_PLAN_DONE');