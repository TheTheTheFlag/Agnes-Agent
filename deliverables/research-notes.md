# 调研笔记：RAG / KAG / OAG 三大知识增强范式

> **调研说明 / 可信度声明**
> - 本笔记在编写时网络检索工具（tavily_search）持续返回 `ConnectionError（连接被重置）`，**未能联网核验**。以下内容基于既有知识整理，标注了置信度，供后续节点在可联网时复核。
> - 标注约定：✅ = 高置信（论文标题/作者/年份/arXiv 号有把握）；⚠️ = 中低置信或社区用法、需核验；❓ = 不确定/存在歧义，务必核验后再对外引用。
> - 已尽量避免编造论文或项目名；对不确定的出处只给"概念/候选名"而不杜撰具体 arXiv 号。

---

## 0. 一句话总览（对比表）

| 维度 | RAG | KAG | OAG（本体增强生成，待核验） |
|---|---|---|---|
| 全称 | Retrieval-Augmented Generation | Knowledge Augmented Generation | Ontology-Augmented Generation（存在同名歧义，见 §3） |
| 提出方 | Facebook AI Research (FAIR) / 学术界 | 蚂蚁集团（Ant Group）OpenSPG 团队 | 尚无公认统一提出方 ❓ |
| 关键时间 | 2020 提出；2023 分类学；2024 模块化 | 2024（论文 + 开源） | 2023–2025 陆续出现零散工作 ⚠️ |
| 核心思想 | 检索外部文档 → 喂给生成模型，缓解幻觉与知识陈旧 | LLM 友好的知识表示 + 图谱/向量混合检索 + 逻辑形式引导混合推理 | 用本体（Ontology）/模式约束来组织、检索、校验生成 ⚠️ |
| 知识载体 | 非结构化文本块（chunk） | 结构化知识图谱 + 文本块互索引 | 本体/概念模式（schema） |
| 代表开源 | LangChain / LlamaIndex / FAISS / Milvus | OpenSPG/KAG (GitHub) | 无主流统一实现 ⚠️ |

---

## 1. RAG — Retrieval-Augmented Generation（检索增强生成）

### 1.1 定义与核心思想
检索增强生成（RAG）把"参数化记忆"（大模型权重中的知识）与"非参数化记忆"（外部文档库）结合：给定问题，先用检索器从外部语料中召回相关文档，再把这些文档作为上下文交给生成模型作答。其目的是让模型在不重训参数的情况下获取最新、可溯源、领域专有的知识，从而降低幻觉、提升事实性并支持引用。

**代表论文（✅ 高置信）**
- Patrick Lewis 等，*Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*，Facebook AI Research，NeurIPS 2020，arXiv:2005.11401。将预训练生成器（BART）与稠密检索器（DPR）以"隐变量"方式联合，包含 RAG-Sequence 与 RAG-Token 两种边缘化方式。

**前序工作（✅）**
- DPR：*Dense Passage Retrieval for Open-Domain Question Answering*，Karpukhin 等，2020，arXiv:2004.04906。
- REALM：Guu 等，2020，arXiv:2002.08909。
- ORQA：Lee 等，2019，arXiv:1906.00300。

### 1.2 演进三阶段（Naive → Advanced → Modular，⚠️ 该术语分类出自下述综述）
- **分类学来源（✅）**：Yunfan Gao 等，*Retrieval-Augmented Generation for Large Language Models: A Survey*，arXiv:2312.10997（2023）。正是这篇综述提出 Naive RAG / Advanced RAG / Modular RAG 的三段式划分。

| 阶段 | 核心变化 | 关键手段 |
|---|---|---|
| **Naive RAG** | "检索—生成"两段式，直接索引切块 | 文本切块 → 向量化 → 向量库 top-k 检索 → 拼接 Prompt 生成 |
| **Advanced RAG** | 在检索前后加优化环节 | 检索前：查询改写/扩展、HyDE；检索中：混合检索；检索后：Rerank 重排 |
| **Modular RAG** | 把 RAG 拆成可复用/可重组的"乐高模块" | 引入 Routing、Memory、Search、Predict、Fusion 等独立模块与流程编排 |

- **Modular RAG 专论（✅）**：Yunfan Gao 等，*Modular RAG: Transforming RAG Systems into LEGO-like Reconfigurable Frameworks*，arXiv:2407.21059（2024）。

### 1.3 技术栈（向量检索 / 混合检索 / Rerank）
- **检索前（Pre-retrieval）**：查询改写、多查询扩展、假设文档嵌入 HyDE（Gao 等，2022，arXiv:2212.10496）。
- **检索（Retrieval）**
  - 稀疏检索：BM25；稠密检索：DPR、BGE、E5、text-embedding-3 等 embedding。
  - **混合检索（Hybrid）**：稀疏 + 稠密融合，常见融合算法 RRF（Reciprocal Rank Fusion）、加权求和。
  - 向量库：FAISS、Milvus、Qdrant、Weaviate、Pinecone、pgvector。
- **检索后（Post-retrieval / Rerank）**：Cross-Encoder 重排（如 bge-reranker、Cohere Rerank）、迟交互 ColBERT（Khattab & Zaharia，2020，arXiv:2004.12832）、LLM 重排（RankGPT）。
- **进阶范式**：Self-RAG（Asai 等，2023，arXiv:2310.11511）、Corrective RAG / CRAG（Yan 等，2024，arXiv:2401.15884）、GraphRAG（微软，Edge 等，2024，arXiv:2404.16130）。
- **工程框架**：LangChain、LlamaIndex、Haystack。

### 1.4 优势与局限
- **优势**：无需重训即可更新知识；可溯源（给引用）；显著降低事实性幻觉；实现成本相对低、落地快。
- **局限**：① 检索质量是天花板，"垃圾进垃圾出"；② 切块（chunking）策略敏感，易丢上下文；③ 弱多跳/逻辑推理，难以做跨文档聚合推理；④ 模型可能"忽视检索内容"仍按参数作答；⑤ 上下文窗口与延迟/成本压力；⑥ 对强结构化、需一致性的专业领域（政务、医疗）支撑不足。
  - → 这正是 KAG / OAG 试图解决的问题。

---

## 2. KAG — Knowledge Augmented Generation（知识增强生成，蚂蚁集团）

### 2.1 定义与提出方
KAG 是蚂蚁集团（Ant Group）提出的"知识增强生成"框架，建立在其开源知识图谱引擎 **OpenSPG** 之上，目标是在**专业领域**（政务、医疗等）增强大模型生成的事实性、逻辑一致性与可解释性。可视为对纯向量 RAG 的"知识图谱化升级"。

**代表论文 / 项目**
- 论文（✅ 高置信）：*KAG: Boosting LLMs in Professional Domains via Knowledge Augmented Generation*，Liangcai Su 等，蚂蚁集团，arXiv:2409.13731（2024 年 9 月）。
- 开源（✅）：OpenSPG/KAG，GitHub `https://github.com/OpenSPG/KAG`；底座 OpenSPG 语义增强知识图谱引擎 `https://github.com/OpenSPG/openspg`。
  > ⚠️ GitHub 具体路径/y star 数未联网核验，引用仓库名时以实际访问为准。

### 2.2 核心机制（论文提出的关键设计，⚠️ 术语以原文为准）
1. **LLM 友好的知识表示（LLMFriSPG）**：一种兼容"模式约束（schema-constrained/结构化）"与"无模式（schema-free/非结构化文本）"的统一知识表示框架，让图谱既能表达严格本体，又能容纳自由文本。
2. **图结构与文本块互索引**：知识图谱节点与原始文本 chunk 之间建立双向索引，实现"图谱可回溯到原文、原文可定位到图谱"。
3. **逻辑形式引导的混合推理求解器（Logical-form-guided hybrid reasoning）**：把自然语言问题转成"逻辑形式"，再由求解器编排 **LLM 推理 + 符号化图谱推理 + 数学/逻辑运算**，弥补纯向量检索的弱逻辑缺陷。
4. **语义推理驱动的知识对齐（Knowledge alignment）**：用语义推理完成概念/实体对齐，减少知识冲突与冗余。
5. **KAG-Model**：面向自然语言理解（NLU）的模型组件，参与理解与推理。

### 2.3 检索方式
采用**混合检索**：向量检索负责从海量文本中召回相关语义片段；图谱检索/符号推理负责结构化、可验证的多跳关系推理；二者通过互索引协同。相较纯向量 RAG，KAG 更强调"逻辑形式 + 图谱"来保证多跳与专业一致性。

### 2.4 优势与局限
- **优势**：多跳与逻辑推理明显更强；专业知识一致性好、可解释、可溯源（图路径 + 原文）；对政务/医疗等强合规领域更友好；论文报告在 HotpotQA、2WikiMultiHopQA 等任务上有提升（⚠️ 具体数值以论文为准）。
- **局限**：① 需构建并维护知识图谱/本体，前期成本高；② 依赖领域 schema 设计，迁移性受限；③ 工程链复杂（图谱 + 向量 + 推理求解器），落地门槛高于朴素 RAG；④ 强依赖高质量领域数据。

---

## 3. OAG — 缩写存在歧义，务必核验 ❓

> **重要不确定性声明**：与前两者不同，**"OAG"在"知识增强/知识图谱"文献中并非公认的标准化简称**，不像 RAG（2020, NeurIPS）和 KAG（2024, arXiv:2409.13731）那样有明确的"提出方 + 论文 + 开源"。本次调研**未能联网核验**，因此以下并列给出多种可能解释，**在对外报告中必须标注"非权威/待核验"**。

### 解释 A（最贴合"三大范式"叙事）：Ontology-Augmented Generation，本体增强生成 ⚠️
- **核心思想**：在检索与生成之间引入**本体（Ontology）/概念模式（schema）**作为"知识骨架"，用本体来组织语料、约束检索范围、并校验/归一化生成内容，从而获得比纯文本单元检索更强的一致性、可解释性与推理能力。
- **与 KAG 的关系**：方向高度相近——都强调"本体/图谱 + 生成"。区别在于 KAG 由蚂蚁集团明确提出并配套 OpenSPG 开源实现，而 OAG 更多是**学术/社区里的概念性说法**，缺乏单一权威出处。
- **可能的候选出处（❓ 均未核验，勿直接引用具体编号）**：社区中与"本体驱动 RAG"相关的表述有 *Ontology-Grounded Retrieval-Augmented Generation*（疑似缩写 OG-RAG）、"Ontology-driven RAG / OntoRAG" 等，但**作者、机构、年份、arXiv 号我均无把握，故不填写**，请后续联网确认后再引用。
- **关键组件（概念层）**：领域本体/OWL 模式；本体对齐与实体消歧；本体约束下的检索（本体检索 + 向量检索混合）；生成后的本体一致性校验。
- **优势**：语义一致性高、可解释、适合强规范领域（医疗、金融、制造、政务）。
- **局限**：本体构建与维护成本极高；本体覆盖率与更新滞后问题；对本体质量强依赖；缺少成熟统一实现与评测基准。

### 解释 B：Open Agent Graph（若按"智能体图"理解）❓
- 若 OAG 指"开放智能体图/Agent 图"，则更偏向"多智能体协作 + 图结构"方向，与知识增强生成的关注点不同。**该解释在当前主流知识增强文献中并非通用缩写**，我无法确认存在以此为主名的主流项目，故仅作并列提示，不下结论。

### 解释 C：其它领域的同名缩写（易混淆，务必区分）⚠️
- **OAG（Official Airline Guide）**：航空航班数据服务商，与 AI 无关，但检索时极易干扰结果，助记时注意避开。
- **OAG（Office of the Attorney General）**：总检察长办公室等机构名缩写。
- 结论：**跨领域检索 "OAG" 会大量混入噪声**，这会进一步说明为何该缩写在 AI 学界未形成公认含义。

### OAG 小结（诚实标注）
- 若报告需要"三大范式"严格对齐，建议将 OAG 表述为：**"Ontology-Augmented Generation（本体增强生成）——一个与 KAG 相邻、但尚未形成权威单一起源与统一实现的范式，可视为'本体/模式驱动的知识增强生成'方向的统称"**，并在脚注中说明 **"OAG 缩写存在歧义，本文取 Ontology-Augmented Generation 之义，非公认标准缩写"**。

---

## 4. 关键出处链接汇总（供后续核验）

| 范式 | 名称 | 出处 / 链接 | 置信度 |
|---|---|---|---|
| RAG | Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks | arXiv:2005.11401 (Lewis et al., NeurIPS 2020) | ✅ |
| RAG | Dense Passage Retrieval (DPR) | arXiv:2004.04906 | ✅ |
| RAG | RAG for LLMs: A Survey（提出 Naive/Advanced/Modular 分类） | arXiv:2312.10997 | ✅ |
| RAG | Modular RAG（LEGO-like） | arXiv:2407.21059 | ✅ |
| RAG | HyDE | arXiv:2212.10496 | ✅ |
| RAG | ColBERT (Late Interaction) | arXiv:2004.12832 | ✅ |
| RAG | Self-RAG | arXiv:2310.11511 | ✅ |
| RAG | Corrective RAG (CRAG) | arXiv:2401.15884 | ✅ |
| RAG | Graph RAG (Microsoft) | arXiv:2404.16130 | ⚠️（标题/编号需复核） |
| KAG | KAG: Boosting LLMs in Professional Domains via Knowledge Augmented Generation | arXiv:2409.13731 (Su et al., Ant Group, 2024) | ✅ |
| KAG | OpenSPG/KAG 开源仓库 | https://github.com/OpenSPG/KAG | ✅（路径需核验） |
| KAG | OpenSPG 引擎 | https://github.com/OpenSPG/openspg | ✅（路径需核验） |
| OAG | Ontology-Augmented Generation | 无权威单一起源，候选名 OG-RAG / Ontology-driven RAG 等 | ❓ |
| OAG | Open Agent Graph（并列解释） | 无确认的主流主名项目 | ❓ |

---

## 5. 给后续节点的提醒
1. **必做核验项**：OAG 的权威出处（是否存在论文/项目把 Ontology-Augmented Generation 作为正式范式名）；KAG 仓库路径与论文 `arXiv:2409.13731` 的标题、作者；GraphRAG 的 `arXiv:2404.16130` 编号。
2. **写作口径建议**：RAG、KAG 可按"有明确起源"论述；OAG 必须加"缩写歧义 + 出处待核验"声明。
3. **对比落点**：三者差异可归纳为"知识载体"演进——非结构化文本块（RAG）→ 图文互索引的混合知识图谱（KAG）→ 本体/模式约束（OAG）；以及"推理能力"演进——向量相似（RAG）→ 逻辑形式 + 符号推理（KAG）→ 本体一致性推理（OAG）。

---
*本文档由 research 子任务生成；因网络工具不可用，★可信度以文内标注为准，联网后请优先补核验 §5 列出的条目。*
