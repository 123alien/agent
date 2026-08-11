import unittest

from app.schemas.task import (
    DocumentSection,
    DocumentTable,
    Issue,
    ParsedDocument,
)
from app.services.document_context import build_document_context
from app.services.evidence_locator import enrich_issue_evidence, locate_evidence


class EvidenceLocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = ParsedDocument(
            file_id="F001",
            filename="真实招标文件.pdf",
            file_type="招标文件",
            text_length=100,
            sections=[
                DocumentSection(
                    title="第二章 投标人资格要求",
                    content="投标人注册地址必须位于本市。",
                    page=18,
                )
            ],
            tables=[DocumentTable(page=25, rows=[["投标报价", "100万元"]])],
        )
        self.context = build_document_context(
            self.document,
            "投标人注册地址必须位于本市。\n投标报价为100万元。",
        )

    def test_locates_exact_quote_to_section_and_page(self) -> None:
        refs = locate_evidence(
            "投标人注册地址必须位于本市。",
            [self.context],
            "真实招标文件.pdf",
        )

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].document_id, "F001")
        self.assertEqual(refs[0].section, "第二章 投标人资格要求")
        self.assertEqual(refs[0].page, 18)
        self.assertEqual(refs[0].source_type, "text")

    def test_unverifiable_quote_does_not_create_reference(self) -> None:
        issue = Issue(
            agent="合规审查智能体",
            risk_level="中",
            issue_type="测试",
            description="测试问题",
            evidence=["模型虚构的原文"],
        )

        enrich_issue_evidence(issue, [self.context])

        self.assertEqual(issue.evidence_refs, [])

    def test_enrichment_fills_source_location_without_changing_quote(self) -> None:
        quote = "投标人注册地址必须位于本市。"
        issue = Issue(
            agent="合规审查智能体",
            risk_level="高",
            issue_type="地域限制",
            description="可能构成地域限制",
            evidence=[quote],
            requires_human_review=True,
        )

        enrich_issue_evidence(issue, [self.context])

        self.assertEqual(issue.evidence, [quote])
        self.assertEqual(issue.source_file, "真实招标文件.pdf")
        self.assertEqual(issue.source_location, "第二章 投标人资格要求，第18页")


if __name__ == "__main__":
    unittest.main()
