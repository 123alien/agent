import json

import httpx

from app.core.config import settings


class DifyWorkflowError(RuntimeError):
    pass


class DifyClient:
    def __init__(self) -> None:
        self.enabled = bool(
            settings.dify_base_url and settings.dify_compliance_api_key
        )

    def _run_workflow(
        self,
        inputs: dict,
        user: str,
        api_key: str,
        output_names: tuple[str, ...] = ("result",),
        timeout_seconds: int | None = None,
    ) -> dict:
        if not settings.dify_base_url or not api_key:
            raise DifyWorkflowError("Dify Workflow 未配置")

        url = f"{settings.dify_base_url}/workflows/run"
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": user,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(
                timeout=timeout_seconds or settings.dify_timeout_seconds,
                trust_env=False,
            ) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DifyWorkflowError(f"Dify Workflow 请求失败: {exc}") from exc

        body = response.json()
        if body.get("data", {}).get("status") != "succeeded":
            error = body.get("data", {}).get("error") or "工作流未成功完成"
            raise DifyWorkflowError(f"Dify Workflow 执行失败: {error}")

        outputs = body.get("data", {}).get("outputs", {})
        result = next(
            (outputs[name] for name in output_names if name in outputs),
            None,
        )
        if isinstance(result, dict):
            return result
        if not isinstance(result, str):
            raise DifyWorkflowError("Dify Workflow 缺少 result 输出")

        try:
            parsed = json.loads(result)
        except json.JSONDecodeError as exc:
            raise DifyWorkflowError("Dify Workflow result 不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise DifyWorkflowError("Dify Workflow result 必须是 JSON 对象")
        return parsed

    def run_document(self, document_text: str, user: str) -> dict:
        return self._run_workflow(
            {"document_text": document_text},
            user,
            settings.dify_compliance_api_key,
        )

    @property
    def document_parser_enabled(self) -> bool:
        return bool(
            settings.dify_base_url
            and settings.dify_document_parser_api_key
        )

    def run_document_semantic_parser(
        self,
        document_text: str,
        document_type: str,
        parser_context: str,
        requested_fields: str,
        include_sections: str,
        user: str,
    ) -> dict:
        return self._run_workflow(
            {
                "document_text": document_text,
                "document_type": document_type,
                "parser_context": parser_context,
                "requested_fields": requested_fields,
                "include_sections": include_sections,
            },
            user,
            settings.dify_document_parser_api_key,
            output_names=("result", "structured_output"),
            timeout_seconds=settings.dify_document_parser_timeout_seconds,
        )

    @property
    def data_validator_enabled(self) -> bool:
        return bool(settings.dify_base_url and settings.dify_data_validator_api_key)

    def run_data_validator(
        self,
        document_text: str,
        parsed_document: str,
        validation_context: str,
        user: str,
    ) -> dict:
        inputs = {
            "document_text": document_text,
            "parsed_document": parsed_document,
        }
        if settings.data_validator_workflow_version.startswith("2."):
            inputs["validation_context"] = validation_context
        return self._run_workflow(
            inputs,
            user,
            settings.dify_data_validator_api_key,
            output_names=("structured_output", "result"),
        )

    @property
    def anomaly_analyzer_enabled(self) -> bool:
        return bool(settings.dify_base_url and settings.dify_anomaly_analyzer_api_key)

    def run_anomaly_analyzer(
        self,
        parsed_documents: str,
        compliance_results: str,
        validation_results: str,
        relationship_data: str,
        anomaly_context: str,
        user: str,
    ) -> dict:
        inputs = {
            "parsed_documents": parsed_documents,
            "compliance_results": compliance_results,
            "validation_results": validation_results,
            "relationship_data": relationship_data,
        }
        if settings.anomaly_analyzer_workflow_version.startswith("2."):
            inputs["anomaly_context"] = anomaly_context
        return self._run_workflow(
            inputs,
            user,
            settings.dify_anomaly_analyzer_api_key,
            output_names=("structured_output", "result"),
        )

    @property
    def report_generator_enabled(self) -> bool:
        return bool(settings.dify_base_url and settings.dify_report_generator_api_key)

    def run_report_generator(
        self,
        output_type: str,
        project_info: dict,
        parsed_documents: str,
        compliance_results: str,
        validation_results: str,
        anomaly_results: str,
        human_review_data: str,
        template_type: str,
        user: str,
    ) -> dict:
        return self._run_workflow(
            {
                "output_type": output_type,
                "project_info": project_info,
                "parsed_documents": parsed_documents,
                "compliance_results": compliance_results,
                "validation_results": validation_results,
                "anomaly_results": anomaly_results,
                "human_review_data": human_review_data,
                "template_type": template_type,
            },
            user,
            settings.dify_report_generator_api_key,
            output_names=("structured_output", "result"),
        )

    def run_contract_drafter(self, contract_data: str, user: str) -> dict:
        return self._run_workflow(
            {"contract_data": contract_data},
            user,
            settings.dify_report_generator_api_key,
            output_names=("structured_output", "result"),
        )


dify_client = DifyClient()
