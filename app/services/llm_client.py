import json

import httpx

from app.core.config import settings


class LLMClient:
    def __init__(self) -> None:
        self.enabled = bool(settings.llm_api_key)

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict | None:
        if not self.enabled:
            return None

        url = f"{settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(
            timeout=settings.llm_timeout_seconds,
            proxy=settings.llm_proxy or None,
            trust_env=False,
        ) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)


llm_client = LLMClient()
