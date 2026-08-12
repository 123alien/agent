"""Frozen public contract shared by standalone agent APIs and orchestration.

The internal domain models may evolve independently.  Values crossing an HTTP,
Dify, LangGraph-worker, or external-system boundary must use these v1 models.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.task import AGENT_CONTRACT_VERSION, AgentResult, EvidenceRef, Issue


ContractVersion = Literal["1.0.0"]
AgentId = Literal[
    "document_parser",
    "compliance_review",
    "data_verification",
    "anomaly_analysis",
    "report_generator",
]
ExecutionStatus = Literal["completed", "partial", "failed"]
FinalStatus = Literal["confirmed_issue", "human_review", "passed"]
RiskLevel = Literal["高", "中", "低"]
EvidenceType = Literal["text", "table", "metadata", "visual", "derived"]
ErrorCode = Literal[
    "INVALID_REQUEST",
    "FILE_PARSE_FAILED",
    "OCR_FAILED",
    "MODEL_UNAVAILABLE",
    "KNOWLEDGE_RETRIEVAL_FAILED",
    "AGENT_WORKFLOW_TIMEOUT",
    "EVIDENCE_NOT_FOUND",
    "OUTPUT_VALIDATION_FAILED",
    "INTERNAL_ERROR",
]


class FrozenContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentOptions(FrozenContractModel):
    enable_dify: bool = True
    enable_human_review: bool = True
    trace_enabled: bool = True


class AgentRequest(FrozenContractModel):
    contract_version: ContractVersion = AGENT_CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(default="", max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)
    options: AgentOptions = Field(default_factory=AgentOptions)


class AgentEvidence(FrozenContractModel):
    evidence_id: str = ""
    document_id: str = ""
    file_name: str = ""
    page: int | None = Field(default=None, ge=1)
    section: str = ""
    source_type: EvidenceType = "text"
    quote: str = Field(min_length=1)
    bbox: list[float] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    derived_from: list[str] = Field(default_factory=list)


class AgentFinding(FrozenContractModel):
    finding_id: str
    final_status: FinalStatus
    risk_level: RiskLevel
    finding_type: str
    description: str
    basis: str = ""
    suggestion: str = ""
    evidence: list[AgentEvidence] = Field(default_factory=list)
    detection_status: Literal[
        "", "detected", "not_detected", "not_checked", "low_confidence", "mismatch", "uncertain"
    ] = ""
    requires_human_review: bool = False
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_three_state_contract(self) -> "AgentFinding":
        review_detections = {
            "not_detected", "not_checked", "low_confidence", "mismatch", "uncertain"
        }
        if self.detection_status in review_detections and self.final_status != "human_review":
            raise ValueError("不确定视觉状态必须映射为 human_review")
        if self.final_status == "human_review" and not self.requires_human_review:
            raise ValueError("human_review 必须设置 requires_human_review=true")
        if self.final_status != "human_review" and self.requires_human_review:
            raise ValueError("非 human_review 状态不得要求人工复核")
        return self


class AgentError(FrozenContractModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    stage: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""


class AgentExecution(FrozenContractModel):
    duration_ms: int = Field(default=0, ge=0)
    model: str = ""
    workflow_version: str = ""
    ruleset_version: str = ""
    execution_mode: str = ""


class AgentResponse(FrozenContractModel):
    contract_version: ContractVersion = AGENT_CONTRACT_VERSION
    request_id: str
    agent: AgentId
    status: ExecutionStatus
    summary: str
    result: dict[str, Any] = Field(default_factory=dict)
    findings: list[AgentFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    execution: AgentExecution = Field(default_factory=AgentExecution)


AGENT_NAME_TO_ID: dict[str, AgentId] = {
    "文档解析智能体": "document_parser",
    "合规审查智能体": "compliance_review",
    "数据核验智能体": "data_verification",
    "异常分析智能体": "anomaly_analysis",
    "报告生成智能体": "report_generator",
}


def _evidence_from_ref(issue: Issue, ref: EvidenceRef, index: int) -> AgentEvidence:
    source_type: EvidenceType = "visual" if issue.detection_status and ref.source_type == "derived" else ref.source_type
    return AgentEvidence(
        evidence_id=f"{issue.issue_id or 'finding'}-E{index:03d}",
        document_id=ref.document_id,
        file_name=issue.source_file,
        page=ref.page,
        section=ref.section,
        source_type=source_type,
        quote=ref.quote,
        confidence=issue.confidence if source_type in {"visual", "derived"} else None,
        derived_from=ref.derived_from,
    )


def finding_from_issue(issue: Issue) -> AgentFinding:
    evidence = [
        _evidence_from_ref(issue, ref, index)
        for index, ref in enumerate(issue.evidence_refs, start=1)
    ]
    if not evidence:
        evidence = [
            AgentEvidence(
                evidence_id=f"{issue.issue_id or 'finding'}-E{index:03d}",
                file_name=issue.source_file,
                section=issue.source_location,
                source_type="visual" if issue.detection_status else "text",
                quote=quote,
                confidence=issue.confidence if issue.detection_status else None,
            )
            for index, quote in enumerate(issue.evidence, start=1)
            if quote
        ]
    return AgentFinding(
        finding_id=issue.issue_id or "unassigned",
        final_status=issue.final_status,
        risk_level=issue.risk_level,
        finding_type=issue.issue_type,
        description=issue.description,
        basis=issue.basis,
        suggestion=issue.suggestion,
        evidence=evidence,
        detection_status=issue.detection_status,
        requires_human_review=issue.final_status == "human_review",
        confidence=issue.confidence,
    )


def response_from_agent_result(
    *,
    request_id: str,
    agent_result: AgentResult,
    result: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[AgentError] | None = None,
    execution: AgentExecution | None = None,
) -> AgentResponse:
    error_items = errors or []
    return AgentResponse(
        request_id=request_id,
        agent=AGENT_NAME_TO_ID[agent_result.agent],
        status="failed" if error_items else "completed",
        summary=agent_result.summary,
        result=result if result is not None else agent_result.data,
        findings=[finding_from_issue(item) for item in agent_result.issues],
        warnings=warnings or [],
        errors=error_items,
        execution=execution or AgentExecution(),
    )


__all__ = [
    "AgentError", "AgentEvidence", "AgentExecution", "AgentFinding",
    "AgentOptions", "AgentRequest", "AgentResponse", "FinalStatus",
    "finding_from_issue", "response_from_agent_result",
]
