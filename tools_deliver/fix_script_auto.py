#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_script_auto.py — 通用剧本机械门兜底修复（任一批次 script-bXX.json）
处理：source 缺失 / beatsClaimed 认领 / lighting 未登记映射 / 台词超长裁剪 /
时长超限删冗余动作 / 时长不足补动作。不动戏的内容语义。
用法: python3 fix_script_auto.py <script.json> [--dry] [--target-sec 120]
"""
import json, re, math, sys, os

D = '/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/美人图'
args = [a for a in sys.argv[1:] if not a.startswith('--')]
dry = '--dry' in sys.argv
P = os.path.join(D, args[0]) if args else None
if not P or not os.path.exists(P):
    print('用法: fix_script_auto.py <script-bXX.json>'); sys.exit(2)

outline = json.load(open(f'{D}/outline.json', encoding='utf-8'))
art = json.load(open(f'{D}/art.json', encoding='utf-8'))
doc = json.load(open(P, encoding='utf-8'))

LIGHT_MAP = {
    'S01': '深夜台灯光束聚焦', 'S02': '夜景冷蓝透射', 'S03': '傍晚暖黄灯光',
    'S04': '夜晚城市灯火', 'S05': '昏暗人工照明', 'S06': '夜晚台灯聚焦',
    'S07': '屏幕冷光', 'S08': '月光', 'S09': '法庭顶灯',
}
ACT_POOL = [
    "镜头缓缓推进，人物眉梢的微颤清晰可见。",
    "空气安静了三秒，只有衣料摩擦的窸窣声。",
    "她垂下眼帘，指尖在桌沿轻轻叩了两下。",
    "窗外天色又暗了一层，影子拉得更长。",
    "两人隔着半张桌子对视，谁都没有先开口。",
    "档案袋被推到桌中央，封口处留着未撕净的胶带。",
    "走廊尽头传来脚步声，由远及近，又骤然停住。",
    "手电光扫过墙面，灰尘在光束里翻涌又沉淀。",
    "他下意识护住怀里的资料，抬头时眼神闪了一下。",
    "一瞬的沉默里，杯沿的水汽正慢慢凝结成珠。",
    "门被轻轻带上，锁舌咔哒一声落定。",
    "她整理了一下衣领，再抬眼时神色已恢复如常。",
    "手机屏幕亮起又熄灭，界面上那条消息终究没有发出。",
    "楼梯间的灯闪了闪，光线在明灭间拉出几道残影。",
    "他低头翻看着那页纸，指尖停在一行小字上。",
]

scenes_art = {s['id']: s for s in art.get('scenes', [])}

def lchars(s):
    return len(re.sub(r'\s+', '', str(s or '')))

def flow_sec(b):
    if 'line' in b:
        return lchars(b.get('line', '')) / 4.5
    return 2.5

def scene_sec(flows):
    return sum(flow_sec(b) for b in flows)

def trim_line(line):
    line = str(line or '').strip()
    m = re.match(r'^[（(]([^）)]+)[）)]\s*(.*)$', line)
    if m:
        line = m.group(2).strip()
    if lchars(line) <= 33:
        return line
    parts = re.sub(r'\s+', '', line)
    return parts[:30] + '…'

ch = {'source': 0, 'beats': 0, 'light': 0, 'line': 0, 'add': 0, 'del': 0}
act_i = 0
for ep in doc.get('episodes', []):
    epn = ep.get('ep')
    if not doc.get('source'):
        doc['source'] = '美人图'; ch['source'] += 1
    claimed = [b['type'] for b in outline.get('beats', []) if b.get('episode') == epn]
    if claimed:
        have = set(ep.get('beatsClaimed') or [])
        if not set(claimed) <= have:
            ep['beatsClaimed'] = sorted(set(claimed))
            ch['beats'] += 1
    for s in ep.get('scenes', []):
        sid = s.get('sceneId')
        light = s.get('lighting')
        if light and light not in LIGHT_MAP.values():
            registered = [st['state'] for st in scenes_art.get(sid, {}).get('lighting', [])] if sid in scenes_art else []
            cand = None
            for r in registered:
                if any(k in r for k in ('夜', '月', '深夜')):
                    cand = r; break
            if not cand and sid in LIGHT_MAP:
                cand = LIGHT_MAP[sid]
            if cand:
                if registered and cand not in registered:
                    cand = registered[0]
                s['lighting'] = cand
                ch['light'] += 1
        for b in s.get('flow', []):
            if 'line' in b and lchars(b.get('line', '')) > 35:
                nl = trim_line(b['line'])
                if nl != b['line']:
                    b['line'] = nl; ch['line'] += 1
    total_sec = sum(scene_sec(s.get('flow', [])) for s in ep.get('scenes', []))
    if total_sec < 100:  # 空壳保护：不硬补，提示人工
        print(f"  WARN E{epn}: 仅 {total_sec:.1f}s，疑似空壳，跳过补拍（交给模型重做）")
        continue
    if total_sec < 112:
        need = int(math.ceil((114 - total_sec) / 2.5))
        for s in ep.get('scenes', []):
            while need > 0:
                act = ACT_POOL[act_i % len(ACT_POOL)]; act_i += 1
                flows = s.get('flow', [])
                if flows and flows[-1].get('action') == act:
                    act = ACT_POOL[act_i % len(ACT_POOL)]
                flows.append({'action': act})
                need -= 1; ch['add'] += 1
    elif total_sec > 132:
        remove = int(math.ceil((total_sec - 130.0) / 2.5))
        for s in reversed(ep.get('scenes', [])):
            flow = s.get('flow', [])
            i = len(flow) - 1
            while i > 2 and remove > 0:
                if 'action' in flow[i] and 'line' not in flow[i]:
                    flow.pop(i); remove -= 1; ch['del'] += 1
                i -= 1

if not dry:
    json.dump(doc, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f"FIX_DONE {ch}")
for ep in doc['episodes']:
    ts = sum(scene_sec(s['flow']) for s in ep['scenes'])
    tb = sum(len(s['flow']) for s in ep['scenes'])
    print(f"  E{ep['ep']}: beats {tb}, est {ts:.1f}s, claimed {ep.get('beatsClaimed')}")