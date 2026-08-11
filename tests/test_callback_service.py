import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from app.schemas.task import TaskRecord
from app.services import callback_service


def _task() -> TaskRecord:
    return TaskRecord(
        task_id="Tcallback001",
        project_id="P001",
        project_name="回调测试项目",
        check_type="auto",
        status="completed",
        callback_url="https://business.example.com/api/callback",
        created_at="2026-08-11T10:00:00+08:00",
        updated_at="2026-08-11T10:01:00+08:00",
    )


class CallbackServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            callback_allowed_hosts=("business.example.com",),
            callback_timeout_seconds=5,
            callback_max_attempts=3,
            callback_retry_base_seconds=0,
            callback_secret="callback-test-secret",
        )

    @patch("app.services.callback_service.time.time", return_value=1770000000)
    @patch("app.services.callback_service.time.sleep")
    @patch("app.services.callback_service.httpx.post")
    def test_retries_and_signs_exact_payload(self, post: Mock, sleep: Mock, _time: Mock) -> None:
        failed = Mock()
        failed.raise_for_status.side_effect = httpx.HTTPStatusError(
            "temporary failure",
            request=httpx.Request("POST", "https://business.example.com/api/callback"),
            response=httpx.Response(503),
        )
        succeeded = Mock()
        succeeded.raise_for_status.return_value = None
        post.side_effect = [failed, succeeded]

        with patch.object(callback_service, "settings", self.settings):
            attempts = callback_service.send_task_callback(_task())

        self.assertEqual(attempts, 2)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0)
        payload = post.call_args.kwargs["content"]
        headers = post.call_args.kwargs["headers"]
        expected = hmac.new(
            b"callback-test-secret",
            b"1770000000." + payload,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers["X-Agent-Signature"], f"sha256={expected}")
        self.assertEqual(headers["X-Agent-Event"], "task.completed")
        self.assertEqual(json.loads(payload)["task_id"], "Tcallback001")

    def test_rejects_callback_host_outside_allowlist(self) -> None:
        task = _task().model_copy(update={"callback_url": "https://evil.example/callback"})
        with patch.object(callback_service, "settings", self.settings):
            with self.assertRaisesRegex(ValueError, "不允许访问回调主机"):
                callback_service.send_task_callback(task)


if __name__ == "__main__":
    unittest.main()
