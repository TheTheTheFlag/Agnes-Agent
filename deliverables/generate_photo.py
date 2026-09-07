#!/usr/bin/env python3
"""Generate AI image from description prompt."""
import base64
import io
import json
import os
import requests
from pathlib import Path

# API key from agnes-media keys.json
API_KEY = "sk-TYkTnDSMTupsF32belYJZZwq9FEKgDBcu2r7yYBcTcyRWdAW"
BASE_URL = "https://api.agnes-ai.cn/v1"
MODEL = "agnes-image-2.5-flash"
SIZE = "2K"
RATIO = "4:5"

# Output directory
OUTPUT_DIR = Path("app/skills/agnes-media/output")
TODAY = Path.cwd().strftime("%Y%m%d")
OUTPUT_DIR_TODAY = OUTPUT_DIR / TODAY
OUTPUT_DIR_TODAY.mkdir(parents=True, exist_ok=True)

PROMPT = """A young Asian woman with long black straight hair, standing by the ocean on a sunny day, wind blowing through her hair and clothes. She is wearing a light blue button-down shirt (unbuttoned at top) over a black tank top. She smiles warmly at the camera with a subtle natural half-smile, one hand making a finger-heart gesture in front of her chest. A gold ring with texture detail on her ring finger. Cinematic side lighting, warm golden rim light, soft dreamy bokeh, photorealistic, high quality, 8k."""

def generate_image(prompt, model, size, ratio):
    url = f"{BASE_URL}/images/generations"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "ratio": ratio,
        "extra_body": {"response_format": "url"}
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    img_url = data["data"][0]["url"]
    return img_url

def save_image(img_url, save_path):
    resp = requests.get(img_url, timeout=60)
    resp.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(resp.content)
    return save_path

def write_log(prompt, img_url, save_path):
    log_file = OUTPUT_DIR_TODAY / "log.md"
    timestamp = Path.cwd().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"""## {timestamp}

**Prompt**: {prompt}

**URL**: {img_url}

**File**: {save_path}

---
"""
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_entry)

if __name__ == "__main__":
    print("Generating image...")
    img_url = generate_image(PROMPT, MODEL, SIZE, RATIO)
    print(f"Image URL: {img_url}")
    
    # Save to output directory
    save_filename = f"img_{Path(OUTPUT_DIR_TODAY).glob('*.png')}"
    idx = len(list(OUTPUT_DIR_TODAY.glob("img_*.png"))) + 1
    save_path = OUTPUT_DIR_TODAY / f"img_{idx:04d}.png"
    save_image(img_url, save_path)
    print(f"Saved to: {save_path}")
    
    # Write log
    write_log(PROMPT, img_url, str(save_path))
    print(f"Log written to: {OUTPUT_DIR_TODAY / 'log.md'}")
    print("Done!")
