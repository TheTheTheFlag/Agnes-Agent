---
name: amap-commute
description: Use when querying Amap route distance and time for commute planning
---

# Amap Commute Route Query

## Overview
Use Amap (高德地图) API v5/direction/driving to query route distance and estimated travel time.

## Steps

1. Geocode origin (杭州市临安区宝龙广场):
```python
import urllib.request, json
from urllib.parse import urlencode
key = "48d4aaacc5ed4f273bced906ca9c67aa"
params = urlencode({"key": key, "address": "杭州市临安区宝龙广场", "output": "json"})
url = f"https://restapi.amap.com/v3/geocode/geo?{params}"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode())
origin_loc = data["geocodes"][0]["location"]
```

2. Geocode destination (杭州市西湖区三墩镇西园七路八号):
```python
params = urlencode({"key": key, "address": "杭州市西湖区三墩镇西园七路八号", "output": "json"})
url = f"https://restapi.amap.com/v3/geocode/geo?{params}"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode())
dest_loc = data["geocodes"][0]["location"]
```

3. Query driving route (v5 with show_fields=cost):
```python
params = urlencode({"key": key, "origin": origin_loc, "destination": dest_loc, "mode": "driving", "show_fields": "cost"})
url = f"https://restapi.amap.com/v5/direction/driving?{params}"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode())
paths = data["route"]["paths"]
for i, p in enumerate(paths):
    dist = int(p.get("distance", 0))
    cost_obj = p.get("cost", {})
    dur = int(cost_obj.get("duration", 0))
    print(f"Route {i+1}: {dist}m ({dist/1000:.1f}km), {dur}s ({dur/60:.1f}min)")
```

Note: show_fields must be "cost" (not "duration"). Duration is at paths[i].cost.duration, not route.cost.duration.

## Output Format
- Distance in km for each route option
- Estimated duration in minutes
- Key roads used (from steps)
- Note if morning rush hour (7am) may add 10-15min
