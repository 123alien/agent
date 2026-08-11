from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.schemas.document_context import (
    ClauseGroups,
    ContextClause,
    ContextField,
    DocumentContext,
    DocumentEntities,
    DocumentQuality,
    SourceLocation,
)
from app.schemas.task import ParsedDocument


_TECHNICAL_KEYWORDS = (
    "技术",
    "产品",
    "品牌",
    "型号",
    "参数",
    "系统",
    "软件",
    "硬件",
    "接口",
    "兼容",
    "性能",
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|0\d{2,3}[- ]?\d{7,8})(?!\d)")


def _sha256(raw_text: str, file_path: str | Path | None) -> tuple[str, str]:
    if file_path:
        path = Path(file_path)
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest(), "file_bytes"
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest(), "parsed_text"


def _source(document: ParsedDocument, text: str, fallback: str = "") -> SourceLocation:
    for section in document.sections:
        if text and text in section.content:
            return SourceLocation(
                document_id=document.file_id,
                section=section.title,
                page=section.page,
                line_start=section.line_start,
            )
    return SourceLocation(document_id=document.file_id, section=fallback)


def _clauses(
    document: ParsedDocument,
    values: list[str],
    group: str,
) -> list[ContextClause]:
    return [
        ContextClause(
            clause_id=f"{document.file_id}:{group}:{index}",
            text=value,
            source=_source(document, value, group),
        )
        for index, value in enumerate(values, start=1)
        if value.strip()
    ]


def build_document_context(
    document: ParsedDocument,
    raw_text: str,
    file_path: str | Path | None = None,
) -> DocumentContext:
    """Convert the current parser result into the frozen DocumentContext v1 contract."""

    file_hash, hash_source = _sha256(raw_text, file_path)
    technical: list[str] = []
    procedure_contract: list[str] = []
    for clause in document.key_clauses:
        target = technical if any(key in clause for key in _TECHNICAL_KEYWORDS) else procedure_contract
        target.append(clause)

    key_fields = {
        name: ContextField(
            value=field.value,
            raw_text=field.raw_text,
            source=_source(document, field.raw_text, field.source_location),
            confidence=field.confidence,
            requires_human_review=field.requires_human_review,
        )
        for name, field in document.extracted_fields.items()
    }

    warnings = list(dict.fromkeys(document.warnings))
    warnings.extend(
        check.message
        for check in document.quality_checks
        if check.status != "passed" and check.message not in warnings
    )
    requires_review = document.parse_status != "success" or any(
        check.requires_human_review or check.status == "failed"
        for check in document.quality_checks
    )

    return DocumentContext(
        document_id=document.file_id,
        file_name=document.filename,
        file_hash=file_hash,
        document_type=document.file_type,
        raw_text=raw_text,
        sections=document.sections,
        tables=document.tables,
        key_fields=key_fields,
        clause_groups=ClauseGroups(
            qualification=_clauses(
                document, document.qualification_requirements, "qualification"
            ),
            technical=_clauses(document, technical, "technical"),
            scoring=_clauses(document, document.scoring_criteria, "scoring"),
            procedure_contract=_clauses(
                document, procedure_contract, "procedure_contract"
            ),
        ),
        entities=DocumentEntities(
            tenderer=document.tenderer,
            procurement_agency=document.procurement_agency,
            bidders=document.bidders,
            contacts=list(dict.fromkeys(_PHONE_RE.findall(raw_text))),
            emails=list(dict.fromkeys(_EMAIL_RE.findall(raw_text))),
        ),
        file_metadata={
            "document_subtype": document.document_subtype,
            "page_count": document.page_count,
            "is_scanned": document.is_scanned,
            "selected_tool": document.selected_tool,
            "tool_trace": document.tool_trace,
            "ocr_applied": document.ocr_applied,
            "ocr_confidence": document.ocr_confidence,
            "text_length": document.text_length,
            "hash_source": hash_source,
            "sheet_names": document.sheet_names,
            "layout_elements": [item.model_dump(mode="json") for item in document.layout_elements],
            "opening_records": [item.model_dump(mode="json") for item in document.opening_records],
            "score_details": [item.model_dump(mode="json") for item in document.score_details],
            "score_summaries": [item.model_dump(mode="json") for item in document.score_summaries],
            "rejection_records": [item.model_dump(mode="json") for item in document.rejection_records],
            "evaluation_opinions": [item.model_dump(mode="json") for item in document.evaluation_opinions],
            "candidate_rankings": [item.model_dump(mode="json") for item in document.candidate_rankings],
            "seal_signature_checks": [item.model_dump(mode="json") for item in document.seal_signature_checks],
        },
        quality=DocumentQuality(
            parse_status=document.parse_status,
            warnings=warnings,
            requires_human_review=requires_review,
        ),
    )
