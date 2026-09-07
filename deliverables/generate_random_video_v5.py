#!/usr/bin/env python3
import sys
sys.path.insert(0, 'app/skills/agnes-media')
from video_generator import AgnesVideoGenerator
import os
import requests
import json
import random
import time
from datetime import datetime

print('等待 API 限流恢复... (约 60 秒)')
time.sleep(60)

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
url = f'{generator.base_url}/videos'
headers = {
    "Authorization": f"Bearer {generator.api_key}",
    "Content-Type": "application/json",
}

payload = {
    "model": "agnes-video-2.5-flash",
    "prompt": selected_prompt,
    "size": "720P",
    "seconds": "5",  # 字符串格式
    "ratio": "16:9"
}

print('Creating video task...')
try:
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    print(f'Response status: {response.status_code}')
    
    if response.status_code == 200:
        data = response.json()
        video_id = data['video_id']
        print(f'Video created: {video_id}')
        print(f'Status: {data.get("status")}')
        print(f'Duration: {data.get("seconds")} seconds')
        
        # 轮询等待完成
        print('Waiting for video generation... (this may take 30-120 seconds)')
        query_url = "https://api.agnes-ai.cn/agnesapi"
        
        while True:
            poll_response = requests.get(
                query_url,
                params={"video_id": video_id},
                headers={"Authorization": f"Bearer {generator.api_key}"},
                timeout=30
            )
            poll_data = poll_response.json()
            status = poll_data.get("status")
            
            print(f'Current status: {status}')
            
            if status == "completed":
                result_url = poll_data.get("url") or poll_data.get("remixed_from_video_id")
                print(f'Video completed! URL: {result_url}')
                break
            elif status == "failed":
                print(f'Video failed: {poll_data.get("error")}')
                break
            else:
                time.sleep(8)  # 避免 429
        
        # 创建输出目录
        today = datetime.now().strftime('%Y%m%d')
        output_dir = f'app/skills/agnes-media/output/{today}'
        os.makedirs(output_dir, exist_ok=True)
        
        # 下载视频
        if result_url:
            print(f'Downloading video from: {result_url}')
            video_content = requests.get(result_url, timeout=120).content
            
            # 保存视频
            video_filename = f'{output_dir}/video_{datetime.now().strftime("%H%M%S")}.mp4'
            with open(video_filename, 'wb') as f:
                f.write(video_content)
            
            print(f'Video saved to: {video_filename}')
            
            # 写入日志
            log_file = f'{output_dir}/video_log.md'
            log_entry = f'''## {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

**Prompt**: {selected_prompt}

**URL**: {result_url}

**File**: {video_filename}

---
'''
            
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
            
            print('Log written successfully!')
            print('\n=== Video Generation Complete ===')
            print(f'\n视频已保存到: {video_filename}')
            print(f'视频 URL: {result_url}')
        else:
            print('No video URL returned')
            
    else:
        print(f'Failed to create video task')
        print(f'Response: {response.text}')
        
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
