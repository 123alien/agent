import hashlib
import tempfile
import unittest
from pathlib import Path

from app.schemas.task import (
    DocumentQualityCheck,
    DocumentSection,
    ExtractedField,
    ParsedDocument,
)
from app.services.document_context import build_document_context


class DocumentContextTests(unittest.TestCase):
    def _document(self) -> ParsedDocument:
        return ParsedDocument(
            file_id="doc-1",
            filename="招标文件.docx",
            file_type="docx",
            text_length=100,
            tenderer="某采购中心",
            bidders=["甲公司"],
            qualification_requirements=["投标人应具有独立法人资格。"],
            scoring_criteria=["技术方案满分40分。"],
            key_clauses=[
                "系统应支持统一身份认证。",
                "合同服务期限为12个月。",
            ],
            sections=[
                DocumentSection(
                    title="第二章 资格要求",
                    content="投标人应具有独立法人资格。",
                    page=3,
                ),
                DocumentSection(
                    title="第三章 技术要求",
                    content="系统应支持统一身份认证。",
                    page=8,
                ),
            ],
            extracted_fields={
                "project_name": ExtractedField(
                    value="政务平台项目",
                    raw_text="项目名称：政务平台项目",
                    source_location="第一章",
                    confidence=0.98,
                )
            },
            quality_checks=[
                DocumentQualityCheck(
                    code="missing_page",
                    status="warning",
                    message="部分条款缺少页码定位",
                    requires_human_review=True,
                )
            ],
        )

    def test_builds_frozen_contract_and_reuses_parser_output(self):
        text = "联系人13800001111，邮箱bid@example.com。"
        context = build_document_context(self._document(), text)

        self.assertEqual(context.contract_version, "1.0.0")
        self.assertEqual(context.file_hash, hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(context.file_metadata["hash_source"], "parsed_text")
        self.assertEqual(context.entities.contacts, ["13800001111"])
        self.assertEqual(context.entities.emails, ["bid@example.com"])
        self.assertEqual(len(context.clause_groups.qualification), 1)
        self.assertEqual(len(context.clause_groups.technical), 1)
        self.assertEqual(len(context.clause_groups.procedure_contract), 1)
        self.assertEqual(context.clause_groups.qualification[0].source.page, 3)
        self.assertEqual(context.key_fields["project_name"].confidence, 0.98)
        self.assertTrue(context.quality.requires_human_review)

    def test_prefers_real_file_bytes_for_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_bytes(b"real-file-bytes")
            context = build_document_context(self._document(), "parsed text", path)

        self.assertEqual(
            context.file_hash, hashlib.sha256(b"real-file-bytes").hexdigest()
        )
        self.assertEqual(context.file_metadata["hash_source"], "file_bytes")


if __name__ == "__main__":
    unittest.main()
