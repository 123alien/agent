import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings


def yes(value: bool) -> str:
    return "configured" if value else "not configured (local fallback)"


print(f"service={settings.app_name}")
print(f"data_dir={settings.data_dir}")
print(f"api_auth={yes(bool(settings.agent_api_token))}")
print(f"llm={yes(bool(settings.llm_api_key))} model={settings.llm_model}")
print(f"dify_base_url={settings.dify_base_url or 'not configured'}")
print(f"dify_document_parser={yes(bool(settings.dify_document_parser_api_key))}")
print(f"dify_compliance={yes(bool(settings.dify_compliance_api_key))}")
print(f"dify_data_validator={yes(bool(settings.dify_data_validator_api_key))}")
print(f"dify_anomaly_analyzer={yes(bool(settings.dify_anomaly_analyzer_api_key))}")
print(f"dify_report_generator={yes(bool(settings.dify_report_generator_api_key))}")
print(f"visual_analysis={settings.visual_analysis_enabled}")

problems = []
if settings.agent_api_token and len(settings.agent_api_token) < 32:
    problems.append("AGENT_API_TOKEN should contain at least 32 characters")
if settings.dify_base_url and not settings.dify_base_url.rstrip("/").endswith("/v1"):
    problems.append("DIFY_BASE_URL should normally end with /v1")
if settings.dify_document_parser_api_key and not settings.dify_base_url:
    problems.append("Dify API key is configured but DIFY_BASE_URL is empty")

if problems:
    print("configuration_warnings:")
    for problem in problems:
        print(f"- {problem}")
    raise SystemExit(1)
print("configuration_check=passed")
