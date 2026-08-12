import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from app.api.file_helpers import infer_file_type, safe_storage_name
from app.core.config import settings
from app.schemas.task import RemoteFileInput, UploadedFileInfo


class RemoteFileError(ValueError):
    pass


def validate_http_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteFileError("文件地址必须是有效的 HTTP 或 HTTPS URL")
    hostname = parsed.hostname.lower()
    if allowed_hosts and hostname not in allowed_hosts:
        raise RemoteFileError(f"不允许访问文件主机: {hostname}")


def infer_remote_filename(item: RemoteFileInput) -> str:
    if item.filename.strip():
        return Path(item.filename.strip()).name
    name = Path(unquote(urlparse(item.url).path)).name
    return name or "remote_file"


async def download_remote_file(
    item: RemoteFileInput,
    target_dir: Path,
) -> UploadedFileInfo:
    validate_http_url(item.url, settings.remote_file_allowed_hosts)
    display_name = infer_remote_filename(item)
    file_id = f"F{uuid.uuid4().hex[:10]}"
    saved_path = target_dir / f"{file_id}_{safe_storage_name(display_name)}"

    try:
        timeout = httpx.Timeout(settings.remote_file_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", item.url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > settings.remote_file_max_bytes:
                    raise RemoteFileError("远程文件超过大小限制")

                total = 0
                with saved_path.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > settings.remote_file_max_bytes:
                            raise RemoteFileError("远程文件超过大小限制")
                        output.write(chunk)
    except RemoteFileError:
        saved_path.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, ValueError) as exc:
        saved_path.unlink(missing_ok=True)
        raise RemoteFileError(f"下载远程文件失败: {display_name}") from exc

    return UploadedFileInfo(
        file_id=file_id,
        filename=display_name,
        file_type=item.file_type or infer_file_type(display_name),
        document_role=item.document_role or "other",
        saved_path=str(saved_path),
        source_url=item.url,
    )
