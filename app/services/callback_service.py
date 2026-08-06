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


def send_task_callback(task: TaskRecord) -> None:
    if not task.callback_url:
        return
    _validate_callback_url(task.callback_url)
    response = httpx.post(
        task.callback_url,
        json=task.model_dump(mode="json"),
        timeout=settings.callback_timeout_seconds,
    )
    response.raise_for_status()
