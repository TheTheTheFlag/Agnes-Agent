# L5 语义记忆（Semantic Cache）

> 在 L1-L4 之外的"知识缓存层"，用于减少重复调用外部工具（tavily_search、文件读取等）。

## 数据源：`semantic_cache` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `source` | TEXT | 缓存来源（tavily / document / web / manual 等） |
| `query` | TEXT | 触发缓存的查询关键词（精确匹配） |
| `content` | TEXT | 缓存的内容（搜索结果、文档等），默认截断 8000 字符 |
| `hit_count` | INTEGER | 命中次数（用于热度排序） |
| `last_accessed_at` | TIMESTAMP | 最后访问时间（按此字段排序） |
| `created_at` | TIMESTAMP | 创建时间 |
| `expires_at` | TIMESTAMP | 过期时间（默认 24h，可配置） |

索引：
- `idx_semantic_cache_query`（按 query 精确匹配）
- `idx_semantic_cache_source`（按 source 过滤）

## 核心方法（`app/memory/memory_manager.py`）

### 写入：`cache_knowledge(source, query, content, ttl_seconds=86400, max_len=8000)`

```python
mm.cache_knowledge(
    source="tavily",           # 来源标识
    query="Python 3.12 新特性", # 查询关键词
    content=search_result,     # 缓存内容
    ttl_seconds=86400,         # 24 小时后过期
)
```

- 相同 `query` 会覆盖旧缓存（`INSERT OR REPLACE`）
- 内容自动截断到 8000 字符，避免撑爆
- `ttl_seconds=None` 表示永不过期

### 读取：`search_knowledge(keyword, limit=5)`

```python
results = mm.search_knowledge("Python 3.12", limit=3)
# 返回按 hit_count + last_accessed_at 排序的列表
# 自动过滤过期项 + 命中后 hit_count + 1
```

返回结构：
```python
[{
    "source": "tavily",
    "query": "Python 3.12 新特性",
    "content": "...",
    "hit_count": 5,
    "last_accessed_at": "2026-09-10T10:30:00",
}, ...]
```

### 命中更新：`get_cached_knowledge(query)`

精确匹配 `query`，命中后：
- 自动 +1 `hit_count`
- 更新 `last_accessed_at`
- 返回缓存内容

### 清理过期：`cleanup_expired_cache()`

```python
deleted = mm.cleanup_expired_cache()
# 返回删除行数
```

定期任务（cron）会调用此方法清理过期项。

## 触发场景

| 场景 | 来源 | 触发点 |
|------|------|--------|
| tavily 网络搜索 | `source="tavily"` | `on_tool_after` 钩子，每次搜索后自动缓存 |
| 文档读取 | `source="document"` | 待实现 |
| 用户手动添加 | `source="manual"` | 工具或 API |

实际触发代码（`app/graph/builder.py:200`）：
```python
# 每次 tavily 搜索后自动缓存结果
if name == "tavily_search":
    query = params.get("query")
    mm.cache_knowledge(
        source="tavily",
        query=query,
        content=str(result)[:8000],
    )
```

## 工具集成

### `search_my_memory` 工具（用户可用）

```python
@tool
def search_my_memory(keyword: str, layers: str = "L3,L5", limit: int = 5):
    """在历史任务/对话/外部知识缓存中按关键词搜索"""
    if "L5" in layers.split(","):
        result["L5_knowledge"] = mm.search_knowledge(keyword, limit)
```

LLM 可主动调用此工具检索 L5 缓存，避免重复网络搜索。

## 特点

| 维度 | 设计 |
|------|------|
| **存储** | SQLite 单表，简单可靠 |
| **匹配** | 精确关键词匹配（query 字段） |
| **检索** | 模糊匹配（query OR content LIKE %keyword%） |
| **排序** | hit_count DESC + last_accessed_at DESC |
| **TTL** | 默认 24 小时，可配置 |
| **去重** | INSERT OR REPLACE（相同 query 覆盖） |
| **统计** | hit_count 自动累计，支持热度排序 |

## 与 L1-L4 的区别

| 层级 | 用途 | 是否注入 system prompt |
|------|------|---------------------|
| L1 Thread | 当前对话消息 | ❌（已在 messages 字段） |
| L2 Profile | 用户画像/偏好 | ✅ 每轮自动 |
| L3 Episodic | 任务历史（dag_plans） | ❌（靠工具查询） |
| L4 Procedural | 命令历史 | ❌（靠工具查询） |
| **L5 Semantic** | **外部知识缓存（tavily 搜索结果等）** | **❌（靠工具查询）** |

L5 的核心价值：**避免重复调用昂贵的外部 API**。
- 第一次 tavily 搜索 → 缓存到 L5
- 后续相同 query → 直接读 L5，不调外部 API
- 命中累计 `hit_count`，越热门的缓存越不容易过期

## 已知限制

| 限制 | 影响 | 改进方向 |
|------|------|---------|
| 仅精确匹配 `query` | 同义 query 不会命中 | 加 Embedding 做语义检索（向量库） |
| LIKE 模糊匹配 | 性能一般（SQLite 扫表） | 加 FTS5 全文检索 |
| 无过期更新策略 | 旧缓存不主动清理 | 加定期 cron 任务 |
| 无 PII 检测 | 可能缓存敏感信息 | 写入前正则过滤 |

## 配置项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ttl_seconds` | 86400（24h） | 缓存过期时间 |
| `max_len` | 8000 | 单条内容最大字符数 |
| `limit`（检索） | 5 | 返回条数上限 |
