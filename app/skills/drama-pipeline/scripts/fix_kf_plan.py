import json, os
R = "/home/mirror/Agnes-Agent/data/users/Mirror/deliverables/短剧/花语战歌"
KFP = os.path.join(R, "投喂稿", "kf_plan.json")
d = json.load(open(KFP, encoding="utf-8"))
# d is a list of episode dicts
all_segs = []
for ep in d:
    all_segs.extend(ep.get("segments", []))
changed = 0
for s in all_segs:
    old_refs = list(s["refs"])
    new_refs = [r for r in old_refs if "/assets/" not in r]
    s["refs"] = new_refs
    if old_refs != new_refs:
        changed += 1
        print(f"{s['seg']}: {len(old_refs)} refs -> {len(new_refs)} refs")
with open(KFP, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print(f"共修改 {changed} 段")
