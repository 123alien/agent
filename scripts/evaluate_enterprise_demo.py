"""Deterministically evaluate a completed enterprise-demo task."""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python scripts/evaluate_enterprise_demo.py data/tasks/Txxxx.json")
        return 2
    task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    expected = json.loads(Path("test_data/enterprise_demo/expected_findings.json").read_text(encoding="utf-8"))
    issues = (task.get("result") or {}).get("issues") or task.get("review_request", {}).get("issues", [])
    agent_results = (task.get("result") or {}).get("agent_results") or task.get("review_request", {}).get("agent_results", [])
    parsed_documents = (task.get("result") or {}).get("parsed_documents") or task.get("review_request", {}).get("parsed_documents", [])
    corpus = json.dumps(
        {"issues": issues, "agent_results": agent_results, "parsed_documents": parsed_documents},
        ensure_ascii=False,
    )
    checks = []
    for finding in expected["expected_findings"]:
        signals = [str(value) for value in finding.get("signals", [])]
        status = finding.get("expected_status")
        statuses = status if isinstance(status, list) else ([status] if status else [])
        checks.append({
            "id": finding["id"],
            "passed": (not signals or any(value in corpus for value in signals))
            and (not statuses or any(f'"detection_status": "{value}"' in corpus for value in statuses)),
        })
    passed = sum(item["passed"] for item in checks)
    result = {
        "dataset_version": expected["dataset_version"],
        "task_id": task.get("task_id", ""),
        "expected_count": len(checks),
        "passed_count": passed,
        "recall": round(passed / len(checks), 4) if checks else 1.0,
        "evidence_coverage": round(sum(bool(item.get("evidence")) for item in issues) / len(issues), 4) if issues else 1.0,
        "human_review_coverage": round(sum(bool(item.get("requires_human_review")) for item in issues) / len(issues), 4) if issues else 1.0,
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
