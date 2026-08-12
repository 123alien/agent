"""Run all five standalone agents against one or more local documents."""

import argparse
import json
import mimetypes
import uuid
from pathlib import Path

import httpx


def envelope(project_id: str, task_id: str, payload: dict, enable_dify: bool) -> dict:
    return {
        "contract_version": "1.0.0",
        "request_id": f"REQ-{uuid.uuid4().hex[:12]}",
        "project_id": project_id,
        "task_id": task_id,
        "input": payload,
        "options": {
            "enable_dify": enable_dify,
            "enable_human_review": True,
            "trace_enabled": True,
        },
    }


def check(response: httpx.Response) -> dict:
    response.raise_for_status()
    body = response.json()
    if body.get("status") == "failed":
        raise RuntimeError(json.dumps(body.get("errors", []), ensure_ascii=False))
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the five-agent standalone pipeline")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--project-id", default="P-DEMO-001")
    parser.add_argument("--project-name", default="招投标智能核验演示项目")
    parser.add_argument("--system-record", type=Path)
    parser.add_argument("--relationship-data", type=Path)
    parser.add_argument("--enable-dify", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("data/standalone-result.json"))
    args = parser.parse_args()

    for path in args.files:
        if not path.is_file():
            parser.error(f"文件不存在：{path}")
    system_record = json.loads(args.system_record.read_text("utf-8")) if args.system_record else {}
    relationship_data = (
        json.loads(args.relationship_data.read_text("utf-8"))
        if args.relationship_data else {}
    )
    task_id = f"PIPE-{uuid.uuid4().hex[:12]}"
    headers = {"X-API-Key": args.api_key} if args.api_key else {}

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=300) as client:
        request = envelope(
            args.project_id, task_id, {"project_name": args.project_name}, args.enable_dify
        )
        opened = [path.open("rb") for path in args.files]
        try:
            files = [
                ("files", (path.name, stream, mimetypes.guess_type(path.name)[0] or "application/octet-stream"))
                for path, stream in zip(args.files, opened)
            ]
            parsed = check(client.post(
                "/api/v1/agents/document-parser",
                data={"request": json.dumps(request, ensure_ascii=False)}, files=files,
            ))
        finally:
            for stream in opened:
                stream.close()

        common = {
            "documents": parsed["result"]["documents"],
            "document_contexts": parsed["result"]["document_contexts"],
        }
        compliance = check(client.post("/api/v1/agents/compliance-review", json=envelope(
            args.project_id, task_id, {**common, "system_record": system_record}, args.enable_dify
        )))
        validation = check(client.post("/api/v1/agents/data-verification", json=envelope(
            args.project_id, task_id, common, args.enable_dify
        )))
        anomaly = check(client.post("/api/v1/agents/anomaly-analysis", json=envelope(
            args.project_id, task_id,
            {**common, "upstream_responses": [compliance, validation],
             "relationship_data": relationship_data}, args.enable_dify,
        )))
        report = check(client.post("/api/v1/agents/report-generator", json=envelope(
            args.project_id, task_id,
            {"project_name": args.project_name, "documents": common["documents"],
             "upstream_responses": [parsed, compliance, validation, anomaly],
             "human_review_results": {}, "output_type": "综合智能核验报告",
             "template_type": "标准审查报告"}, args.enable_dify,
        )))

    result = {
        "task_id": task_id,
        "agents": {item["agent"]: item for item in [parsed, compliance, validation, anomaly, report]},
        "report_files": report["result"]["report_files"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(f"completed task_id={task_id}")
    for name, url in result["report_files"].items():
        print(f"{name}: {args.base_url.rstrip('/')}{url}")
    print(f"result: {args.output.resolve()}")


if __name__ == "__main__":
    main()
