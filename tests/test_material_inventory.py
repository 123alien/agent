import unittest

from app.schemas.task import ParsedDocument
from app.services.material_inventory import build_material_inventory, classify_document_role


class MaterialInventoryTests(unittest.TestCase):
    def test_classifies_project_document_roles(self) -> None:
        cases = {
            "01_采购文件.pdf": "procurement_document",
            "A公司投标文件.pdf": "bid_response",
            "开标记录表.xlsx": "opening_record",
            "评审标准及评分办法.xlsx": "evaluation_standard",
            "专家评分明细.xlsx": "expert_score",
            "评标结果汇总.xlsx": "evaluation_summary",
            "评标报告.pdf": "evaluation_report",
            "电子交易元数据.xlsx": "transaction_metadata",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(classify_document_role(filename), expected)

    def test_missing_material_is_not_described_as_confirmed_missing(self) -> None:
        document = ParsedDocument(
            file_id="F1", filename="采购文件.pdf", file_type="招标文件",
            document_subtype="采购文件", document_role="procurement_document",
            text_length=10,
        )
        result = build_material_inventory([document], "full")
        self.assertTrue(result["requires_human_review"])
        self.assertEqual(result["role_counts"]["procurement_document"], 1)
        self.assertTrue(result["not_identified_required_documents"])
        self.assertIn("未识别到", result["note"])
        self.assertNotIn("确认缺失", result["note"])


if __name__ == "__main__":
    unittest.main()
