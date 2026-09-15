"""回归测试：实体归一化钩子（app.memory.entity_normalizer）。

覆盖：
  1. normalize_terms 对大小写/拼写变体的还原（Rag→RAG、GraphRag→GraphRAG）；
  2. 词边界保护（"rag" 不误伤 "Rag" 之外如 "config" 之类中文无关文本）；
  3. normalize_entity_name 对单个实体名的归一化；
  4. canonical_variants 与 normalize_terms 使用同一张别名表。

轻量纯函数测试，不触网。
"""
import unittest

from app.memory.entity_normalizer import normalize_terms, normalize_entity_name, canonical_variants


class NormalizeTermsTest(unittest.TestCase):
    def test_case_variants_merged(self):
        self.assertEqual(normalize_terms("Rag is a method"), "RAG is a method")
        self.assertEqual(normalize_terms("GraphRag differs from Rag"), "GraphRAG differs from RAG")
        self.assertEqual(normalize_terms("Graphrag is newer"), "GraphRAG is newer")
        self.assertEqual(normalize_terms("the Oag paradigm"), "the OAG paradigm")
        self.assertEqual(normalize_terms("Kag knowledge augmentation"), "KAG knowledge augmentation")

    def test_abbreviation_full_form_merged(self):
        self.assertEqual(normalize_terms("Large Language Model powers it"), "LLM powers it")
        self.assertEqual(normalize_terms("大语言模型 已就绪"), "LLM 已就绪")
        self.assertEqual(normalize_terms("multiple LLMs"), "multiple LLM")

    def test_word_boundary_no_false_hit(self):
        # 变体只作为独立词替换：GraphRAGrag 中 GraphRAG 部分是前缀不是整词，保持不变
        self.assertEqual(normalize_terms("GraphRAGrag"), "GraphRAGrag")
        # 独立词才替换
        self.assertEqual(normalize_terms("GraphRAG and Rag"), "GraphRAG and RAG")

    def test_cjk_adjacent_english_replaced(self):
        # CJK 被 Python \w 视为词内字符，普通 \b 在此失效；已用 ASCII 边界替代
        self.assertEqual(
            normalize_terms("了解GraphRag和Rag的区别"),
            "了解GraphRAG和RAG的区别",
        )
        self.assertEqual(normalize_terms("Oag用本体约束。"), "OAG用本体约束。")

    def test_empty_and_none(self):
        self.assertEqual(normalize_terms(""), "")
        self.assertEqual(normalize_terms(None), None)

    def test_entity_name_normalization(self):
        self.assertEqual(normalize_entity_name("  Rag "), "RAG")
        self.assertEqual(normalize_entity_name("GraphRag"), "GraphRAG")
        self.assertEqual(normalize_entity_name(""), "")

    def test_variants_match_normalization(self):
        table = canonical_variants()
        # 每条别名应能被 normalize_terms 还原成规范名
        for canonical, variants in table.items():
            for v in variants:
                self.assertEqual(normalize_terms(v), canonical, f"{v} 应还原为 {canonical}")


if __name__ == "__main__":
    unittest.main()