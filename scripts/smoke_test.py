from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app
from app.core.config import settings


def main() -> None:
    client = TestClient(app)
    headers = {"X-API-Key": settings.agent_api_token} if settings.agent_api_token else {}
    sample_path = Path("samples/demo_tender.txt")
    response = client.post(
        "/api/agent/tasks",
        data={
            "project_id": "P-DEMO-001",
            "project_name": "某学校智慧教室设备采购项目",
            "check_type": "full",
        },
        files=[
            (
                "files",
                (
                    sample_path.name,
                    sample_path.read_bytes(),
                    "text/plain",
                ),
            )
        ],
        headers=headers,
    )
    response.raise_for_status()
    task_id = response.json()["task_id"]

    task_response = client.get(f"/api/agent/tasks/{task_id}", headers=headers)
    task_response.raise_for_status()
    task = task_response.json()

    if task["status"] == "waiting_review":
        review_response = client.post(
            f"/api/agent/tasks/{task_id}/review",
            json={
                "reviewer": "smoke-test",
                "items": [
                    {
                        "issue_id": issue["issue_id"],
                        "decision": "正确",
                    }
                    for issue in task["review_request"]["issues"]
                ],
            },
            headers=headers,
        )
        review_response.raise_for_status()
        task = client.get(f"/api/agent/tasks/{task_id}", headers=headers).json()

    print("task_id:", task_id)
    print("status:", task["status"])
    print("summary:", task["result"]["summary"])
    print("issues:", len(task["result"]["issues"]))
    for issue in task["result"]["issues"]:
        print("-", issue["agent"], issue["risk_level"], issue["issue_type"])

    report_response = client.get(f"/api/agent/tasks/{task_id}/report", headers=headers)
    report_response.raise_for_status()
    print("report:", f"data/reports/{task_id}.md")


if __name__ == "__main__":
    main()
