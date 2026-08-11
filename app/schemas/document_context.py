from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.task import DocumentSection, DocumentTable


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceLocation(ContractModel):
    document_id: str
    section: str = ""
    page: int | None = None
    line_start: int | None = None


class ContextField(ContractModel):
    value: Any = None
    raw_text: str = ""
    source: SourceLocation
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_human_review: bool = False


class ContextClause(ContractModel):
    clause_id: str
    text: str
    source: SourceLocation
    source_type: Literal["text", "table", "metadata", "derived"] = "text"


class ClauseGroups(ContractModel):
    qualification: list[ContextClause] = Field(default_factory=list)
    technical: list[ContextClause] = Field(default_factory=list)
    scoring: list[ContextClause] = Field(default_factory=list)
    procedure_contract: list[ContextClause] = Field(default_factory=list)


class DocumentEntities(ContractModel):
    tenderer: str = ""
    procurement_agency: str = ""
    bidders: list[str] = Field(default_factory=list)
    contacts: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)


class DocumentQuality(ContractModel):
    parse_status: Literal["success", "warning", "failed"] = "success"
    warnings: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class DocumentContext(ContractModel):
    contract_version: Literal["1.0.0"] = "1.0.0"
    document_id: str
    file_name: str
    file_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    document_type: str
    raw_text: str
    sections: list[DocumentSection] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    key_fields: dict[str, ContextField] = Field(default_factory=dict)
    clause_groups: ClauseGroups = Field(default_factory=ClauseGroups)
    entities: DocumentEntities = Field(default_factory=DocumentEntities)
    file_metadata: dict[str, Any] = Field(default_factory=dict)
    quality: DocumentQuality = Field(default_factory=DocumentQuality)
