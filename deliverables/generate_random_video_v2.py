#!/usr/bin/env python3
import sys
sys.path.insert(0, 'app/skills/agnes-media')
from video_generator import AgnesVideoGenerator
import os
import requests
import json
import random
from datetime import datetime

generator = AgnesVideoGenerator()

# 随机视频提示词
prompts = [
    "A serene mountain lake at sunrise, mist rising from the water, reflection of golden peaks, cinematic slow pan, photorealistic",
    "A bustling Tokyo street at night, neon lights reflecting on wet pavement, people walking with umbrellas, cyberpunk atmosphere, 4K cinematic",
    "A gentle ocean wave crashing on a tropical beach, turquoise water, white foam, golden sunset lighting, slow motion, drone aerial view",
    "A snowy forest in winter, soft snow falling gently between pine trees, moonlight filtering through branches, magical winter wonderland",
    "A city skyline transitioning from day to night, time-lapse, clouds moving fast, stars appearing, urban landscape, cinematic quality"
]

selected_prompt = random.choice(prompts)
print(f'Selected prompt: {selected_prompt}')

# 创建视频任务 - seconds 必须是字符串
task_result = generator.create(
    prompt=selected_prompt,
    model='agnes-video-2.5-flash',
    ratio='16:9',
    duration='5',  # 字符串格式
    seconds=5      # 整数会被转换，但 API 需要字符串
)

video_id = task_result['video_id']
print(f'Video created: {video_id}')
print(f'Status: {task_result["status"]}')
print(f'Duration: {task_result["seconds"]} seconds')

# 轮询等待完成
print('Waiting for video generation... (this may take 30-120 seconds)')
result = generator.poll_result(video_id)

print(f'Video completed! URL: {result["url"]}')

# 创建输出目录
today = datetime.now().strftime('%Y%m%d')
output_dir = f'app/skills/agnes-media/output/{today}'
os.makedirs(output_dir, exist_ok=True)

# 下载视频
video_url = result['url']
print(f'Downloading video from: {video_url}')
video_content = requests.get(video_url, timeout=120).content

# 保存视频
video_filename = f'{output_dir}/video_{datetime.now().strftime("%H%M%S")}.mp4'
with open(video_filename, 'wb') as f:
    f.write(video_content)

print(f'Video saved to: {video_filename}')

# 写入日志
log_file = f'{output_dir}/video_log.md'
log_entry = f'''## {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

**Prompt**: {selected_prompt}

**URL**: {video_url}

**File**: {video_filename}

---
'''

with open(log_file, 'a', encoding='utf-8') as f:
    f.write(log_entry)

print('Log written successfully!')
print('\n=== Video Generation Complete ===')
print(f'\n视频已保存到: {video_filename}')
print(f'视频 URL: {video_url}')
