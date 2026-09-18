---
name: amap-weather
description: Use when querying weather forecast for Hangzhou via Amap API
---

# Amap Weather Query

## Overview
Use Amap (高德地图) v3/weather/weatherInfo API to query weather forecast.

## Steps

```python
import urllib.request, json
from urllib.parse import urlencode

key = json.load(open("app/skills/amap-weather/keys.json"))["amap"]   # 读技能目录 keys.json，不要硬编码
# Hangzhou adcode: 330100
params = urlencode({"key": key, "city": "330100", "extensions": "all"})
url = f"https://restapi.amap.com/v3/weather/weatherInfo?{params}"
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read().decode())

forecasts = data.get("forecasts", [{}])[0]
city = forecasts.get("city")
casts = forecasts.get("casts", [])
for c in casts[:2]:  # Today and tomorrow
    print(f"{c['date']} ({c['week']}): {c['dayweather']}/{c['nightweather']}")
    print(f"  Temp: {c['daytemp']}°C / {c['nighttemp']}°C")
    print(f"  Wind: {c['daywind']} {c['daypower']}级")
```

## Output Format
- Today's weather: day/night conditions, temperature range, wind
- Tomorrow's forecast: same format
- Add commute advice if raining
