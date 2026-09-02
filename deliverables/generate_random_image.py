#!/usr/bin/env python3
"""Generate a random image using Agnes AI API."""

import json
import os
import random
import requests
from datetime import datetime

# 读取 API key
skill_dir = 'app/skills/agnes-media'
keys_file = os.path.join(skill_dir, 'keys.json')
with open(keys_file) as f:
    api_key = json.load(f)['agnes']

# 随机提示词
prompts = [
    'A mystical forest with bioluminescent flowers and fireflies, magical atmosphere, fantasy art style, detailed lighting',
    'An astronaut floating in space surrounded by colorful nebula, cinematic composition, photorealistic',
    'A cozy Japanese café on a rainy day, warm lighting, steam rising from coffee, lo-fi aesthetic',
    'A majestic dragon perched on a cliff overlooking an ancient castle, epic fantasy scene, dramatic clouds',
    'Underwater coral reef city with merfolk, vibrant colors, sun rays piercing through water, magical realism',
    'A steampunk airship flying through golden clouds at sunset, intricate mechanical details, vintage illustration style',
    'Northern lights dancing over a frozen lake with snow-covered pine trees, serene winter landscape, photorealistic',
    'A whimsical tea party hosted by cats in an enchanted garden, detailed illustration, soft pastel colors',
    'Cyberpunk city street at night with neon signs reflecting on wet pavement, futuristic architecture, rain atmosphere',
    'A phoenix rising from ashes with golden flames, mythological art, dramatic lighting, detailed feathers',
    'An ancient library with floating books and magical glow, scholarly atmosphere, detailed interior',
    'A crystal cave with rainbow light refractions, sparkling minerals, ethereal beauty',
    'A floating island with waterfalls cascading into clouds, pastoral fantasy, Ghibli-inspired',
    'A samurai standing in a field of cherry blossoms, wind blowing petals, cinematic composition',
    'A mysterious portal in an old stone doorway, swirling magic energy, dark fantasy'
]
random.seed()
prompt = prompts[random.randint(0, len(prompts)-1)]

# 生成图片
url = 'https://api.agnes-ai.cn/v1/images/generations'
headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
payload = {
    'model': 'agnes-image-2.1-flash',
    'prompt': prompt,
    'size': '1K',
    'ratio': '16:9',
    'extra_body': {'response_format': 'url'}
}
resp = requests.post(url, headers=headers, json=payload, timeout=60)
resp.raise_for_status()
data = resp.json()
img_url = data['data'][0]['url']

# 保存到 output 目录
today = datetime.now().strftime('%Y%m%d')
output_dir = os.path.join(skill_dir, 'output', today)
os.makedirs(output_dir, exist_ok=True)
img_path = os.path.join(output_dir, 'img_0001.png')
img_content = requests.get(img_url).content
with open(img_path, 'wb') as f:
    f.write(img_content)

# 写入日志
log_entry = f'''## {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

**Prompt**: {prompt}

**URL**: {img_url}

**File**: {img_path}

---
'''
with open(os.path.join(output_dir, 'log.md'), 'a', encoding='utf-8') as f:
    f.write(log_entry)

print(f'成功！图片已保存至: {img_path}')
print(f'预览 URL: {img_url}')
