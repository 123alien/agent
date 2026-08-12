import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.schemas.document_context import DocumentContext
from app.schemas.task import AgentResult, Issue, ParsedDocument


class StandaloneDocumentParserApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.headers = ({"X-API-Key": settings.agent_api_token} if settings.agent_api_token else {})
        self.request = json.dumps({
            "contract_version": "1.0.0",
            "request_id": "REQ-DOC-001",
            "project_id": "P-001",
            "input": {"project_name": "某市信息化平台项目"},
            "options": {"enable_dify": True, "enable_human_review": True, "trace_enabled": True},
        }, ensure_ascii=False)

    @patch("app.api.agents.DocumentParserAgent.run")
    def test_document_parser_returns_uniform_contract(self, mock_run) -> None:
        mock_run.return_value = (
            [ParsedDocument(
                file_id="F-1", filename="采购文件.txt", file_type="招标文件",
                text_length=20, project_name="某市信息化平台项目",
            )],
            AgentResult(agent="文档解析智能体", summary="已解析 1 个文件。"),
            {"F-1": "项目名称：某市信息化平台项目"},
        )
        response = self.client.post(
            "/api/v1/agents/document-parser",
            data={"request": self.request},
            files=[("files", ("采购文件.txt", "项目名称：某市信息化平台项目", "text/plain"))],
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["contract_version"], "1.0.0")
        self.assertEqual(payload["agent"], "document_parser")
        self.assertEqual(payload["request_id"], "REQ-DOC-001")
        self.assertEqual(payload["result"]["documents"][0]["filename"], "采购文件.txt")
        self.assertEqual(payload["result"]["document_contexts"][0]["document_id"], "F-1")
        self.assertEqual(payload["execution"]["execution_mode"], "standalone_api")
        self.assertTrue(mock_run.call_args.kwargs["enable_semantic_enhancement"])

    def test_invalid_request_uses_uniform_error_contract(self) -> None:
        response = self.client.post(
            "/api/v1/agents/document-parser",
            data={"request": "{}"},
            files=[("files", ("采购文件.txt", "text", "text/plain"))],
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["errors"][0]["code"], "INVALID_REQUEST")

    def test_openapi_exposes_multipart_endpoint(self) -> None:
        operation = self.client.get("/openapi.json").json()["paths"][
            "/api/v1/agents/document-parser"
        ]["post"]
        self.assertIn("multipart/form-data", operation["requestBody"]["content"])

    @patch("app.api.agents.ComplianceCheckerAgent.run_contexts")
    def test_compliance_review_consumes_parser_contract(self, mock_run) -> None:
        document = ParsedDocument(
            file_id="F-1", filename="采购文件.txt", file_type="招标文件",
            text_length=20, project_name="某市信息化平台项目",
        )
        context = DocumentContext(
            document_id="F-1", file_name="采购文件.txt",
            file_hash="a" * 64, document_type="招标文件",
            raw_text="投标人注册地址必须位于本市。",
        )
        mock_run.return_value = AgentResult(
            agent="合规审查智能体",
            summary="发现 1 项待复核问题。",
            issues=[Issue(
                issue_id="C-1", agent="合规审查智能体", risk_level="高",
                issue_type="地域限制", description="注册地址限制需审查",
                evidence=["投标人注册地址必须位于本市。"],
                requires_human_review=True,
            )],
        )
        payload = {
            "contract_version": "1.0.0",
            "request_id": "REQ-COM-001",
            "project_id": "P-001",
            "input": {
                "documents": [document.model_dump(mode="json")],
                "document_contexts": [context.model_dump(mode="json")],
                "system_record": {},
            },
            "options": {"enable_dify": False, "enable_human_review": True, "trace_enabled": True},
        }
        response = self.client.post(
            "/api/v1/agents/compliance-review", json=payload, headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["agent"], "compliance_review")
        self.assertEqual(result["findings"][0]["final_status"], "human_review")
        self.assertEqual(result["findings"][0]["evidence"][0]["quote"], "投标人注册地址必须位于本市。")
        self.assertFalse(mock_run.call_args.kwargs["enable_dify"])

    def test_compliance_review_rejects_missing_context(self) -> None:
        payload = {
            "request_id": "REQ-COM-002", "project_id": "P-001",
            "input": {"documents": [{}]},
        }
        response = self.client.post(
            "/api/v1/agents/compliance-review", json=payload, headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["code"], "INVALID_REQUEST")

    @patch("app.api.agents.DataValidatorAgent.run_contexts")
    def test_data_verification_reuses_context_and_returns_findings(self, mock_run) -> None:
        document = ParsedDocument(
            file_id="F-SCORE", filename="评分汇总表.xlsx", file_type="评审评分表",
            text_length=50, project_name="某市信息化平台项目",
        )
        context = DocumentContext(
            document_id="F-SCORE", file_name="评分汇总表.xlsx",
            file_hash="b" * 64, document_type="评审评分表",
            raw_text="甲公司技术得分45分，总分60分。",
        )
        mock_run.return_value = AgentResult(
            agent="数据核验智能体",
            summary="发现 1 项计算问题。",
            issues=[Issue(
                issue_id="D-1", agent="数据核验智能体", risk_level="高",
                issue_type="总分复算错误", description="分项合计与总分不一致",
                evidence=["甲公司技术得分45分，总分60分。"],
                requires_human_review=True,
            )],
            data={"execution_mode": "deterministic"},
        )
        payload = {
            "request_id": "REQ-DATA-001", "project_id": "P-001",
            "input": {
                "documents": [document.model_dump(mode="json")],
                "document_contexts": [context.model_dump(mode="json")],
            },
            "options": {"enable_dify": False, "enable_human_review": True, "trace_enabled": True},
        }
        response = self.client.post(
            "/api/v1/agents/data-verification", json=payload, headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["agent"], "data_verification")
        self.assertEqual(result["findings"][0]["finding_type"], "总分复算错误")
        self.assertEqual(result["result"]["verified_document_ids"], ["F-SCORE"])
        self.assertFalse(mock_run.call_args.kwargs["enable_dify"])

    def test_data_verification_rejects_mismatched_document_ids(self) -> None:
        document = ParsedDocument(
            file_id="F-1", filename="评分表.xlsx", file_type="评审评分表",
            text_length=10,
        )
        context = DocumentContext(
            document_id="F-2", file_name="评分表.xlsx", file_hash="c" * 64,
            document_type="评审评分表", raw_text="评分表",
        )
        payload = {
            "request_id": "REQ-DATA-002", "project_id": "P-001",
            "input": {
                "documents": [document.model_dump(mode="json")],
                "document_contexts": [context.model_dump(mode="json")],
            },
        }
        response = self.client.post(
            "/api/v1/agents/data-verification", json=payload, headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["code"], "INVALID_REQUEST")

    @patch("app.api.agents.AnomalyAnalyzerAgent.run_contexts")
    def test_anomaly_analysis_accepts_upstream_responses(self, mock_run) -> None:
        document = ParsedDocument(
            file_id="F-1", filename="响应文件.pdf", file_type="投标文件",
            document_subtype="响应文件", text_length=20,
        )
        context = DocumentContext(
            document_id="F-1", file_name="响应文件.pdf", file_hash="d" * 64,
            document_type="投标文件", raw_text="联系电话13800001111",
        )
        mock_run.return_value = AgentResult(
            agent="异常分析智能体", summary="发现 1 项异常线索。",
            issues=[Issue(
                issue_id="A-001", agent="异常分析智能体", risk_level="高",
                issue_type="多信号组合异常", description="多个独立信号重合。",
                evidence=["13800001111"], requires_human_review=True,
            )],
        )
        upstream = {
            "contract_version": "1.0.0", "request_id": "REQ-UP-1",
            "agent": "compliance_review", "status": "completed", "summary": "完成",
            "result": {}, "findings": [], "warnings": [], "errors": [], "execution": {},
        }
        payload = {
            "request_id": "REQ-ANOMALY-001", "project_id": "P-001",
            "input": {
                "documents": [document.model_dump(mode="json")],
                "document_contexts": [context.model_dump(mode="json")],
                "upstream_responses": [upstream],
                "relationship_data": {"network_features": [{"ip": "203.0.113.10"}]},
            },
            "options": {"enable_dify": False},
        }
        response = self.client.post(
            "/api/v1/agents/anomaly-analysis", json=payload, headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["agent"], "anomaly_analysis")
        self.assertEqual(body["findings"][0]["final_status"], "human_review")
        args, kwargs = mock_run.call_args
        self.assertEqual(args[2][0].agent, "合规审查智能体")
        self.assertFalse(kwargs["enable_dify"])
        self.assertIn("network_features", kwargs["relationship_data"])

    def test_anomaly_analysis_requires_relationship_data(self) -> None:
        document = ParsedDocument(
            file_id="F-1", filename="响应文件.pdf", file_type="投标文件", text_length=10,
        )
        context = DocumentContext(
            document_id="F-1", file_name="响应文件.pdf", file_hash="e" * 64,
            document_type="投标文件", raw_text="响应文件",
        )
        response = self.client.post("/api/v1/agents/anomaly-analysis", json={
            "request_id": "REQ-ANOMALY-002", "project_id": "P-001",
            "input": {
                "documents": [document.model_dump(mode="json")],
                "document_contexts": [context.model_dump(mode="json")],
            },
        }, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertIn("relationship_data", response.json()["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
