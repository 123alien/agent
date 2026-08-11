import unittest

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.headers = (
            {"X-API-Key": settings.agent_api_token} if settings.agent_api_token else {}
        )

    def test_health_exposes_frozen_api_version(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api_version"], "1.0.0")

    def test_capabilities_describe_all_five_agents(self) -> None:
        response = self.client.get("/api/agent/capabilities", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["api_version"], "1.0.0")
        self.assertEqual(payload["agent_contract_version"], "1.0.0")
        self.assertEqual(len(payload["agents"]), 5)
        self.assertEqual(
            {item["id"] for item in payload["agents"]},
            {"document", "compliance", "data", "anomaly", "report"},
        )
        self.assertIn("docx", payload["report_formats"])
        self.assertIn("pdf", payload["report_formats"])

    def test_openapi_version_matches_contract(self) -> None:
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["info"]["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
