from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed"]


class UploadedFileInfo(BaseModel):
    file_id: str
    filename: str
    file_type: str = "未知文件"
    saved_path: str
    source_url: str = ""


class RemoteFileInput(BaseModel):
    url: str
    filename: str = ""
    file_type: str = ""


class CreateUrlTaskRequest(BaseModel):
    project_id: str
    project_name: str
    check_type: str = "full"
    files: list[RemoteFileInput]
    callback_url: str = ""


class Issue(BaseModel):
    agent: str
    risk_level: Literal["高", "中", "低"]
    issue_type: str
    source_file: str = ""
    source_location: str = ""
    description: str
    basis: str = ""
    suggestion: str = ""
    evidence: list[str] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    file_id: str
    filename: str
    file_type: str
    text_length: int
    project_name: str = ""
    tenderer: str = ""
    bidders: list[str] = Field(default_factory=list)
    bid_prices: list[str] = Field(default_factory=list)
    qualification_requirements: list[str] = Field(default_factory=list)
    scoring_criteria: list[str] = Field(default_factory=list)
    key_clauses: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent: str
    summary: str
    issues: list[Issue] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)


class TaskResult(BaseModel):
    summary: str
    parsed_documents: list[ParsedDocument] = Field(default_factory=list)
    agent_results: list[AgentResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    report_url: str = ""


class TaskRecord(BaseModel):
    task_id: str
    project_id: str
    project_name: str
    check_type: str
    status: TaskStatus
    files: list[UploadedFileInfo] = Field(default_factory=list)
    result: TaskResult | None = None
    error: str = ""
    callback_url: str = ""
    callback_status: Literal["not_configured", "pending", "sent", "failed"] = (
        "not_configured"
    )
    callback_error: str = ""
    created_at: str
    updated_at: str


class ReviewItem(BaseModel):
    issue_id: str | None = None
    decision: Literal["正确", "误判", "漏判", "需修改"]
    comment: str = ""
    corrected_text: str = ""


class ReviewRequest(BaseModel):
    reviewer: str = ""
    items: list[ReviewItem]
