import unittest
from unittest.mock import PropertyMock, patch

from app.agents.anomaly_analyzer import AnomalyAnalyzerAgent
from app.schemas.task import OpeningRecord, ParsedDocument, ScoreDetail, ScoreSummary
from app.services.document_context import build_document_context
from app.services.dify_client import DifyWorkflowError, dify_client


class AnomalyAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = AnomalyAnalyzerAgent()
        self.doc = ParsedDocument(
            file_id="F1",
            filename="采购文件.pdf",
            file_type="招标文件",
            text_length=100,
        )

    def test_relationship_payload_reuses_context_entities_and_hash(self) -> None:
        context = build_document_context(
            self.doc,
            "联系人13800001111，邮箱bid@example.com。",
        )
        payload = self.agent._relationship_context_payload([context], [self.doc])

        self.assertIn(
            {"document_id": "F1", "phone": "13800001111"},
            payload["contacts"],
        )
        self.assertEqual(
            payload["file_metadata"][0]["content_sha256"],
            context.file_hash,
        )

    def test_normal_anti_collusion_clause_is_not_flagged(self) -> None:
        text = "投标人不得相互串通投标，不得排挤其他投标人的公平竞争。"
        with patch.object(
            type(dify_client),
            "anomaly_analyzer_enabled",
            new_callable=PropertyMock,
            return_value=False,
        ):
            result = self.agent.run([self.doc], {"F1": text})
        self.assertEqual(result.issues, [])

    def test_specific_collusion_signal_requires_review(self) -> None:
        text = "检查发现不同投标人的投标文件由同一人编制，报价呈规律性差异。"
        with patch.object(
            type(dify_client),
            "anomaly_analyzer_enabled",
            new_callable=PropertyMock,
            return_value=False,
        ):
            result = self.agent.run([self.doc], {"F1": text})
        self.assertEqual(len(result.issues), 1)
        issue = result.issues[0]
        self.assertEqual(issue.issue_type, "围串标风险线索")
        self.assertEqual(issue.assessment, "待人工判断")
        self.assertTrue(issue.requires_human_review)
        self.assertTrue(all(item in text for item in issue.evidence))

    def test_dify_anomaly_requires_verifiable_evidence(self) -> None:
        self.doc.bid_prices = ["980000"]
        payload = {
            "anomalies": [
                {
                    "is_anomaly": True,
                    "risk_level": "中",
                    "anomaly_type": "报价规律异常",
                    "description": "报价形成规律",
                    "related_entities": ["F1", "F2", "F3"],
                    "evidence": ["980000"],
                    "basis": "需进一步核验",
                    "suggestion": "人工复核",
                    "requires_human_review": True,
                },
                {
                    "is_anomaly": True,
                    "risk_level": "高",
                    "anomaly_type": "虚构异常",
                    "related_entities": ["F1", "F2"],
                    "evidence": ["原始输入中不存在的证据"],
                },
            ]
        }
        with patch.object(
            type(dify_client),
            "anomaly_analyzer_enabled",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "app.agents.anomaly_analyzer.dify_client.run_anomaly_analyzer",
            return_value=payload,
        ):
            result = self.agent.run([self.doc], {"F1": "报价为980000"}, [])

        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].assessment, "待人工判断")
        self.assertEqual(result.data["execution_mode"], "dify")

    def test_dify_failure_falls_back_to_local_analysis(self) -> None:
        text = "检查发现不同投标人的投标文件由同一人编制。"
        with patch.object(
            type(dify_client),
            "anomaly_analyzer_enabled",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "app.agents.anomaly_analyzer.dify_client.run_anomaly_analyzer",
            side_effect=DifyWorkflowError("timeout"),
        ):
            result = self.agent.run([self.doc], {"F1": text}, [])

        self.assertEqual(result.data["execution_mode"], "local_fallback")
        self.assertTrue(result.issues)

    def test_expert_score_deviation_is_detected_deterministically(self) -> None:
        self.doc.score_details = [
            ScoreDetail(bidder="甲公司", expert="专家A", factor="技术方案", max_score=40, raw_score=36),
            ScoreDetail(bidder="甲公司", expert="专家B", factor="技术方案", max_score=40, raw_score=35),
            ScoreDetail(bidder="甲公司", expert="专家C", factor="技术方案", max_score=40, raw_score=12),
        ]
        with patch.object(type(dify_client), "anomaly_analyzer_enabled", new_callable=PropertyMock, return_value=False):
            result = self.agent.run([self.doc], {"F1": "评分明细"})

        self.assertTrue(any(item.issue_type == "专家评分显著偏离" for item in result.issues))

    def test_cross_lot_and_arithmetic_price_patterns_are_detected(self) -> None:
        self.doc.score_summaries = [
            ScoreSummary(bidder="甲公司", lot="包1", total_score=92),
            ScoreSummary(bidder="甲公司", lot="包2", total_score=76),
        ]
        self.doc.opening_records = [
            OpeningRecord(bidder="甲公司", lot="包1", bid_price=980000),
            OpeningRecord(bidder="乙公司", lot="包1", bid_price=990000),
            OpeningRecord(bidder="丙公司", lot="包1", bid_price=1000000),
        ]
        with patch.object(type(dify_client), "anomaly_analyzer_enabled", new_callable=PropertyMock, return_value=False):
            result = self.agent.run([self.doc], {"F1": "评分和报价记录"})

        issue_types = {item.issue_type for item in result.issues}
        self.assertIn("同一供应商跨标段得分差异异常", issue_types)
        self.assertIn("报价等差规律异常", issue_types)

    def test_dify_and_deterministic_results_are_merged(self) -> None:
        self.doc.opening_records = [
            OpeningRecord(bidder="甲公司", bid_price=980000),
            OpeningRecord(bidder="乙公司", bid_price=990000),
            OpeningRecord(bidder="丙公司", bid_price=1000000),
        ]
        payload = {
            "anomalies": [{
                "is_anomaly": True,
                "risk_level": "中",
                "anomaly_type": "主体关联异常",
                "description": "联系信息重合",
                "related_entities": ["甲公司", "乙公司"],
                "evidence": ["13800001111"],
                "basis": "需核查",
                "suggestion": "人工复核",
                "requires_human_review": True,
            }]
        }
        with patch.object(type(dify_client), "anomaly_analyzer_enabled", new_callable=PropertyMock, return_value=True), patch(
            "app.agents.anomaly_analyzer.dify_client.run_anomaly_analyzer", return_value=payload
        ):
            result = self.agent.run([self.doc], {"F1": "联系电话13800001111"}, [])

        issue_types = {item.issue_type for item in result.issues}
        self.assertIn("报价等差规律异常", issue_types)
        self.assertIn("主体关联异常", issue_types)


if __name__ == "__main__":
    unittest.main()
