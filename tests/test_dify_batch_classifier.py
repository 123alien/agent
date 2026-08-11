import json
import unittest

from dify.compliance_batch_classifier import main


class DifyBatchClassifierTests(unittest.TestCase):
    def test_routes_scoring_before_geographic_qualification_keywords(self) -> None:
        result = main(
            [
                {
                    "evidence": "投标人注册地位于本市的得10分，外省不得分。",
                    "issue_type_hint": "差别评分",
                    "search_query": "注册地评分差别待遇",
                }
            ]
        )

        self.assertEqual(len(json.loads(result["scoring_candidates"])), 1)
        self.assertEqual(json.loads(result["qualification_candidates"]), [])

    def test_deduplicates_and_routes_major_compliance_topics(self) -> None:
        candidates = [
            {
                "evidence": "投标人注册地址必须位于本市。",
                "issue_type_hint": "地域限制",
                "search_query": "注册地址限制",
            },
            {
                "evidence": "指定某品牌产品。",
                "issue_type_hint": "指定品牌",
                "search_query": "指定品牌",
            },
            {
                "evidence": "招标人有权调整中标候选人顺序且无需说明理由。",
                "issue_type_hint": "不当权限",
                "search_query": "调整中标候选人顺序",
            },
        ]
        result = main({"candidates": candidates + [candidates[0]]})

        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(len(json.loads(result["qualification_candidates"])), 1)
        self.assertEqual(len(json.loads(result["technical_candidates"])), 1)
        self.assertEqual(len(json.loads(result["procedure_candidates"])), 1)


if __name__ == "__main__":
    unittest.main()
