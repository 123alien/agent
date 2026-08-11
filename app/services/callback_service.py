import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.schemas.task import TaskRecord


def _validate_callback_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("回调地址必须是有效的 HTTP 或 HTTPS URL")
    hostname = parsed.hostname.lower()
    if settings.callback_allowed_hosts and hostname not in settings.callback_allowed_hosts:
        raise ValueError(f"不允许访问回调主机: {hostname}")


def _callback_headers(payload: bytes, timestamp: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Timestamp": timestamp,
        "X-Agent-Event": "task.completed" if task_status_is_success(payload) else "task.failed",
    }
    if settings.callback_secret:
        digest = hmac.new(
            settings.callback_secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + payload,
            hashlib.sha256,
        ).hexdigest()
        headers["X-Agent-Signature"] = f"sha256={digest}"
    return headers


def task_status_is_success(payload: bytes) -> bool:
    try:
        return json.loads(payload).get("status") == "completed"
    except (json.JSONDecodeError, AttributeError):
        return False


def send_task_callback(task: TaskRecord) -> int:
    if not task.callback_url:
        return 0
    _validate_callback_url(task.callback_url)
    payload = json.dumps(
        task.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, settings.callback_max_attempts + 1):
        timestamp = str(int(time.time()))
        try:
            response = httpx.post(
                task.callback_url,
                content=payload,
                headers=_callback_headers(payload, timestamp),
                timeout=settings.callback_timeout_seconds,
            )
            response.raise_for_status()
            return attempt
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < settings.callback_max_attempts:
                time.sleep(settings.callback_retry_base_seconds * (2 ** (attempt - 1)))
    raise RuntimeError(
        f"回调在 {settings.callback_max_attempts} 次尝试后仍失败: {last_error}"
    ) from last_error
