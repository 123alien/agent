import unittest
from unittest.mock import PropertyMock, patch

from app.agents.data_validator import DataValidatorAgent
from app.schemas.task import (
    CandidateRanking,
    DocumentTable,
    OpeningRecord,
    ParsedDocument,
    ScoreDetail,
    ScoreSummary,
    SourceLocation,
)
from app.services.document_context import build_document_context
from app.services.dify_client import DifyWorkflowError, dify_client


class DataValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = ParsedDocument(
            file_id="F1",
            filename="测试文件.txt",
            file_type="招标文件",
            text_length=100,
            project_name="测试项目",
            scoring_criteria=["评分办法"],
        )
        self.text = "服务期限为12个月。合同服务期限为10个月。"

    def test_shared_context_contract_is_recorded(self) -> None:
        context = build_document_context(self.doc, self.text)
        with patch.object(
            type(dify_client),
            "data_validator_enabled",
            new_callable=PropertyMock,
            return_value=False,
        ):
            result = DataValidatorAgent().run_contexts([context], [self.doc])

        self.assertEqual(result.data["input_contract"], "DocumentContext/1.0.0")
        self.assertEqual(result.data["input_document_count"], 1)

    def test_dify_result_requires_exact_source_evidence(self) -> None:
        payload = {
            "issues": [
                {
                    "is_issue": True,
                    "risk_level": "高",
                    "issue_type": "数据不一致",
                    "description": "服务期限冲突",
                    "field_name": "service_period",
                    "evidence": ["服务期限为12个月。", "合同服务期限为10个月。"],
                    "basis": "同一字段冲突",
                    "suggestion": "统一期限",
                    "requires_human_review": False,
                },
                {
                    "is_issue": True,
                    "risk_level": "中",
                    "issue_type": "虚构问题",
                    "description": "不存在的证据",
                    "evidence": ["原文没有这句话", "另一个虚构证据"],
                },
            ]
        }
        with patch.object(
            type(dify_client),
            "data_validator_enabled",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "app.agents.data_validator.dify_client.run_data_validator",
            return_value=payload,
        ):
            result = DataValidatorAgent().run([self.doc], {"F1": self.text})

        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].source_location, "service_period")
        self.assertEqual(result.data["execution_mode"], "dify")

    def test_dify_self_contradictory_equal_values_are_filtered(self) -> None:
        payload = {
            "issues": [{
                "is_issue": True,
                "risk_level": "高",
                "issue_type": "合同金额与投标总价不一致",
                "description": "合同金额与投标总价一致，但仍需确认。",
                "field_name": "contract_amount",
                "value_1": "120万元",
                "value_2": "120万元",
                "evidence": ["投标总价为120万元。", "合同金额为120万元。"],
                "basis": "两者一致。",
                "suggestion": "核对。",
                "requires_human_review": True,
            }]
        }
        text = "投标总价为120万元。合同金额为120万元。"
        with patch.object(type(dify_client), "data_validator_enabled", new_callable=PropertyMock, return_value=True), patch(
            "app.agents.data_validator.dify_client.run_data_validator", return_value=payload
        ):
            result = DataValidatorAgent().run([self.doc], {"F1": text})

        self.assertFalse(any(item.issue_type == "合同金额与投标总价不一致" for item in result.issues))

    def test_dify_failure_falls_back_to_local_rules(self) -> None:
        with patch.object(
            type(dify_client),
            "data_validator_enabled",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "app.agents.data_validator.dify_client.run_data_validator",
            side_effect=DifyWorkflowError("timeout"),
        ):
            result = DataValidatorAgent().run([self.doc], {"F1": self.text})

        self.assertEqual(result.data["execution_mode"], "local_fallback")
        self.assertTrue(result.data["dify_errors"])

    def test_deterministic_rules_recalculate_weight_total_and_rank(self) -> None:
        doc = ParsedDocument(
            file_id="F-score",
            filename="评分汇总表.xlsx",
            file_type="评分表",
            text_length=200,
            project_name="测试项目",
            score_details=[
                ScoreDetail(bidder="甲公司", factor="技术", raw_score=80, weight=50, weighted_score=45),
                ScoreDetail(bidder="甲公司", factor="商务", raw_score=40, weight=50, weighted_score=20),
                ScoreDetail(bidder="乙公司", factor="技术", raw_score=70, weight=50, weighted_score=35),
                ScoreDetail(bidder="乙公司", factor="商务", raw_score=50, weight=50, weighted_score=25),
            ],
            score_summaries=[
                ScoreSummary(bidder="甲公司", total_score=65, rank=2),
                ScoreSummary(bidder="乙公司", total_score=60, rank=1),
            ],
        )
        with patch.object(type(dify_client), "data_validator_enabled", new_callable=PropertyMock, return_value=False):
            result = DataValidatorAgent().run([doc])

        issue_types = [item.issue_type for item in result.issues]
        self.assertIn("权重折算错误", issue_types)
        self.assertEqual(issue_types.count("得分排名不一致"), 2)
        self.assertEqual(result.data["score_detail_count"], 4)

    def test_cross_document_price_and_candidate_order_are_checked(self) -> None:
        opening = ParsedDocument(
            file_id="F-open",
            filename="开标记录表.xlsx",
            file_type="开标记录表",
            text_length=100,
            project_name="测试项目",
            opening_records=[OpeningRecord(bidder="甲公司", bid_price=980000)],
        )
        report = ParsedDocument(
            file_id="F-report",
            filename="评标报告.docx",
            file_type="评标报告",
            text_length=100,
            project_name="测试项目",
            opening_records=[OpeningRecord(bidder="甲公司", bid_price=990000)],
            score_summaries=[ScoreSummary(bidder="甲公司", total_score=90, rank=1)],
            candidate_rankings=[CandidateRanking(bidder="甲公司", rank=2, evidence="第二中标候选人：甲公司。")],
        )
        with patch.object(type(dify_client), "data_validator_enabled", new_callable=PropertyMock, return_value=False):
            result = DataValidatorAgent().run([opening, report])

        issue_types = {item.issue_type for item in result.issues}
        self.assertIn("跨文件报价不一致", issue_types)
        self.assertIn("中标候选人排序不一致", issue_types)

    def test_plain_text_component_prices_are_recalculated(self) -> None:
        text = (
            "软件开发服务报价为人民币60万元。\n"
            "系统运维服务报价为人民币30万元。\n"
            "培训服务报价为人民币20万元。\n"
            "投标总价为人民币120万元。"
        )
        with patch.object(type(dify_client), "data_validator_enabled", new_callable=PropertyMock, return_value=False):
            result = DataValidatorAgent().run([self.doc], {"F1": text})

        price_issues = [item for item in result.issues if item.issue_type == "分项报价合计不一致"]
        self.assertEqual(len(price_issues), 1)
        self.assertIn("1100000.00元", price_issues[0].description)


if __name__ == "__main__":
    unittest.main()
