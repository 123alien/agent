from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


API_CONTRACT_VERSION = "1.0.0"
AGENT_CONTRACT_VERSION = "1.0.0"

TaskStatus = Literal["pending", "running", "waiting_review", "completed", "failed"]
ReportOutputType = Literal[
    "综合智能核验报告",
    "合规审查专项报告",
    "数据核验专项报告",
    "异常分析专项报告",
    "整改建议报告",
    "标准化评标报告",
]
ReportTemplateType = Literal[
    "标准审查报告",
    "简版管理层报告",
    "详细审查报告",
    "整改建议报告",
    "标准化评标报告",
]


class UploadedFileInfo(BaseModel):
    file_id: str
    filename: str
    file_type: str = "未知文件"
    saved_path: str
    source_url: str = ""
    document_role: str = "other"


class RemoteFileInput(BaseModel):
    url: str
    filename: str = ""
    file_type: str = ""
    document_role: str = ""


class CreateUrlTaskRequest(BaseModel):
    project_id: str
    project_name: str
    check_type: str = "auto"
    files: list[RemoteFileInput]
    callback_url: str = ""
    system_record: dict = Field(default_factory=dict)
    relationship_data: dict = Field(default_factory=dict)
    output_type: ReportOutputType = "综合智能核验报告"
    template_type: ReportTemplateType = "标准审查报告"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    quote: str
    page: int | None = Field(default=None, ge=1)
    section: str = ""
    source_type: Literal["text", "table", "metadata", "derived"] = "text"
    derived_from: list[str] = Field(default_factory=list)


class Issue(BaseModel):
    issue_id: str = ""
    agent: str
    risk_level: Literal["高", "中", "低"]
    issue_type: str
    source_file: str = ""
    source_location: str = ""
    description: str
    basis: str = ""
    suggestion: str = ""
    evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    requires_human_review: bool = False
    assessment: Literal["明确问题", "待人工判断", "未发现问题"] = "明确问题"
    final_status: Literal["confirmed_issue", "human_review", "passed"] = "confirmed_issue"
    detection_status: Literal[
        "", "detected", "not_detected", "not_checked", "low_confidence", "mismatch", "uncertain"
    ] = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @field_validator("final_status", mode="before")
    @classmethod
    def migrate_legacy_final_status(cls, value: object) -> object:
        """Keep persisted tasks from the legacy two-state contract readable."""
        if value == "confirmed":
            return "confirmed_issue"
        return value

    @model_validator(mode="after")
    def enforce_review_state(self) -> "Issue":
        review_detections = {
            "not_detected", "not_checked", "low_confidence", "mismatch", "uncertain"
        }
        if self.requires_human_review or self.detection_status in review_detections:
            self.requires_human_review = True
            self.assessment = "待人工判断"
            self.final_status = "human_review"
        elif self.final_status == "passed":
            self.assessment = "未发现问题"
        else:
            self.assessment = "明确问题"
            self.final_status = "confirmed_issue"
        return self


class DocumentSection(BaseModel):
    title: str
    level: int = 1
    content: str = ""
    page: int | None = None
    line_start: int | None = None


class DocumentTable(BaseModel):
    page: int | None = None
    page_end: int | None = None
    sheet: str = ""
    start_row: int | None = None
    continued: bool = False
    rows: list[list[str]] = Field(default_factory=list)


class LayoutElement(BaseModel):
    element_type: Literal["title", "paragraph", "table", "image", "header", "footer"]
    text: str = ""
    page: int | None = None
    order: int = 0
    bbox: list[float] = Field(default_factory=list)
    source_name: str = ""


class SourceLocation(BaseModel):
    page: int | None = None
    section: str = ""
    line_start: int | None = None
    sheet: str = ""
    row: int | None = None
    column: str = ""
    cell: str = ""


class ScoreDetail(BaseModel):
    bidder: str = ""
    expert: str = ""
    lot: str = ""
    factor: str = ""
    max_score: float | None = None
    raw_score: float | None = None
    weight: float | None = None
    weighted_score: float | None = None
    source: SourceLocation = Field(default_factory=SourceLocation)


class ScoreSummary(BaseModel):
    bidder: str = ""
    lot: str = ""
    total_score: float | None = None
    rank: int | None = None
    source: SourceLocation = Field(default_factory=SourceLocation)


class OpeningRecord(BaseModel):
    bidder: str = ""
    lot: str = ""
    bid_price: float | None = None
    source: SourceLocation = Field(default_factory=SourceLocation)


class RejectionRecord(BaseModel):
    bidder: str = ""
    reason: str
    cited_clause: str = ""
    evidence: str = ""
    source: SourceLocation = Field(default_factory=SourceLocation)


class EvaluationOpinion(BaseModel):
    author: str = ""
    opinion: str
    evidence: str = ""
    source: SourceLocation = Field(default_factory=SourceLocation)


class CandidateRanking(BaseModel):
    bidder: str
    rank: int | None = None
    lot: str = ""
    evidence: str = ""
    source: SourceLocation = Field(default_factory=SourceLocation)


class SealSignatureCheck(BaseModel):
    target: str = ""
    expected: bool = False
    status: Literal[
        "detected",
        "not_detected",
        "not_checked",
        "low_confidence",
        "mismatch",
        "uncertain",
    ] = "not_checked"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox: list[int] = Field(default_factory=list)
    recognized_text: str = ""
    ocr_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    detector: str = ""
    validation_message: str = ""
    source_text: str = ""
    source: SourceLocation = Field(default_factory=SourceLocation)
    requires_human_review: bool = False


class ExtractedField(BaseModel):
    value: str = ""
    raw_text: str = ""
    source_location: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human_review: bool = False


class DocumentQualityCheck(BaseModel):
    code: str
    status: Literal["passed", "warning", "failed"]
    message: str
    requires_human_review: bool = False


class ParsePlan(BaseModel):
    strategy: str
    source_format: str
    planned_tools: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    quality_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    reasons: list[str] = Field(default_factory=list)


class ParseAttempt(BaseModel):
    attempt: int = Field(ge=1)
    action: str
    tool: str = ""
    trigger: str = ""
    outcome: Literal["completed", "failed", "skipped"] = "completed"
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)


class EvidenceChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    content_type: Literal["text", "table", "metadata"] = "text"
    page: int | None = Field(default=None, ge=1)
    section: str = ""
    source_hash: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_human_review: bool = False


class ParsedDocument(BaseModel):
    file_id: str
    filename: str
    file_type: str
    document_subtype: str = "其他资料"
    document_role: str = "other"
    text_length: int
    project_name: str = ""
    tenderer: str = ""
    procurement_agency: str = ""
    bidders: list[str] = Field(default_factory=list)
    bid_prices: list[str] = Field(default_factory=list)
    qualification_requirements: list[str] = Field(default_factory=list)
    scoring_criteria: list[str] = Field(default_factory=list)
    key_clauses: list[str] = Field(default_factory=list)
    page_count: int = 0
    is_scanned: bool = False
    parse_status: Literal["success", "warning", "failed"] = "success"
    selected_tool: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    ocr_applied: bool = False
    ocr_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sections: list[DocumentSection] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    layout_elements: list[LayoutElement] = Field(default_factory=list)
    sheet_names: list[str] = Field(default_factory=list)
    opening_records: list[OpeningRecord] = Field(default_factory=list)
    score_details: list[ScoreDetail] = Field(default_factory=list)
    score_summaries: list[ScoreSummary] = Field(default_factory=list)
    invalid_bid_clauses: list[str] = Field(default_factory=list)
    rejection_records: list[RejectionRecord] = Field(default_factory=list)
    evaluation_opinions: list[EvaluationOpinion] = Field(default_factory=list)
    candidate_rankings: list[CandidateRanking] = Field(default_factory=list)
    seal_signature_checks: list[SealSignatureCheck] = Field(default_factory=list)
    extracted_fields: dict[str, ExtractedField] = Field(default_factory=dict)
    quality_checks: list[DocumentQualityCheck] = Field(default_factory=list)
    parse_plan: ParsePlan | None = None
    parse_attempts: list[ParseAttempt] = Field(default_factory=list)
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_chunks: list[EvidenceChunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent: str
    summary: str
    issues: list[Issue] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)


class TaskResult(BaseModel):
    summary: str
    routing: dict = Field(default_factory=dict)
    parsed_documents: list[ParsedDocument] = Field(default_factory=list)
    agent_results: list[AgentResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    report_url: str = ""
    report_files: dict[str, str] = Field(default_factory=dict)
    material_inventory: dict = Field(default_factory=dict)


class TaskRecord(BaseModel):
    api_version: str = API_CONTRACT_VERSION
    task_id: str
    project_id: str
    project_name: str
    check_type: str
    status: TaskStatus
    files: list[UploadedFileInfo] = Field(default_factory=list)
    result: TaskResult | None = None
    error: str = ""
    review_request: dict = Field(default_factory=dict)
    review_progress: dict = Field(default_factory=dict)
    execution_context: dict = Field(default_factory=dict)
    execution_events: list[dict] = Field(default_factory=list)
    execution_metadata: dict = Field(default_factory=dict)
    review_audit: list[dict] = Field(default_factory=list)
    callback_url: str = ""
    system_record: dict = Field(default_factory=dict)
    relationship_data: dict = Field(default_factory=dict)
    output_type: ReportOutputType = "综合智能核验报告"
    template_type: ReportTemplateType = "标准审查报告"
    callback_status: Literal["not_configured", "pending", "sent", "failed"] = (
        "not_configured"
    )
    callback_error: str = ""
    callback_attempts: int = 0
    created_at: str
    updated_at: str


class ReviewItem(BaseModel):
    issue_id: str | None = None
    decision: Literal["正确", "误判", "漏判", "需修改"]
    comment: str = ""
    corrected_text: str = ""


class ReviewRequest(BaseModel):
    reviewer: str = ""
    items: list[ReviewItem] = Field(default_factory=list)
    submit: bool = True
    batch_decision: Literal["正确", "误判", "需修改"] | None = None
    batch_issue_ids: list[str] = Field(default_factory=list)
    comment: str = ""
