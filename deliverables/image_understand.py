import base64
import requests
import json

# Read image
with open(r'C:\Users\17625\Agnes-Agent\uploads\up_fdee1d4ad54e.jpg', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

body = {
    "model": "qwen3.7-plus",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "min_pixels": 3072, "max_pixels": 8388608}},
            {"type": "text", "text": "请详细描述这张图片的内容，包括场景、物体、人物、文字等所有可见信息。用中文回答。"}
        ]
    }]
}

headers = {
    "Authorization": "Bearer sk-85a17acfc43c4392acc894655156b73e",
    "Content-Type": "application/json"
}

resp = requests.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", json=body, headers=headers)
result = resp.json()
print(result.get("choices", [{}])[0].get("message", {}).get("content", ""))
