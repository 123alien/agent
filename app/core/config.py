import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "招投标智能核验智能体服务")
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data")).resolve()
    agent_api_token: str = os.getenv("AGENT_API_TOKEN", "")
    cors_allowed_origins: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:8000").split(",")
        if value.strip()
    )
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    llm_proxy: str = os.getenv("LLM_PROXY", "")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    dify_base_url: str = os.getenv("DIFY_BASE_URL", "").rstrip("/")
    dify_compliance_api_key: str = os.getenv(
        "DIFY_COMPLIANCE_API_KEY",
        os.getenv("DIFY_API_KEY", ""),
    )
    dify_document_parser_api_key: str = os.getenv(
        "DIFY_DOCUMENT_PARSER_API_KEY",
        "",
    )
    dify_data_validator_api_key: str = os.getenv(
        "DIFY_DATA_VALIDATOR_API_KEY",
        "",
    )
    dify_anomaly_analyzer_api_key: str = os.getenv(
        "DIFY_ANOMALY_ANALYZER_API_KEY",
        "",
    )
    dify_report_generator_api_key: str = os.getenv(
        "DIFY_REPORT_GENERATOR_API_KEY",
        "",
    )
    dify_timeout_seconds: int = int(os.getenv("DIFY_TIMEOUT_SECONDS", "120"))
    dify_document_parser_timeout_seconds: int = int(
        os.getenv("DIFY_DOCUMENT_PARSER_TIMEOUT_SECONDS", "45")
    )
    rag_cache_ttl_seconds: int = int(os.getenv("RAG_CACHE_TTL_SECONDS", "604800"))
    visual_analysis_enabled: bool = os.getenv("VISUAL_ANALYSIS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    visual_analysis_max_pages: int = int(os.getenv("VISUAL_ANALYSIS_MAX_PAGES", "100"))
    visual_review_threshold: float = float(os.getenv("VISUAL_REVIEW_THRESHOLD", "0.80"))
    visual_detector_model_path: str = os.getenv("VISUAL_DETECTOR_MODEL_PATH", "")
    compliance_workflow_version: str = os.getenv(
        "COMPLIANCE_WORKFLOW_VERSION", "1.0.0"
    )
    data_validator_workflow_version: str = os.getenv(
        "DATA_VALIDATOR_WORKFLOW_VERSION", "1.0.0"
    )
    anomaly_analyzer_workflow_version: str = os.getenv(
        "ANOMALY_ANALYZER_WORKFLOW_VERSION", "1.0.0"
    )
    report_generator_workflow_version: str = os.getenv(
        "REPORT_GENERATOR_WORKFLOW_VERSION", "1.0.0"
    )
    ruleset_version: str = os.getenv("RULESET_VERSION", "1.0.0")
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
    def contracts_dir(self) -> Path:
        return self.data_dir / "contracts"

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def reviews_dir(self) -> Path:
        return self.data_dir / "reviews"

    @property
    def graph_checkpoint_path(self) -> Path:
        return self.data_dir / "langgraph_checkpoints.sqlite"

    @property
    def workflow_cache_path(self) -> Path:
        return self.data_dir / "workflow_cache.sqlite"


settings = Settings()


def ensure_data_dirs() -> None:
    for path in [
        settings.data_dir,
        settings.uploads_dir,
        settings.reports_dir,
        settings.contracts_dir,
        settings.tasks_dir,
        settings.reviews_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
