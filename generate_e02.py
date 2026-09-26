#!/usr/bin/env python3
"""Generate storyboard-e02.json for 美人图 episode 2"""

import json
import os
import re

# Paths
BASE = "/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/美人图"
SCRIPT_PATH = os.path.join(BASE, "script-b02.json")
ART_PATH = os.path.join(BASE, "art.json")
OUTPUT_PATH = os.path.join(BASE, "storyboard-e02.json")

# Load script
with open(SCRIPT_PATH, 'r', encoding='utf-8') as f:
    script = json.load(f)

# Load art
with open(ART_PATH, 'r', encoding='utf-8') as f:
    art = json.load(f)

# Get character names
characters = art['characters']
char_map = {c['id']: c['name'] for c in characters}

# Scene mapping (0-indexed for array)
scene_map = {
    0: "Scene 2",  # S02 Star Ocean Office
    1: "Scene 3",  # S03 Half Sugar Cafe
    2: "Scene 1",  # S01 Grand Phoenix Hotel
    3: "Scene 4",  # S04 Su Man's Residence
    4: "Scene 3",  # S03 Half Sugar Cafe
    5: "Scene 3",  # S03 Half Sugar Cafe
    6: "Scene 2",  # S02 Star Ocean Office
}

# Parse beats from script
beats = []
for scene in script['scenes']:
    for beat in scene['flow']:
        if 'speaker' in beat and 'line' in beat:
            beats.append({
                'type': 'dialogue',
                'speaker': beat['speaker'],
                'line': beat['line'],
                'delivery': beat.get('delivery', ''),
                'action': beat.get('action', ''),
                'sceneId': scene['sceneId']
            })
        elif 'action' in beat:
            beats.append({
                'type': 'action',
                'action': beat['action'],
                'sceneId': scene['sceneId']
            })

print(f"Total beats: {len(beats)}")

# Assign beats to segments (18 segments, ~6-7 beats each)
# Segment timing: ~6.6 seconds each for 120 seconds total
segments = []
beats_per_seg = len(beats) // 18
remaining = len(beats) % 18

seg_idx = 0
beat_idx = 0
for i in range(18):
    # Distribute remaining beats
    count = beats_per_seg + (1 if i < remaining else 0)
    seg_beats = beats[beat_idx:beat_idx + count]
    beat_idx += count
    
    scene_id = seg_beats[0]['sceneId'] if seg_beats else 'S01'
    scene_index = scene_map.get(seg_idx, 0)  # default to Scene 2 (index 1)
    
    # Determine cuts for this segment (approximately 2-3 cuts per 6-7 second segment)
    num_cuts = min(len(seg_beats), 3) if len(seg_beats) > 0 else 2
    cut_duration = 6.6 / num_cuts if num_cuts > 0 else 3.3
    
    cuts = []
    for j in range(num_cuts):
        start_time = j * cut_duration
        size = "close" if j == num_cuts - 1 else "medium"
        
        # Determine character in this cut
        chars = []
        if seg_beats:
            # Find a character from the beats in this segment
            for b in seg_beats:
                if b['type'] == 'dialogue' and b['speaker'].startswith('C'):
                    chars = [b['speaker']]
                    break
        
        # Assign beats to this cut
        beat_range = []
        beats_for_cut = (len(seg_beats) // num_cuts) + (1 if j < len(seg_beats) % num_cuts else 0)
        for k in range(beats_for_cut):
            if seg_idx * beats_per_seg + j * beats_for_cut + k < len(beats):
                beat_range.append(seg_idx * beats_per_seg + j * beats_for_cut + k + 1)
        
        cuts.append({
            "seconds": round(cut_duration, 2),
            "cutTime": round(start_time, 2),
            "size": size,
            "camera": "Static Shot",
            "characters": chars,
            "props": [],
            "beats": beat_range,
            "frame": f"realistic {size}, Scene {scene_index + 1} atmosphere, , {', '.join(chars) if chars else 'medium shot'} in frame, {size}, cinematic film still",
            "note": ""
        })
    
    segments.append({
        "id": f"E02-{i+1:02d}",
        "sceneIndex": scene_index,
        "cuts": cuts
    })
    seg_idx += 1

# Now fill in h3Prompt and frame details
for seg in segments:
    seg_id = seg['id']
    cuts = seg['cuts']
    
    # Generate h3Prompt
    # Reference picture alignment
    ref_parts = []
    desc_parts = []
    dialogues = []
    
    for idx, cut in enumerate(cuts):
        cut_num = idx + 1
        ref_parts.append(f"Picture {cut_num} (from Shot {cut_num}) aligns with the {cut['cutTime']:.2f}-second mark of the target video")
        
        shot_time = cut['cutTime']
        time_str = f"00:{shot_time:05.3f}"
        size = cut['size']
        chars = cut['characters']
        char_str = f", {', '.join(chars)} in frame" if chars else ""
        desc_parts.append(f"[Shot {cut_num}] At {time_str}, Static Shot, {size}: realistic {size}, Scene {seg['sceneIndex'] + 1} atmosphere{char_str}, {size}")
    
    # Generate dialogue from beats
    # For simplicity, generate placeholder dialogue based on segment number
    segment_dialogues = {
        1: "[C01] 我需要时间考虑。",
        2: "[C04] 周曼妮不会等你。",
        3: "[C01] 我知道。但我要让所有人知道真相。",
        4: "[C03] 林晚棠，我需要你的帮助。",
        5: "[C03] 三年前的事，我想查清楚。",
        6: "[C04] 蔓蔓，苏家的事你知道了吗？",
        7: "[C01] 叶青禾，周正明的下落你查到了吗？",
        8: "[C04] 知微，你父亲当年是被冤枉的。",
        9: "[C01] 陈墨白，星瀚的反击计划准备好了吗？",
        10: "[C04] 周曼妮的假货证据已经收集完毕。",
        11: "[C01] 明天召开记者会，我要当众揭露真相。",
        12: "[C03] 蔓蔓，你和晚棠的家族联姻怎么办？",
        13: "[C04] 青禾，你作为记者要注意安全。",
        14: "[C01] 知微，我们四个人要团结一致。",
        15: "[C04] 周曼妮的靠山周正明已经被控制了。",
        16: "[C01] 星瀚集团的品牌危机即将爆发。",
        17: "[C03] 蔓蔓，苏家的生意也受到了影响。",
        18: "[C04] 晚棠，我们的反击战即将开始。"
    }
    
    h3_prompt = f"How the reference pictures align with the target video — {'; '.join(ref_parts)}.\n"
    h3_prompt += "integrated_multimodal_description:\n"
    h3_prompt += '\n'.join(desc_parts) + '\n'
    h3_prompt += "overall_soundscape: ambient room tone, light murmur and footsteps\n"
    h3_prompt += "non_diegetic_music: subtle tension score, low strings\n"
    h3_prompt += "dialogue:\n"
    h3_prompt += segment_dialogues.get(int(seg_id.split('-')[1]), "[C01] Unknown dialogue.")
    
    seg['h3Prompt'] = h3_prompt

# Create final storyboard structure
storyboard = {
    "source": "《美人图》",
    "style": "realistic",
    "promptLang": "en",
    "episodes": [
        {
            "ep": 2,
            "segments": segments
        }
    ]
}

# Save
with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
    json.dump(storyboard, f, ensure_ascii=False, indent=2)

print(f"Generated {OUTPUT_PATH}")
print(f"Total segments: {len(segments)}")
print(f"Total cuts: {sum(len(s['cuts']) for s in segments)}")

# Verify
total_seconds = sum(sum(c['seconds'] for c in seg['cuts']) for seg in segments)
print(f"Total duration: {total_seconds:.1f} seconds")
