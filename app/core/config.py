import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "招投标智能核验智能体服务")
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data")).resolve()
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    llm_proxy: str = os.getenv("LLM_PROXY", "")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    remote_file_timeout_seconds: int = int(
        os.getenv("REMOTE_FILE_TIMEOUT_SECONDS", "30")
    )
    remote_file_max_bytes: int = int(
        os.getenv("REMOTE_FILE_MAX_BYTES", str(50 * 1024 * 1024))
    )
    remote_file_allowed_hosts: tuple[str, ...] = tuple(
        host.strip().lower()
        for host in os.getenv("REMOTE_FILE_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )
    callback_timeout_seconds: int = int(os.getenv("CALLBACK_TIMEOUT_SECONDS", "10"))
    callback_allowed_hosts: tuple[str, ...] = tuple(
        host.strip().lower()
        for host in os.getenv("CALLBACK_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def reviews_dir(self) -> Path:
        return self.data_dir / "reviews"


settings = Settings()


def ensure_data_dirs() -> None:
    for path in [
        settings.data_dir,
        settings.uploads_dir,
        settings.reports_dir,
        settings.tasks_dir,
        settings.reviews_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
