"""app.memory.entity_normalizer — 实体术语归一化钩子（GraphRAG 建图前置）。

问题背景：LightRAG 按 chunk 独立抽取实体/关系，同一概念在不同 chunk 里
常被 LLM 写成不同变体（RAG/Rag、GraphRAG/GraphRag、Microsoft/Microsoft
Research 等），而图谱层只对完全相同的实体名去重，导致节点膨胀。

本模块在喂入 GraphRAG 前对文本做确定性术语还原：
  1. 把已知变体统一成规范名（大小写/拼写变体 → 官方写法）；
  2. 缩写与全称互相映射（LLM ↔ Large Language Model）；
  3. 只合并"确定性同义"，避免误伤真·不同概念。

这样 LLM 抽取时只看到规范名，从源头消除变体分裂。
"""
from __future__ import annotations
import re
from typing import Dict, Tuple

# 术语变体 → 规范名。规则：全词匹配、大小写不敏感（词边界 + 单词），
# 保证 "GraphRAG" 不被 "function" 之类的子串误伤。
# 顺序重要：长词/更具体变体在前，避免缩写先命中导致后续全称不再匹配。
_TERM_ALIASES: Dict[str, Tuple[str, ...]] = {
    # AI 检索/增强范式（仅合并无歧义的大小写/拼写变体与确定同义缩写）
    "RAG": ("Rag", "rag"),
    "KAG": ("Kag", "kag"),
    "OAG": ("Oag", "oag"),
    "GraphRAG": ("GraphRag", "Graphrag", "graphrag"),
    "LLM": ("LLMs", "Large Language Model", "large language model", "大语言模型"),
    "OpenSPG": ("OpenSpg", "openspg"),
}


def canonical_variants() -> Dict[str, Tuple[str, ...]]:
    """返回规范名 → 已知变体清单（供图谱侧 merge_entities 使用）。"""
    return dict(_TERM_ALIASES)

# 逐个构建 regex：用 ASCII 词边界（两侧非 [A-Za-z0-9]）而非 re 的 \b，
# 因为 \b 对 CJK 无效（中文字符被当 \w），会导致 "了GraphRag" 这类中英混排
# 前缀无法命中。这样 "GraphRAG" 不会被 "function" 之类英文子串误伤，
# 中文紧邻也不受影响。
_RE_COMPILED = [
    (
        canonical,
        re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(v) for v in aliases) + r")(?![A-Za-z0-9])"),
    )
    for canonical, aliases in _TERM_ALIASES.items()
]


def normalize_terms(text: str) -> str:
    """把文本中已知的实体变体替换为规范名。未知文本原样返回。"""
    if not text:
        return text
    out = text
    for canonical, rx in _RE_COMPILED:
        out = rx.sub(canonical, out)
    return out


def alias_map() -> Dict[str, str]:
    """暴露当前别名映射（供仪表盘/调试展示）。"""
    return {v: canonical for canonical, aliases in _TERM_ALIASES.items() for v in aliases}


def normalize_entity_name(name: str) -> str:
    """入库前对单个实体名做归一化（供显式三元组/实体名走同一套规则）。"""
    if not name:
        return name
    return normalize_terms(name).strip()