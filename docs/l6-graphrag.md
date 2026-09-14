# L6 知识图谱（GraphRAG）

> 在 L1-L5 之上的"关联记忆层"：把对话/检索中出现的实体关系沉淀成一张可查询的知识图谱，
> 支撑"跨会话/跨文档的实体关联问题"（如"上次提到的 HarmonyOS 5.0 支持哪家大模型？"）。

## 设计思想

| 维度 | 设计 |
|------|------|
| **引擎** | [LightRAG](https://github.com/HKUDS/LightRAG)（`lightrag-hku>=1.5.7`），实体提取 → 知识图谱 + 向量索引双路召回 |
| **桥接** | `app/memory/ligraphrag_adapter.py` 把项目现有的 LLM / Embedding / Rerank 网关包装成 LightRAG 需要的角色 |
| **存储** | 图存储后端可选：`neo4j`（默认，项目内本地 Neo4j 5.26，Bolt 7687）或 `networkx`（本地 GraphML）＋ `lightrag_storage/<thread_id>/`（KV/向量仍在本地，每会话独立，重启不丢） |
| **召回** | 向量 + 图谱双路（`mode="hybrid"`）+ SiliconFlow Rerank 重排 |
| **隔离** | 按 thread 分桶（workdir + 空间 label），不同会话知识不串扰；实例按需懒加载并常驻 worker 线程 |

## 数据流

```text
用户消息 / 助手回答（全量喂养，后台线程）
tavily 搜索结果 / record_graph 三元组
        │
        ▼
lightrag_insert(thread_id, text)  ──►  实体提取 + 关系抽取（LLM）
        │                                  │
        ▼                                  ▼
每轮 system prompt           graph: Neo4j(默认) 或 NetworkX(graphml)
   注入 get_l6_context()   ◄─┐    （节点 label=thread_id）+ vdb_* 向量库
        │                 │
        ▼                 │
  lightrag_query(thread_id, query)  ── 混合检索 + Rerank ──┘
```

## 三个入口

| 入口 | 位置 | 说明 |
|------|------|------|
| 全量喂养 | `builder.py` chatbot 节点（`_feed_turn_async`） | 每个回合自动把 用户消息+助手回答 异步喂进 LightRAG，图谱随对话自然增长 |
| `record_graph` 工具 | `app/tools/graph_rag_tools.py` | LLM 显式记录三元组 `[{head, rel, tail}]` |
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

- **Neo4j 后端**（默认）：走 `Neo4JStorage.get_all_nodes()/get_all_edges()` 的 Cypher 全图查询
  （worker loop 上 async 读取，`_neo4j_snapshot`），边按无向去重，最多返回 500 节点 / 1000 边。
- **NetworkX 后端**：从 `rag.chunk_entity_relation_graph._graph`（NetworkX）导出。

```python
{"nodes": [{id, name, kind, attrs}], "edges": [{id, from_id, to_id, label, attrs}]}
```

供 `GET /api/graph/nodes` / `GET /api/graph/edges` 使用，前端"图谱" tab 用 vis-network 渲染。

### Neo4j 本地后端（安装与运维）

- 路径：`<repo_root>/neo4j/neo4j-community-5.26.30/`；JDK（Temurin 21）在 `<repo_root>/.runtime/jdk-21.0.12.1+1/`。
  两者均已 gitignore，不入仓。
- 安装（无管理员、网络受限环境）：Neo4j zip 来自 fossies.org 镜像，JDK 来自清华 TUNA Adoptium 镜像；
  下载后校验 SHA-256 与官方部署中心一致。
- 初始密码：`neo4j-admin dbms set-initial-password <pwd>`（仅首次启动前设置有效）。
- 启动：双击 `neo4j\start-neo4j.cmd`（或手动 `set JAVA_HOME=...` 后 `neo4j.bat console`）；
  停止：Ctrl+C。默认地址 `bolt://localhost:7687`、`http://localhost:7474`。
- 后端切换：`.env` 里 `GRAPH_STORAGE=networkx`（本地文件，免装 Java）或 `GRAPH_STORAGE=neo4j`。
  切 `neo4j` 时也必须配齐 `NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD`（缺任一则回退 NetworkX）。
- 隔离：LightRAG 的 `workspace` 显式传 `thread_id`，Neo4j 按节点 label（=thread_id）隔离；
  KV/向量/分块仍在 `lightrag_storage/<thread_id>/`。

### 前端

- 调试面板新增"图谱"tab（`app.js`：`renderGraphTab` + `DRAWER_TABS`）。
- `index.html` 引入 `vis-network.min.js`；节点按 `kind` 着色，可拖动/缩放/hover 查看类型。

## 配置

| 变量 | 用途 | 来源 |
|------|------|------|
| `EMBEDDING_BASE_URL/API_KEY/MODEL` | 嵌入（默认 `BAAI/bge-m3`） | `.env` > `data/.model_config` |
| `RERANK_BASE_URL/API_KEY/MODEL` | 重排（默认 `BAAI/bge-reranker-v2-m3`） | 同上 |
| `GRAPH_STORAGE` | 图后端：`neo4j`（默认）\|`networkx` | `.env` |
| `NEO4J_URI/USERNAME/PASSWORD/DATABASE` | Neo4j 连接（`NEO4J_DATABASE=neo4j` 默认库） | `.env` |

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
| Neo4j 未启动时建图失败 | 该会话图为空/记录降级 | 启动 `start-neo4j.cmd`；或临时 `GRAPH_STORAGE=networkx` |
| 每会话独立索引 | 共享知识需重提 | 跨 thread 合并查询 / 全局层 |
| 实体提取走主模型 | 长文本慢、token 成本高 | 用专用小模型 / 缓存抽取结果 |
| 被动注入 top_k=6 | 图谱小时召回有限 | 调节 top_k / 提高抽取触发频率 |
| `.env` 与 model_config 双来源 | 配置分散 | 统一配置中心 |

## 回归测试

`tests/test_graph_rag.py`（全 mock、不触网）：三元组解析 / record_graph 降级 /
lightgraph_query 命中约定 / L6 注入格式 / API 端点数据结构与空图降级。