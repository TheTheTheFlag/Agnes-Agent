#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""split_lines.py v2 — 剧本合并稿机械拆句：把超长台词在标点边界拆句，保证每句≤17字（~3.8s）。
输出拆分后的剧本 + 节拍映射（old→new 区间），供分镜 cut.beats 重对齐。
用法: python3 split_lines.py <script.json> [--out <new.json>] [--map <map.json>]
hookBeat 经映射修正到新拍号。
"""
import json, re, sys

def clean_len(s):
    return len(re.sub(r'\s+', '', s or ''))

MAX_CHARS = 17  # ~3.8s @4.5字/s，装得进 ≤5s 切

def hard_split(text, limit=MAX_CHARS):
    """把一段文本切成每段 clean ≤ limit 的块，优先在句读/逗号边界切。"""
    txt = text.strip()
    if not txt:
        return []
    if clean_len(txt) <= limit:
        return [txt]
    out = []
    while clean_len(txt) > limit:
        window = txt[:min(len(txt), limit + 2)]
        cut = -1
        for idx in range(len(window) - 1, 0, -1):
            if window[idx] in '。！？；…，、 ':
                cut = idx
                break
        if cut <= 0:
            cut = limit
        out.append(txt[:cut + 1].strip())
        txt = txt[cut + 1:]
    if txt:
        out.append(txt.strip())
    return [p for p in out if p]

def split_text(text, limit=MAX_CHARS):
    text = str(text).strip()
    if clean_len(text) <= limit:
        return [text]
    # 标点边界优先整段切分，再对每个超限片段递归硬切
    chunks = []
    cur = ''
    for ch in text:
        cur += ch
        if clean_len(cur) >= limit and ch in '。！？；…':
            chunks.append(cur)
            cur = ''
    if cur:
        chunks.append(cur)
    # 若整段一个句读都没有（或只有末尾），直接从开头递归硬切
    if len(chunks) <= 1 and clean_len(text) > limit:
        chunks = []
        cur = ''
        for ch in text:
            cur += ch
            if clean_len(cur) >= limit + 1 and ch in '，、 ':
                chunks.append(cur)
                cur = ''
        if cur:
            chunks.append(cur)
    result = []
    for c in chunks:
        if clean_len(c) > limit:
            result.extend(hard_split(c, limit))
        else:
            result.append(c)
    return [p.strip() for p in result if p and p.strip()]

def main():
    src = sys.argv[1]
    outp = None
    mapp = None
    if '--out' in sys.argv:
        outp = sys.argv[sys.argv.index('--out') + 1]
    if '--map' in sys.argv:
        mapp = sys.argv[sys.argv.index('--map') + 1]
    doc = json.load(open(src, encoding='utf-8'))
    mapping = {}
    split_count = 0
    for ep in doc.get('episodes', []):
        e = ep.get('ep')
        emap = {}
        for si, scene in enumerate(ep.get('scenes', []), 1):
            flow = scene.get('flow', [])
            new_flow = []
            smap = {}
            new_pos = 1
            for old_pos, b in enumerate(flow, 1):
                if 'line' in b and clean_len(b.get('line')) > MAX_CHARS:
                    chunks = split_text(b['line'])
                    start = new_pos
                    for c in chunks:
                        nb = {'speaker': b.get('speaker')}
                        if b.get('delivery'):
                            nb['delivery'] = b['delivery']
                        nb['line'] = c
                        new_flow.append(nb)
                        new_pos += 1
                    smap[old_pos] = [start, new_pos - 1]
                    split_count += 1
                else:
                    new_flow.append({**b})
                    smap[old_pos] = [new_pos, new_pos]
                    new_pos += 1
            scene['flow'] = new_flow
            emap[si] = smap
        hb = ep.get('hookBeat')
        if isinstance(hb, list) and len(hb) == 2:
            sh, bp = hb
            em = emap.get(sh, {})
            if bp in em:
                ep['hookBeat'] = [sh, em[bp][0]]
        mapping[e] = emap
    if outp:
        json.dump(doc, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    if mapp:
        json.dump(mapping, open(mapp, 'w', encoding='utf-8'), ensure_ascii=False)
    eps = {e.get('ep'): sum(len(s.get('flow', [])) for s in e.get('scenes', [])) for e in doc.get('episodes', [])}
    print(f"SPLIT_OK 拆句 {split_count} 句 | 集数 {len(eps)} | 总拍数 {sum(eps.values())}")
    remain = [(e.get('ep'), s.get('sceneId'), len(re.sub(r'\s+', '', b['line'])))
              for e in doc.get('episodes', []) for s in e.get('scenes', [])
              for b in s.get('flow', []) if 'line' in b and clean_len(b.get('line')) > MAX_CHARS]
    print("残留超长:", remain if remain else "无")

if __name__ == '__main__':
    main()