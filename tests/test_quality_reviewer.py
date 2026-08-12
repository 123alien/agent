import unittest

from app.agents.quality_reviewer import QualityReviewerAgent
from app.schemas.task import EvidenceRef, Issue


class QualityReviewerIssueIdTests(unittest.TestCase):
    def test_derived_detector_evidence_is_valid(self):
        issue = Issue(
            agent="文档解析智能体", risk_level="中", source_file="D.pdf",
            source_location="第 8 页", issue_type="印章视觉核验待复核",
            description="第8页未确认印章",
            evidence=["投标人（盖章）：", "状态=not_detected；检测器=red-seal-rule-v1"],
            evidence_refs=[
                EvidenceRef(document_id="F1", quote="投标人（盖章）：", page=8),
                EvidenceRef(document_id="F1", quote="状态=not_detected；检测器=red-seal-rule-v1", page=8, source_type="derived"),
            ], requires_human_review=True, detection_status="not_detected",
        )
        result = QualityReviewerAgent().review([issue], {"D.pdf": "投标人（盖章）："})
        self.assertEqual(len(result.valid_issues), 1)

    def test_same_evidence_on_different_pages_gets_unique_ids(self):
        issues = [
            Issue(agent="文档解析智能体", risk_level="中", source_file="A.pdf", source_location="第 8 页", issue_type="印章视觉核验待复核", description="第8页待复核", evidence=["投标人（盖章）："], requires_human_review=True),
            Issue(agent="文档解析智能体", risk_level="中", source_file="A.pdf", source_location="第 12 页", issue_type="印章视觉核验待复核", description="第12页待复核", evidence=["投标人（盖章）："], requires_human_review=True),
        ]
        result = QualityReviewerAgent().review(issues, {"A.pdf": "投标人（盖章）："})
        self.assertEqual(len(result.valid_issues), 2)
        self.assertNotEqual(result.valid_issues[0].issue_id, result.valid_issues[1].issue_id)

    def test_related_anomaly_signals_are_merged_by_entity_pair(self):
        issues = [
            Issue(agent="异常分析智能体", risk_level="中", source_file="A.pdf、B.pdf", source_location="甲公司、乙公司", issue_type="跨文件主体联系信息重合", description="电话相同", evidence=["13800001111"], requires_human_review=True),
            Issue(agent="异常分析智能体", risk_level="高", source_file="A.pdf、B.pdf", source_location="甲公司、乙公司", issue_type="设备网络与文件元数据组合异常", description="设备相同", evidence=["DEVICE-001"], requires_human_review=True),
        ]
        raw = {"A.pdf": "13800001111 DEVICE-001", "B.pdf": "13800001111 DEVICE-001"}
        result = QualityReviewerAgent().review(issues, raw)
        self.assertEqual(len(result.valid_issues), 1)
        merged = result.valid_issues[0]
        self.assertEqual(merged.issue_type, "多信号组合异常")
        self.assertEqual(merged.risk_level, "高")
        self.assertEqual(merged.evidence, ["13800001111", "DEVICE-001"])
        self.assertTrue(merged.requires_human_review)


if __name__ == "__main__":
    unittest.main()
