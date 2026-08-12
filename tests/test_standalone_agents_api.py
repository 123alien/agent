import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.schemas.task import AgentResult, ParsedDocument


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


if __name__ == "__main__":
    unittest.main()
