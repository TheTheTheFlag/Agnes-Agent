# L6 知识图谱（GraphRAG）

> 在 L1-L5 之上的"关联记忆层"：把对话/检索中出现的实体关系沉淀成一张可查询的知识图谱，
> 支撑"跨会话/跨文档的实体关联问题"（如"上次提到的 HarmonyOS 5.0 支持哪家大模型？"）。

## 设计思想

| 维度 | 设计 |
|------|------|
| **引擎** | [LightRAG](https://github.com/HKUDS/LightRAG)（`lightrag-hku>=1.5.7`），实体提取 → 知识图谱 + 向量索引双路召回 |
| **桥接** | `app/memory/ligraphrag_adapter.py` 把项目现有的 LLM / Embedding / Rerank 网关包装成 LightRAG 需要的角色 |
| **存储** | `lightrag_storage/<thread_id>/`，每会话独立索引（GraphML + JsonKV + NanoVectorDB），进程重启不丢 |
| **召回** | 向量 + 图谱双路（`mode="hybrid"`）+ SiliconFlow Rerank 重排 |
| **隔离** | 按 thread 分桶，不同会话知识不串扰；实例按需懒加载并常驻 worker 线程 |

## 数据流

```text
用户消息 / tavily 搜索结果 / record_graph 三元组
        │
        ▼
lightrag_insert(thread_id, text)  ──►  实体提取 + 关系抽取（LLM）
        │                                  │
        ▼                                  ▼
  每轮 system prompt           graph_chunk_entity_relation.graphml
  注入 get_l6_context()   ◄─┐    （NetworkX 节点/边）+ vdb_* 向量库
        │                 │
        ▼                 │
  lightrag_query(thread_id, query)  ── 混合检索 + Rerank ──┘
```

## 三个入口

| 入口 | 位置 | 说明 |
|------|------|------|
| `record_graph` 工具 | `app/memory/graph_rag_tool.py` | LLM 显式记录三元组 `[{head, rel, tail}]` |
| `lightgraph_query` 工具 | 同上 | LLM 主动在会话图谱里做混合检索 |
| 被动注入 | `get_l6_context()` → L6 提示词块 | 每轮自动用当前 query 检索，把命中摘要拼进 system prompt |

LLM 工具描述见 `graph_rag_tool.py` 底部的 `RECORD_GRAPH_DESC` / `LIGHTGRAPH_QUERY_DESC`。

## 关键实现细节

### 适配层（`ligraphrag_adapter.py`）

- **Embedding**：走独立 SiliconFlow 端点（`BAAI/bge-m3`，1024 维）。每批 16 条，
  `run_in_executor` 并发，最后 **`np.vstack` 成单个 ndarray** 返回
  （LightRAG 的 `EmbeddingFunc` 包装层要求 `func` 返回 `[n, dim]` 数组，不是 list）。
- **LLM**：项目主模型 `RotatingKeyChatOpenAI`（同步 invoke）被包成 async callable，
  供 LightRAG 的 extract/keyword/query 等角色使用。
- **Rerank**：`POST {base_url}/rerank`，body `{model, query, documents, top_n}`，
  返回 `{results: [{index, relevance_score}]}`；失败时降级为全 0 分。
- **worker 单事件循环**：LightRAG 1.5.7 的 `initialize_storages()` 会把 shared-storage 锁绑定到
  创建它的 event loop。项目是同步上下文（LangGraph 节点 + FastAPI handler），
  因此所有 `ainsert/aquery/initialize_storages` 都提交到一条专属 worker 线程的常驻 loop 执行
  （`_run_on_worker` 用 `run_coroutine_threadsafe`），避免跨循环锁报错。

### 图读取（`graph_snapshot`）

从 `rag.chunk_entity_relation_graph._graph`（NetworkX）导出：

```python
{"nodes": [{id, name, kind, attrs}], "edges": [{id, from_id, to_id, label, attrs}]}
```

供 `GET /api/graph/nodes` / `GET /api/graph/edges` 使用，前端"图谱" tab 用 vis-network 渲染。

### 前端

- 调试面板新增"图谱"tab（`app.js`：`renderGraphTab` + `DRAWER_TABS`）。
- `index.html` 引入 `vis-network.min.js`；节点按 `kind` 着色，可拖动/缩放/hover 查看类型。

## 配置

| 变量 | 用途 | 来源 |
|------|------|------|
| `EMBEDDING_BASE_URL/API_KEY/MODEL` | 嵌入（默认 `BAAI/bge-m3`） | `.env` > `data/.model_config` |
| `RERANK_BASE_URL/API_KEY/MODEL` | 重排（默认 `BAAI/bge-reranker-v2-m3`） | 同上 |

LLM 角色直接复用项目当前主模型（无需额外配置）。

## 与 L1-L5 的区别

| 层级 | 用途 | 是否注入 system prompt |
|------|------|---------------------|
| L1 Thread | 当前对话消息 | ❌（已在 messages 字段） |
| L2 Profile | 用户画像/偏好 | ✅ 每轮自动 |
| L3 Episodic | 任务历史（dag_plans） | ❌（靠工具查询） |
| L4 Procedural | 命令历史 | ❌（靠工具查询） |
| L5 Semantic | 外部知识缓存（tavily 结果等） | ❌（靠工具查询） |
| **L6 Graph** | **实体关系知识图谱** | **✅ 自动检索注入** |

## 已知限制

| 限制 | 影响 | 改进方向 |
|------|------|---------|
| 每会话独立索引 | 共享知识需重提 | 跨 thread 合并查询 / 全局层 |
| 实体提取走主模型 | 长文本慢、token 成本高 | 用专用小模型 / 缓存抽取结果 |
| 被动注入 top_k=6 | 图谱小时召回有限 | 调节 top_k / 提高抽取触发频率 |
| `.env` 与 model_config 双来源 | 配置分散 | 统一配置中心 |

## 回归测试

`tests/test_graph_rag.py`（全 mock、不触网）：三元组解析 / record_graph 降级 /
lightgraph_query 命中约定 / L6 注入格式 / API 端点数据结构与空图降级。