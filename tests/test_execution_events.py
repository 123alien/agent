import unittest
from unittest.mock import patch

from app.api.tasks import _progress_callback, build_task
from app.schemas.task import TaskRecord


class ExecutionEventTests(unittest.TestCase):
    def test_progress_callback_persists_latest_context_and_timeline(self) -> None:
        task = TaskRecord(
            task_id="TRACE1",
            project_id="P1",
            project_name="执行轨迹测试",
            check_type="full",
            status="running",
            created_at="2026-08-12T00:00:00+08:00",
            updated_at="2026-08-12T00:00:00+08:00",
        )
        event = {
            "agent": "文档解析智能体",
            "node": "document_parser",
            "status": "running",
            "goal": "解析文档",
            "tools": ["OCR"],
            "finding": "正在读取文件",
            "decision": "完成后进入总控路由",
            "review_reason": "",
        }

        with patch("app.api.tasks.task_store.save") as save:
            _progress_callback(task)(event)

        self.assertEqual(len(task.execution_events), 1)
        self.assertEqual(task.execution_context["node"], "document_parser")
        self.assertIn("timestamp", task.execution_context)
        save.assert_called_once_with(task)

    def test_new_task_freezes_execution_versions(self) -> None:
        task = build_task("T1", "P1", "版本测试", "full", [])
        self.assertIn("model", task.execution_metadata)
        self.assertIn("ruleset_version", task.execution_metadata)
        self.assertEqual(
            set(task.execution_metadata["workflows"]),
            {"compliance", "data_validation", "anomaly_analysis", "report_generation"},
        )

    def test_execution_metadata_is_serializable_in_task_contract(self) -> None:
        task = build_task("T2", "P2", "序列化测试", "auto", [])
        restored = TaskRecord.model_validate_json(task.model_dump_json())
        self.assertEqual(restored.execution_metadata, task.execution_metadata)
        self.assertEqual(restored.review_audit, [])


if __name__ == "__main__":
    unittest.main()
