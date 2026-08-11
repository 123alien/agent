import unittest

from app.agents.quality_reviewer import QualityReviewerAgent
from app.schemas.task import Issue


class QualityReviewerIssueIdTests(unittest.TestCase):
    def test_same_evidence_on_different_pages_gets_unique_ids(self):
        issues = [
            Issue(agent="文档解析智能体", risk_level="中", source_file="A.pdf", source_location="第 8 页", issue_type="印章视觉核验待复核", description="第8页待复核", evidence=["投标人（盖章）："], requires_human_review=True),
            Issue(agent="文档解析智能体", risk_level="中", source_file="A.pdf", source_location="第 12 页", issue_type="印章视觉核验待复核", description="第12页待复核", evidence=["投标人（盖章）："], requires_human_review=True),
        ]
        result = QualityReviewerAgent().review(issues, {"A.pdf": "投标人（盖章）："})
        self.assertEqual(len(result.valid_issues), 2)
        self.assertNotEqual(result.valid_issues[0].issue_id, result.valid_issues[1].issue_id)


if __name__ == "__main__":
    unittest.main()
