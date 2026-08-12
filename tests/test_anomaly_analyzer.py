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
        self.doc.file_type = "投标文件"
        self.doc.document_subtype = "响应文件"
        self.doc.document_role = "bid_response"
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

    def test_procurement_anti_collusion_machine_code_rule_is_not_evidence(self) -> None:
        text = "不同投标人的投标文件制作机器码应当不一致，否则按串通投标处理。"
        with patch.object(
            type(dify_client),
            "anomaly_analyzer_enabled",
            new_callable=PropertyMock,
            return_value=False,
        ):
            result = self.agent.run([self.doc], {"F1": text})
        self.assertEqual(result.issues, [])

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
        self.doc.file_type = "投标文件"
        self.doc.document_subtype = "响应文件"
        self.doc.document_role = "bid_response"
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
            OpeningRecord(bidder="正常公司", lot="包1", bid_price=923000),
            OpeningRecord(bidder="甲公司", lot="包1", bid_price=980000),
            OpeningRecord(bidder="乙公司", lot="包1", bid_price=990000),
            OpeningRecord(bidder="丙公司", lot="包1", bid_price=1000000),
        ]
        with patch.object(type(dify_client), "anomaly_analyzer_enabled", new_callable=PropertyMock, return_value=False):
            result = self.agent.run([self.doc], {"F1": "评分和报价记录"})

        issue_types = {item.issue_type for item in result.issues}
        self.assertIn("同一供应商跨标段得分差异异常", issue_types)
        self.assertIn("报价等差规律异常", issue_types)

    def test_business_table_metadata_overlap_is_detected_as_combined_clue(self) -> None:
        metadata = ParsedDocument(
            file_id="F-XLSX", filename="电子交易元数据.xlsx", file_type="业务文件",
            document_subtype="其他资料", text_length=500,
        )
        text = """【工作表：文件与网络元数据】
supplier_code | supplier_name | file_author | created_time | creation_tool | upload_ip | mac_address | machine_code | cost_software_lock_id
S002 | 博远公司 | 制作中心 | 2026-08-08T10:15:00 | WPS | 117.20.33.15 | BC-22 | PC-X888 | LOCK-7788
S003 | 新联公司 | 制作中心 | 2026-08-08T10:16:00 | WPS | 117.20.33.15 | BC-22 | PC-X888 | LOCK-7788
【工作表：其他】"""
        issues = self.agent._metadata_overlap_anomalies([metadata], {"F-XLSX": text})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, "设备网络与文件元数据组合异常")
        self.assertIn("不能直接认定串通投标", issues[0].basis)

    def test_business_metadata_does_not_duplicate_contact_identity(self) -> None:
        response = ParsedDocument(
            file_id="F-BID", filename="甲公司投标文件.pdf", file_type="投标文件",
            document_subtype="响应文件", text_length=100,
        )
        metadata = ParsedDocument(
            file_id="F-XLSX", filename="电子交易元数据.xlsx", file_type="业务文件",
            document_subtype="其他资料", text_length=100,
        )
        issues = self.agent._shared_identity_anomalies(
            [response, metadata],
            {"F-BID": "联系人13800001111", "F-XLSX": "甲公司 13800001111"},
            None,
        )
        self.assertEqual(issues, [])

    def test_similarity_ignores_common_template_and_keeps_distinctive_overlap(self) -> None:
        docs = [
            ParsedDocument(file_id=f"F{i}", filename=f"{i}.pdf", file_type="投标文件", document_subtype="响应文件", text_length=500)
            for i in range(1, 5)
        ]
        common = "投标人承诺遵守采购文件全部要求并对响应文件的真实性负责。"
        unique = "项目实施过程中采用双周迭代机制由项目经理统一协调资源并形成阶段性交付成果。"
        typo = "系统应具备统一身份正认功能实现用户角色和权限的统一管理。"
        build = "本项目采用微服务架构提高系统扩展性和运行稳定性并建设统一服务治理体系。"
        texts = {
            "F1": common + "\n甲方采用独立技术路线并完成各阶段质量控制与验收工作。",
            "F2": common + "\n" + unique + "\n" + typo + "\n" + build,
            "F3": common + "\n" + unique + "\n" + typo + "\n" + build,
            "F4": common + "\n乙方采用另一技术路线并形成独立的部署及运维实施方案。",
        }
        issues = self.agent._document_similarity_anomalies(docs, texts)
        self.assertEqual(len(issues), 1)
        self.assertIn("2.pdf", issues[0].source_file)
        self.assertIn("3.pdf", issues[0].source_file)
        self.assertTrue(issues[0].evidence)

    def test_similarity_ignores_only_two_shared_template_like_lines(self) -> None:
        docs = [
            ParsedDocument(file_id=f"F{i}", filename=f"{i}.pdf", file_type="投标文件", document_subtype="响应文件", text_length=500)
            for i in range(1, 3)
        ]
        first = "我公司已充分理解采购需求将遵循安全可靠可维护的建设原则完成平台升级。"
        second = "采用分层架构与标准接口支持业务组件解耦横向扩展和统一运维。"
        texts = {
            "F1": first + "\n" + second + "\n甲方具有独立的实施计划和质量控制安排。",
            "F2": first + "\n" + second + "\n乙方具有独立的项目组织和交付验收安排。",
        }
        self.assertEqual(self.agent._document_similarity_anomalies(docs, texts), [])

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
