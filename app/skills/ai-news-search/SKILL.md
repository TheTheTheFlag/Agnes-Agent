---
name: ai-news-search
description: Use when searching for recent AI industry news via Tavily API
---

# AI News Search

## Overview
Search Tavily API for recent AI news and return structured results.

## Steps

1. Search today's AI news:
```python
import os, urllib.request, json
key = os.environ["TAVILY_API_KEY"]   # 从项目 .env 读取，不要硬编码 key（若缺失先询问用户配置）
url = "https://api.tavily.com/search"
payload = {"api_key": key, "query": "AI artificial intelligence latest news", "max_results": 5, "search_depth": "advanced"}
data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as resp:
    result = json.loads(resp.read().decode())
articles = result.get("results", [])
```

2. Search 2 days ago:
```python
payload["query"] = "AI news 2 days ago"
# same request pattern
```

## Output Format
- Title, URL, brief summary for each article
- Group by recency (today vs older)
- Top 5 most relevant per query
