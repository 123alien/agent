import json
from typing import Any

from app.core.config import settings
from app.schemas.contract import ContractGenerationRequest, ContractValidationItem
from app.schemas.task import AgentResult, Issue, ParsedDocument, TaskRecord
from app.services.contract_service import validate_contract
from app.services.dify_client import DifyWorkflowError, dify_client


class ReportGeneratorAgent:
    name = "报告生成智能体"

    def run(
        self,
        parsed_docs: list[ParsedDocument],
        issues: list[Issue],
        *,
        task: TaskRecord | None = None,
        agent_results: list[AgentResult] | None = None,
        human_review: dict | None = None,
        output_type: str = "综合智能核验报告",
        template_type: str = "标准审查报告",
    ) -> AgentResult:
        agent_results = agent_results or []
        human_review = human_review or {}
        high = sum(1 for issue in issues if issue.risk_level == "高")
        medium = sum(1 for issue in issues if issue.risk_level == "中")
        low = sum(1 for issue in issues if issue.risk_level == "低")
        confirmed = sum(1 for issue in issues if issue.final_status == "confirmed_issue")
        pending = sum(1 for issue in issues if issue.final_status == "human_review")
        missing_evidence = sum(
            1 for issue in issues if not issue.evidence and not issue.evidence_refs
        )
        review_required = pending
        failed_documents = sum(
            1 for document in parsed_docs if document.parse_status == "failed"
        )
        report_ready = missing_evidence == 0 and failed_documents == 0
        review_completed = bool(human_review) and review_required == 0
        report_status = "正式核验版" if review_completed else "待复核版"
        summary = (
            f"已汇总 {len(parsed_docs)} 份文档的核验结果，共保留 {len(issues)} 项审查事项，"
            f"其中明确问题 {confirmed} 项、待人工判断 {pending} 项；"
            f"按风险等级统计：高风险 {high} 项、中风险 {medium} 项、低风险 {low} 项。"
        )

        report_package = self._build_report_package(
            task,
            parsed_docs,
            issues,
            agent_results,
            human_review,
            output_type,
            template_type,
            report_status,
            summary,
            {"high": high, "medium": medium, "low": low, "total": len(issues)},
        )
        execution_mode = "deterministic"
        dify_errors: list[str] = []
        if (
            settings.report_generator_workflow_version.startswith("2.")
            and dify_client.report_generator_enabled
        ):
            try:
                semantic = dify_client.run_report_generator(
                    output_type=output_type,
                    project_info=report_package["project_info"],
                    parsed_documents=json.dumps(
                        self._dify_parsed_payload(parsed_docs), ensure_ascii=False
                    ),
                    compliance_results=json.dumps(
                        self._dify_agent_payload(
                            agent_results, issues, "合规审查智能体", "issues"
                        ),
                        ensure_ascii=False,
                    ),
                    validation_results=json.dumps(
                        self._dify_agent_payload(
                            agent_results, issues, "数据核验智能体", "issues"
                        ),
                        ensure_ascii=False,
                    ),
                    anomaly_results=json.dumps(
                        self._dify_agent_payload(
                            agent_results, issues, "异常分析智能体", "anomalies"
                        ),
                        ensure_ascii=False,
                    ),
                    human_review_data=json.dumps(human_review, ensure_ascii=False),
                    template_type=template_type,
                    user="agent-report-generation",
                )
                report_package["semantic_content"] = self._sanitize_semantic_content(
                    semantic, issues
                )
                execution_mode = "dify"
            except DifyWorkflowError as exc:
                dify_errors.append(str(exc))
                execution_mode = "deterministic_fallback"

        return AgentResult(
            agent=self.name,
            summary=summary,
            data={
                "high": high,
                "medium": medium,
                "low": low,
                "confirmed": confirmed,
                "pending": pending,
                "missing_evidence_count": missing_evidence,
                "review_required_count": review_required,
                "failed_document_count": failed_documents,
                "report_ready": report_ready,
                "report_status": report_status,
                "output_type": output_type,
                "template_type": template_type,
                "standard_evaluation_report_ready": False,
                "execution_mode": execution_mode,
                "dify_errors": dify_errors,
                "validation": {
                    "issue_count_matches": high + medium + low == len(issues),
                    "evidence_complete": missing_evidence == 0,
                    "documents_parseable": failed_documents == 0,
                    "human_review_complete": review_required == 0,
                },
                "report_package": report_package,
            },
        )

    @staticmethod
    def _dify_parsed_payload(parsed_docs: list[ParsedDocument]) -> dict:
        """Send report planning metadata instead of entire extracted documents."""
        documents = []
        for document in parsed_docs:
            def extracted_value(name: str) -> str:
                field = document.extracted_fields.get(name)
                return str(field.value) if field and field.value is not None else ""

            documents.append(
                {
                    "document_id": document.file_id,
                    "document_name": document.filename,
                    "document_type": document.document_subtype or document.file_type,
                    "parse_status": document.parse_status,
                    "page_count": document.page_count,
                    "section_count": len(document.sections),
                    "table_count": len(document.tables),
                    "warning_count": len(document.warnings),
                    "warnings": document.warnings[:20],
                    "project_name": document.project_name,
                    "tenderer": document.tenderer,
                    "procurement_agency": document.procurement_agency,
                    "budget": extracted_value("budget"),
                    "price_limit": extracted_value("price_limit"),
                    "candidate_rankings": [
                        item.model_dump(mode="json")
                        for item in document.candidate_rankings[:20]
                    ],
                    "rejection_records": [
                        item.model_dump(mode="json")
                        for item in document.rejection_records[:20]
                    ],
                }
            )
        return {
            "summary": f"共解析 {len(documents)} 份文档。",
            "documents": documents,
        }

    @staticmethod
    def _dify_agent_payload(
        agent_results: list[AgentResult],
        final_issues: list[Issue],
        name: str,
        result_key: str,
    ) -> dict:
        """Flatten agent results to the input contract used by the Dify DSL."""
        matches = [item for item in agent_results if item.agent == name]
        summaries = [item.summary for item in matches if item.summary]
        items = [
            issue.model_dump(mode="json")
            for issue in final_issues
            if issue.agent == name
        ]
        return {
            "summary": "；".join(summaries),
            result_key: items,
        }

    @staticmethod
    def _agent_payload(agent_results: list[AgentResult], name: str) -> dict:
        matches = [item for item in agent_results if item.agent == name]
        return {
            "results": [
                {
                    "summary": item.summary,
                    "issues": [issue.model_dump(mode="json") for issue in item.issues],
                    "data": item.data,
                }
                for item in matches
            ]
        }

    def _build_report_package(
        self,
        task: TaskRecord | None,
        parsed_docs: list[ParsedDocument],
        issues: list[Issue],
        agent_results: list[AgentResult],
        human_review: dict,
        output_type: str,
        template_type: str,
        report_status: str,
        summary: str,
        risk_statistics: dict,
    ) -> dict:
        first = parsed_docs[0] if parsed_docs else None
        extracted_project = first.extracted_fields.get("project_name") if first else None
        if extracted_project and extracted_project.confidence >= 0.75 and not extracted_project.requires_human_review:
            project_name = str(extracted_project.value)
        elif extracted_project:
            project_name = "待人工确认"
        else:
            project_name = (task.project_name if task else "") or (first.project_name if first else "")
        def display_field(name: str, fallback: str = "") -> str:
            field = first.extracted_fields.get(name) if first else None
            if field and field.confidence >= 0.75 and not field.requires_human_review:
                return str(field.value)
            if field and field.value:
                return "待人工确认"
            return fallback
        field_sources = {}
        if first:
            for name in ("project_name", "tenderer", "procurement_agency", "budget", "price_limit", "deadline"):
                field = first.extracted_fields.get(name)
                if not field:
                    continue
                field_sources[name] = {
                    "value": field.value,
                    "source_file": first.filename,
                    "source_location": field.source_location,
                    "source_text": field.raw_text,
                    "confidence": field.confidence,
                    "requires_human_review": field.requires_human_review,
                }
        project_info = {
            "task_id": task.task_id if task else "",
            "project_id": task.project_id if task else "",
            "project_name": project_name,
            "tenderer": display_field("tenderer", first.tenderer if first else ""),
            "procurement_agency": display_field("procurement_agency", first.procurement_agency if first else ""),
            "system_record": task.system_record if task else {},
            "field_sources": field_sources,
        }
        return {
            "report_title": "评标智能核验报告",
            "report_type": output_type,
            "report_status": report_status,
            "template_type": template_type,
            "project_info": project_info,
            "executive_summary": summary,
            "risk_statistics": risk_statistics,
            "parsed_documents": [doc.model_dump(mode="json") for doc in parsed_docs],
            "compliance_results": self._agent_payload(
                agent_results, "合规审查智能体"
            ),
            "validation_results": self._agent_payload(
                agent_results, "数据核验智能体"
            ),
            "anomaly_results": self._agent_payload(
                agent_results, "异常分析智能体"
            ),
            "human_review_data": human_review,
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "pending_items": [
                issue.model_dump(mode="json")
                for issue in issues
                if issue.requires_human_review
            ],
            "semantic_content": {},
        }

    @staticmethod
    def _sanitize_semantic_content(payload: Any, issues: list[Issue]) -> dict:
        if not isinstance(payload, dict):
            return {}
        allowed_issue_ids = {issue.issue_id for issue in issues if issue.issue_id}
        semantic_issues = payload.get("issues", [])
        if isinstance(semantic_issues, list):
            payload["issues"] = [
                item
                for item in semantic_issues
                if isinstance(item, dict)
                and item.get("issue_id") in allowed_issue_ids
            ]
        else:
            payload["issues"] = []
        high = sum(1 for issue in issues if issue.risk_level == "高")
        medium = sum(1 for issue in issues if issue.risk_level == "中")
        low = sum(1 for issue in issues if issue.risk_level == "低")
        pending = sum(1 for issue in issues if issue.requires_human_review)
        payload["risk_statistics"] = {
            "high": high,
            "medium": medium,
            "low": low,
            "total": len(issues),
        }
        payload["executive_summary"] = (
            f"本次核验共发现{len(issues)}项明确或潜在问题，其中高风险{high}项、"
            f"中风险{medium}项、低风险{low}项，尚有{pending}项需要人工复核。"
        )
        return payload

    def validate_contract(
        self, request: ContractGenerationRequest
    ) -> list[ContractValidationItem]:
        return validate_contract(request)
