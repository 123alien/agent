import unittest

from app.schemas.task import (
    CandidateRanking,
    ExtractedField,
    Issue,
    ParsedDocument,
    ScoreSummary,
)
from app.services.evaluation_rule_service import (
    evaluation_rule_service,
    public_rule_catalog,
)


class EvaluationRuleServiceTests(unittest.TestCase):
    def test_catalog_contains_three_groups_and_41_rules(self) -> None:
        catalog = public_rule_catalog()
        self.assertEqual(catalog["total_rules"], 41)
        self.assertEqual(catalog["active_rules"], 39)
        self.assertEqual([group["group_code"] for group in catalog["groups"]], ["P1", "P2", "P3"])

    def test_missing_material_is_not_marked_passed(self) -> None:
        result = evaluation_rule_service.evaluate([], [], [], [])
        self.assertEqual(result["summary"]["insufficient_data"], 39)
        self.assertEqual(result["summary"]["disabled"], 2)
        self.assertEqual(result["summary"]["passed"], 0)

    def test_existing_issue_is_mapped_to_rule_matrix(self) -> None:
        doc = ParsedDocument(
            file_id="F1",
            filename="评标报告.pdf",
            file_type="pdf",
            document_subtype="评标报告",
            text_length=100,
        )
        issue = Issue(
            issue_id="I-1",
            agent="数据核验智能体",
            risk_level="高",
            issue_type="中标候选人排序不一致",
            description="中标候选人排序与综合得分排名不一致。",
            evidence=["甲公司排名第一，乙公司综合得分第一。"],
            requires_human_review=True,
        )
        result = evaluation_rule_service.evaluate([], [doc], [], [issue])
        row = next(item for item in result["results"] if item["rule_id"] == "P1-04")
        self.assertEqual(row["status"], "human_review")
        self.assertEqual(row["issue_ids"], ["I-1"])

    def test_execution_plan_identifies_method_and_uc_phases(self) -> None:
        doc = ParsedDocument(
            file_id="F2",
            filename="评标报告.pdf",
            file_type="pdf",
            document_subtype="评标报告",
            text_length=100,
            extracted_fields={
                "procurement_method": ExtractedField(
                    value="公开招标", raw_text="采购方式：公开招标"
                )
            },
        )
        plan = evaluation_rule_service.build_execution_plan([], [doc], {}, "full")
        self.assertEqual(plan["procurement_method"], "公开招标")
        self.assertIn("compliance", plan["selected_agents"])
        self.assertNotIn("anomaly", plan["selected_agents"])
        self.assertEqual([phase["uc_id"] for phase in plan["phases"]], ["UC-04", "UC-05", "UC-06", "UC-07"])

    def test_project_name_only_passes_after_two_sources_are_compared(self) -> None:
        doc = ParsedDocument(
            file_id="F3", filename="评标报告.pdf", file_type="pdf", text_length=100,
            extracted_fields={"project_name": ExtractedField(value="测试项目", raw_text="项目名称：测试项目")},
        )
        one_source = evaluation_rule_service.evaluate([], [doc], [], [])
        row = next(item for item in one_source["results"] if item["rule_id"] == "P1-02")
        self.assertEqual(row["status"], "insufficient_data")
        self.assertTrue(row["missing_inputs"])

        compared = evaluation_rule_service.evaluate(
            [], [doc], [], [], system_record={"project_name": "测试项目"}
        )
        row = next(item for item in compared["results"] if item["rule_id"] == "P1-02")
        self.assertEqual(row["status"], "passed")
        self.assertEqual(len(row["execution_evidence"]), 2)

    def test_candidate_ranking_has_reproducible_calculation(self) -> None:
        doc = ParsedDocument(
            file_id="F4", filename="评分汇总.xlsx", file_type="xlsx", text_length=100,
            candidate_rankings=[
                CandidateRanking(bidder="甲公司", rank=1),
                CandidateRanking(bidder="乙公司", rank=2),
            ],
            score_summaries=[
                ScoreSummary(bidder="甲公司", total_score=91.0, rank=1),
                ScoreSummary(bidder="乙公司", total_score=88.0, rank=2),
            ],
        )
        result = evaluation_rule_service.evaluate([], [doc], [], [])
        row = next(item for item in result["results"] if item["rule_id"] == "P1-04")
        self.assertEqual(row["status"], "passed")
        self.assertIn("综合得分降序", row["calculation"])
        self.assertEqual(len(row["execution_evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
