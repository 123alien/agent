import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document

from app.schemas.contract import ContractGenerationRequest, ContractParty
from app.services.contract_service import amount_to_chinese, create_contract_docx, validate_contract


def contract_request() -> ContractGenerationRequest:
    return ContractGenerationRequest(
        project_id="P-001",
        project_name="某市政务平台运维项目",
        purchaser=ContractParty(name="某市政务服务中心", unified_social_credit_code="123456", address="某市1号"),
        supplier=ContractParty(name="某科技有限公司", unified_social_credit_code="654321", address="某市2号"),
        contract_amount=Decimal("1200000"),
        service_start_date=date(2026, 9, 1),
        service_end_date=date(2027, 8, 31),
        service_scope=["提供平台运行维护及技术支持服务。"],
        payment_terms=["合同签订后支付合同金额的30%。", "验收合格后支付剩余款项。"],
        acceptance_criteria=["系统运行稳定并通过采购人组织的验收。"],
        breach_terms=["违约方应承担相应违约责任。"],
    )


class ContractGenerationTests(unittest.TestCase):
    def test_amount_to_chinese(self) -> None:
        self.assertEqual(amount_to_chinese(Decimal("1200000")), "人民币壹佰贰拾万元整")

    def test_invalid_service_period_is_blocked(self) -> None:
        request = contract_request().model_copy(update={"service_end_date": date(2026, 8, 31)})
        items = validate_contract(request)
        self.assertTrue(any(item.code == "INVALID_SERVICE_PERIOD" and item.level == "error" for item in items))

    def test_contract_docx_contains_core_fields(self) -> None:
        request = contract_request()
        items = validate_contract(request)
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.services.contract_service.settings", SimpleNamespace(contracts_dir=Path(directory))):
                path = create_contract_docx("C-TEST", "HT-2026-001", request, items)
            document = Document(path)
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            self.assertIn("某市政务平台运维项目", text)
            self.assertIn("人民币壹佰贰拾万元整", text)
            self.assertIn("生成校验与人工复核清单", text)
            self.assertIn("数据安全与保密", text)
            self.assertIn("变更管理", text)
            self.assertIn("合同解除与终止", text)


if __name__ == "__main__":
    unittest.main()
