#!/usr/bin/env python3
"""Generate artistic image - woman in rain at night, artistic silhouette."""
import json
import requests
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path("app/skills/agnes-media")
OUTPUT_DIR = SKILL_DIR / "output" / datetime.now().strftime("%Y%m%d")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load API key
with open(SKILL_DIR / "keys.json") as f:
    api_key = json.load(f)["agnes"]

prompt = """A cinematic photograph of a woman standing in dark water under heavy rain at night. She is wearing a flowing translucent white dress that clings to her form. Her head tilted back and eyes closed as raindrops cascade over her face. Long wet hair flowing down her back. Warm backlight creates dramatic silhouette against the black void. The lighting highlights her silhouette and the raindrops on her dress like liquid glass. Expression serene and immersed in the moment. Shadowy reflections shimmering on the water's surface. Cinematic contrast between light and darkness. Shot with shallow depth of field, bokeh softens distant ripples. Photorealistic, high-resolution, dramatic chiaroscuro, editorial glamour aesthetic."""

url = "https://api.agnes-ai.cn/v1/images/generations"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}
payload = {
    "model": "agnes-image-2.5-flash",
    "prompt": prompt,
    "size": "2K",
    "ratio": "3:4",
    "extra_body": {"response_format": "url"}
}

print("Sending request...")
resp = requests.post(url, headers=headers, json=payload, timeout=180)
print(f"Status: {resp.status_code}")
if resp.status_code != 200:
    print(f"Response: {resp.text[:1000]}")
    exit(1)
data = resp.json()
img_url = data["data"][0]["url"]
print(f"Image URL: {img_url}")

# Download image
img_data = requests.get(img_url).content
img_path = OUTPUT_DIR / "img_0001.png"
with open(img_path, "wb") as f:
    f.write(img_data)
print(f"Saved to: {img_path}")

# Write log
log_path = OUTPUT_DIR / "log.md"
log_entry = f"""## {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

**Prompt**: {prompt}

**URL**: {img_url}

**File**: {img_path}

---
"""
with open(log_path, "a", encoding="utf-8") as f:
    f.write(log_entry)
print("Log written.")
