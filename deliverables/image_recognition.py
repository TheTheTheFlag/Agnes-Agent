import base64
import json
import urllib.request

# Read image and convert to base64
with open('C:/Users/17625/Agnes-Agent/uploads/up_5a3eeb107c68.jpg', 'rb') as f:
    image_data = base64.b64encode(f.read()).decode('utf-8')

# Prepare request
request_body = json.dumps({
    "model": "qwen3.7-plus",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_data}"
                    }
                },
                {
                    "type": "text",
                    "text": "请详细描述这张图片的内容，包括场景、人物、文字等所有可见信息。"
                }
            ]
        }
    ]
}).encode('utf-8')

# Send request
req = urllib.request.Request(
    'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    data=request_body,
    headers={
        'Authorization': 'Bearer sk-85a17acfc43c4392acc894655156b73e',
        'Content-Type': 'application/json'
    }
)

response = urllib.request.urlopen(req)
result = json.loads(response.read().decode('utf-8'))
print(json.dumps(result, ensure_ascii=False, indent=2))
