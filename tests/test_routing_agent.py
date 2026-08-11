import unittest

from app.agents.routing_agent import RoutingAgent
from app.schemas.task import ParsedDocument


class RoutingAgentTests(unittest.TestCase):
    def test_unfilled_template_only_runs_document_parser(self) -> None:
        document = ParsedDocument(
            file_id="F001",
            filename="政府采购公开招标文件示范文本.docx",
            file_type="招标文件",
            text_length=100,
        )
        text = "□是 □否 年 月 日 （如有） 填写说明 □是 □否"

        decision = RoutingAgent().plan([document], {"F001": text})

        self.assertEqual(decision.selected_agents, [])
        self.assertIn("示范模板", decision.reasons[0])

    def test_multiple_business_bid_documents_trigger_anomaly_analysis(self) -> None:
        documents = [
            ParsedDocument(
                file_id="F001",
                filename="供应商甲.txt",
                file_type="业务文件",
                text_length=100,
            ),
            ParsedDocument(
                file_id="F002",
                filename="供应商乙.txt",
                file_type="业务文件",
                text_length=100,
            ),
        ]
        texts = {
            "F001": "甲公司投标文件\n投标报价：980000元\n联系人电话：13800001111",
            "F002": "乙公司投标文件\n投标报价：990000元\n联系人电话：13800001111",
        }

        decision = RoutingAgent().plan(documents, texts)

        self.assertIn("anomaly", decision.selected_agents)
        self.assertTrue(any("多份投标/响应文件" in reason for reason in decision.reasons))


if __name__ == "__main__":
    unittest.main()
