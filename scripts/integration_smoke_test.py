from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.file_helpers import infer_file_type
from app.main import app
from app.core.config import settings
from app.schemas.task import RemoteFileInput, UploadedFileInfo


async def fake_download(
    item: RemoteFileInput,
    target_dir: Path,
) -> UploadedFileInfo:
    source = ROOT / "samples" / "demo_tender.txt"
    target = target_dir / "FREMOTE_demo_tender.txt"
    target.write_bytes(source.read_bytes())
    return UploadedFileInfo(
        file_id="FREMOTE",
        filename=item.filename,
        file_type=item.file_type or infer_file_type(item.filename),
        saved_path=str(target),
        source_url=item.url,
    )


def main() -> None:
    client = TestClient(app)
    headers = {"X-API-Key": settings.agent_api_token} if settings.agent_api_token else {}
    payload = {
        "project_id": "P-INTEGRATION-001",
        "project_name": "远程文件接入测试项目",
        "check_type": "full",
        "files": [
            {
                "url": "https://files.example.com/demo_tender.txt",
                "filename": "demo_tender.txt",
                "file_type": "招标文件",
            }
        ],
    }
    with patch("app.api.tasks.download_remote_file", new=fake_download):
        response = client.post("/api/agent/tasks/from-urls", json=payload, headers=headers)

    response.raise_for_status()
    created_task = response.json()
    task_response = client.get(f"/api/agent/tasks/{created_task['task_id']}", headers=headers)
    task_response.raise_for_status()
    task = task_response.json()
    if task["status"] == "waiting_review":
        review_response = client.post(
            f"/api/agent/tasks/{task['task_id']}/review",
            json={
                "reviewer": "integration-smoke-test",
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
        task = client.get(f"/api/agent/tasks/{task['task_id']}", headers=headers).json()
    assert task["status"] == "completed"
    assert task["files"][0]["source_url"] == payload["files"][0]["url"]
    assert task["result"] is not None
    print("task_id:", task["task_id"])
    print("status:", task["status"])
    print("issues:", len(task["result"]["issues"]))
    print("url integration: ok")


if __name__ == "__main__":
    main()
