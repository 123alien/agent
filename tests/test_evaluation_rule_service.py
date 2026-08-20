import unittest

from app.schemas.task import (
    CandidateRanking,
    DocumentSection,
    ExtractedField,
    Issue,
    OpeningRecord,
    ParsedDocument,
    RejectionRecord,
    ScoreDetail,
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

    def test_official_horizontal_scores_enable_score_recalculation(self) -> None:
        details = []
        for bidder, business, technical, price in (
            ("甲公司", [8, 9], [7, 8], [30, 30]),
            ("乙公司", [6, 7], [5, 6], [27, 27]),
        ):
            for expert, score in zip(("张三", "李四"), business):
                details.append(ScoreDetail(bidder=bidder, expert=expert, factor="商务标部分/业绩", raw_score=score))
            for expert, score in zip(("张三", "李四"), technical):
                details.append(ScoreDetail(bidder=bidder, expert=expert, factor="技术标部分/方案", raw_score=score))
            for expert, score in zip(("张三", "李四"), price):
                details.append(ScoreDetail(bidder=bidder, expert=expert, factor="价格标部分/投标报价", raw_score=score))
        doc = ParsedDocument(
            file_id="SCORE", filename="评标报告.pdf", file_type="pdf", document_subtype="评标报告",
            text_length=100, score_details=details,
            score_summaries=[
                ScoreSummary(bidder="甲公司", business_score=8.5, technical_score=7.5, price_score=30, total_score=46),
                ScoreSummary(bidder="乙公司", business_score=6.5, technical_score=5.5, price_score=27, total_score=39),
            ],
        )
        result = evaluation_rule_service.evaluate([], [doc], [], [])
        for rule_id in ("P3-08", "P3-10", "P3-13"):
            row = next(item for item in result["results"] if item["rule_id"] == rule_id)
            self.assertEqual(row["status"], "passed", rule_id)

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

    def test_rule_does_not_consume_same_keyword_from_wrong_agent(self) -> None:
        doc = ParsedDocument(
            file_id="F1B", filename="招标文件.pdf", file_type="pdf", text_length=100
        )
        issue = Issue(
            issue_id="I-WRONG-AGENT",
            agent="合规审查智能体",
            risk_level="高",
            issue_type="疑似限制性条款",
            description="条款中出现投标报价字样，但不是报价记录完整性问题。",
            evidence=["投标文件不得出现有选择性投标报价。"],
            requires_human_review=True,
        )
        result = evaluation_rule_service.evaluate([], [doc], [], [issue])
        row = next(item for item in result["results"] if item["rule_id"] == "P2-09")
        self.assertEqual(row["status"], "insufficient_data")
        self.assertEqual(row["issue_ids"], [])

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

    def test_project_id_is_normalized_and_compared_across_sources(self) -> None:
        doc = ParsedDocument(
            file_id="F5", filename="招标文件.pdf", file_type="pdf", text_length=100,
            extracted_fields={
                "project_id": ExtractedField(
                    value="zjwz－2025－025", raw_text="项目编号：zjwz－2025－025",
                    source_location="第 1 页",
                )
            },
        )
        result = evaluation_rule_service.evaluate(
            [], [doc], [], [], system_record={"project_id": "ZJWZ-2025-025"}
        )
        row = next(item for item in result["results"] if item["rule_id"] == "P1-03")
        self.assertEqual(row["status"], "passed")
        self.assertEqual(len(row["execution_evidence"]), 2)

        mismatch = evaluation_rule_service.evaluate(
            [], [doc], [], [], system_record={"project_id": "ZJWZ-2025-052"}
        )
        row = next(item for item in mismatch["results"] if item["rule_id"] == "P1-03")
        self.assertEqual(row["status"], "human_review")

    def test_field_presence_does_not_replace_cross_source_rule_execution(self) -> None:
        doc = ParsedDocument(
            file_id="F6", filename="正式评标报告.pdf", file_type="pdf", text_length=1000,
            document_subtype="评标报告",
            sections=[DocumentSection(
                title="评标情况",
                content=(
                    "项目名称：道路脱空检测项目\n项目编号：ZJWZ2025-025\n"
                    "开标时间：2025年7月1日9时30分\n开标地点：第一开标室\n"
                    "评标委员会共5人。资格审查全部通过。符合性审查全部通过。\n"
                    "经评审推荐第一中标候选人。评标委员会成员签字确认。"
                ),
            )],
            opening_records=[OpeningRecord(bidder="甲公司", bid_price=100.0)],
            score_details=[ScoreDetail(bidder="甲公司", expert=f"评委{i}", factor="技术", raw_score=10) for i in range(1, 6)],
        )
        result = evaluation_rule_service.evaluate([], [doc], [], [])
        statuses = {row["rule_id"]: row["status"] for row in result["results"]}
        for rule_id in ("P1-09", "P2-07", "P2-11", "P3-01", "P3-03"):
            self.assertEqual(statuses[rule_id], "passed", rule_id)
        for rule_id in ("P2-02", "P2-03"):
            self.assertEqual(statuses[rule_id], "insufficient_data", rule_id)
        self.assertEqual(statuses["P2-08"], "human_review")

    def test_rejection_record_requires_basis_review(self) -> None:
        doc = ParsedDocument(
            file_id="F7", filename="评标报告.pdf", file_type="pdf", text_length=100,
            rejection_records=[RejectionRecord(
                bidder="乙公司", reason="资格审查不通过：未提供资格证明",
                cited_clause="采购文件第三章", evidence="乙公司资格审查不通过。",
            )],
        )
        result = evaluation_rule_service.evaluate([], [doc], [], [])
        row = next(item for item in result["results"] if item["rule_id"] == "P3-02")
        self.assertEqual(row["status"], "human_review")
        self.assertFalse(row["missing_inputs"])


if __name__ == "__main__":
    unittest.main()
