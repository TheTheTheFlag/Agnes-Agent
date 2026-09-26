#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sb_img_gap.py — 列出 storyboard 缺 f1.png 的段。
用法: python3 sb_img_gap.py <storyboard.json> [E01|1]
输出：缺图段 id 一行一个（无则空）。"""
import json, os, sys

sb_path = sys.argv[1]
ep = sys.argv[2] if len(sys.argv) > 2 else None
base = os.path.dirname(os.path.abspath(sb_path))
sb = json.load(open(sb_path, encoding='utf-8'))
missing = []
total = 0
for e in sb.get('episodes', []):
    tag = f"E{int(e.get('ep', 0)):02d}"
    if ep and tag.upper() != ep.upper().replace('EP', 'E'):
        continue
    for s in e.get('segments', []):
        sid = str(s.get('id', ''))
        if not sid:
            continue
        total += 1
        if not os.path.exists(os.path.join(base, sid, 'f1.png')):
            missing.append(sid)
print(f"GAP total={total} missing={len(missing)}")
for sid in missing:
    print(sid)