# -*- coding: utf-8 -*-
"""归一化段视频（concat 前置步骤，任何项目通用）。

- 按 manifest 声明时长裁剪（-t <decl>）：消除 24fps 帧对齐溢出（18s 档实为 18.375s）
- 精确 16:9 pad：等比缩放 + 居中黑边（1280x704 -> 1280x720，setsar=1；见 render-contracts §1.1）
- 音频保留（-c:a aac 重编码，默认不再 -an——自带配音是交付物）、libx264 crf18 preset medium、+faststart
- 覆盖前备份到 <R>/.review/pre_normalize/ 并登记 INDEX.jsonl（路径 + sha256）
- 幂等：已达标（1280x720 / 声明时长 / 含 audio 流；STATIC_FALLBACK 标记段豁免 audio）的段直接 SKIP
用法：normalize_segments.py <项目根> [EP ...] [--workers N]
  不给 EP 时扫描 <R>/投喂稿/*/manifest.json 全量；兼容旧写法第二参直接给并行度数字
退出码：0 全 OK/SKIP；1 有 FAIL；2 用法/缺 manifest
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
R = sys.argv[1]
WORKERS, EPS = 3, []
_i = 2
while _i < len(sys.argv):
    a = sys.argv[_i]
    if a in ('--workers', '-j') and _i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[_i + 1]); _i += 2
    elif a.isdigit() and not EPS:      # 兼容旧用法：<项目根> <并行度>
        WORKERS = int(a); _i += 1
    else:
        EPS.append(a); _i += 1

if not os.path.isdir(R):
    print('!! 项目根不存在: %s' % R)
    sys.exit(2)
if not EPS:
    feed = os.path.join(R, '投喂稿')
    if os.path.isdir(feed):
        EPS = sorted(d for d in os.listdir(feed)
                     if os.path.exists(os.path.join(feed, d, 'manifest.json')))
if not EPS:
    print('!! 未发现任何 <投喂稿>/<EP>/manifest.json（且未指定 EP）')
    sys.exit(2)

BK = os.path.join(R, '.review', 'pre_normalize')
os.makedirs(BK, exist_ok=True)
IDX = os.path.join(BK, 'INDEX.jsonl')
_LOCK = threading.Lock()


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error',
                        '-show_entries', 'stream=codec_type,width,height,r_frame_rate',
                        '-show_entries', 'format=duration',
                        '-of', 'json', path], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except Exception:
        return None
    streams = d.get('streams', [])
    v = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if v is None:
        return None
    return {
        'w': int(v.get('width') or 0),
        'h': int(v.get('height') or 0),
        'fps': v.get('r_frame_rate'),
        'dur': float((d.get('format') or {}).get('duration') or 0),
        'audio': any(s.get('codec_type') == 'audio' for s in streams),
    }


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def do_seg(seg, decl, tw, th):
    mp4 = os.path.join(R, seg, seg + '.mp4')
    if not os.path.exists(mp4):
        return (seg, 'MISSING', 'no file')
    info = probe(mp4)
    if info is None:
        return (seg, 'PROBE_FAIL', '')
    static_fb = os.path.exists(os.path.join(R, seg, 'STATIC_FALLBACK'))
    if (info['w'] == tw and info['h'] == th and (info['audio'] or static_fb)
            and abs(info['dur'] - decl) <= 0.05):
        return (seg, 'SKIP', 'already %dx%d %.3fs' % (info['w'], info['h'], info['dur']))

    bak = os.path.join(BK, seg + '.mp4')
    if not os.path.exists(bak):
        shutil.copy2(mp4, bak)
        line = json.dumps({'file': mp4, 'backup': bak, 'sha256': sha256(bak),
                           'at': time.strftime('%Y-%m-%dT%H:%M:%S')},
                          ensure_ascii=False)
        with _LOCK:
            with open(IDX, 'a', encoding='utf-8') as f:
                f.write(line + '\n')

    vf = ('scale=%d:%d:force_original_aspect_ratio=decrease,'
          'pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1' % (tw, th, tw, th))
    tmp = mp4 + '.norm.tmp.mp4'
    cmd = ['ffmpeg', '-y', '-i', mp4, '-vf', vf, '-t', str(decl),
           '-c:v', 'libx264', '-crf', '18', '-preset', 'medium',
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
           '-movflags', '+faststart', tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        return (seg, 'FFMPEG_FAIL', (r.stderr or '')[-300:].replace('\n', ' '))
    os.replace(tmp, mp4)

    v2 = probe(mp4)
    if (v2 is None or v2['w'] != tw or v2['h'] != th or (not v2['audio'] and not static_fb)
            or abs(v2['dur'] - decl) > 0.1):
        return (seg, 'VERIFY_FAIL', json.dumps(v2))
    return (seg, 'OK', '%dx%d/%.2fs -> %dx%d/%.3fs fps%s' % (
        info['w'], info['h'], info['dur'], v2['w'], v2['h'], v2['dur'], v2['fps']))


jobs, tw, th = [], 1280, 720
for ep in EPS:
    mf = os.path.join(R, '投喂稿', ep, 'manifest.json')
    if not os.path.exists(mf):
        print('!! 缺 manifest: %s' % mf, flush=True)
        sys.exit(2)
    d = json.load(open(mf, encoding='utf-8'))
    for s in d['segments']:
        jobs.append((s['seg'], int(s['duration'])))

print('eps=%s total segments: %d  parallel: %d  target=%dx%d'
      % (','.join(EPS), len(jobs), WORKERS, tw, th), flush=True)
t0 = time.time()
results = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for seg, st, msg in ex.map(lambda a: do_seg(a[0], a[1], tw, th), jobs):
        results.append((seg, st, msg))
        print('%-9s %-11s %s' % (seg, st, msg), flush=True)

bad = [r for r in results if r[1] not in ('OK', 'SKIP')]
print('done in %.0fs  OK=%d SKIP=%d FAIL=%d' % (
    time.time() - t0,
    sum(1 for r in results if r[1] == 'OK'),
    sum(1 for r in results if r[1] == 'SKIP'),
    len(bad)), flush=True)
for b in bad:
    print('  FAIL %s %s %s' % b, flush=True)
sys.exit(1 if bad else 0)
