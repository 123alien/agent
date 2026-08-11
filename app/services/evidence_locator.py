from __future__ import annotations

import json
from typing import Any

from app.schemas.document_context import DocumentContext
from app.schemas.task import EvidenceRef, Issue


def _contains(value: Any, quote: str) -> bool:
    if isinstance(value, dict):
        return any(_contains(item, quote) for item in value.values())
    if isinstance(value, list):
        return any(_contains(item, quote) for item in value)
    return quote in str(value)


def _text_ref(context: DocumentContext, quote: str) -> EvidenceRef | None:
    if quote not in context.raw_text:
        return None
    for section in context.sections:
        if quote in section.content:
            return EvidenceRef(
                document_id=context.document_id,
                quote=quote,
                page=section.page,
                section=section.title,
                source_type="text",
            )
    return EvidenceRef(
        document_id=context.document_id,
        quote=quote,
        source_type="text",
    )


def _table_ref(context: DocumentContext, quote: str) -> EvidenceRef | None:
    for table in context.tables:
        if any(quote in str(cell) for row in table.rows for cell in row):
            return EvidenceRef(
                document_id=context.document_id,
                quote=quote,
                page=table.page,
                source_type="table",
            )
    return None


def _metadata_ref(context: DocumentContext, quote: str) -> EvidenceRef | None:
    sources = {
        "entities": context.entities.model_dump(mode="json"),
        "file_metadata": context.file_metadata,
        "key_fields": {
            key: value.model_dump(mode="json")
            for key, value in context.key_fields.items()
        },
    }
    for section, value in sources.items():
        if _contains(value, quote):
            return EvidenceRef(
                document_id=context.document_id,
                quote=quote,
                section=section,
                source_type="metadata",
            )
    return None


def locate_evidence(
    quote: str,
    contexts: list[DocumentContext],
    source_file: str = "",
) -> list[EvidenceRef]:
    quote = quote.strip()
    if not quote:
        return []
    preferred = [context for context in contexts if context.file_name == source_file]
    candidates = preferred or contexts
    refs: list[EvidenceRef] = []
    for context in candidates:
        ref = (
            _text_ref(context, quote)
            or _table_ref(context, quote)
            or _metadata_ref(context, quote)
        )
        if ref:
            refs.append(ref)
    return refs


def enrich_issue_evidence(
    issue: Issue,
    contexts: list[DocumentContext],
) -> Issue:
    seen: set[tuple[str, str, str]] = set()
    refs: list[EvidenceRef] = []
    for quote in issue.evidence:
        for ref in locate_evidence(quote, contexts, issue.source_file):
            key = (ref.document_id, ref.quote, ref.source_type)
            if key not in seen:
                seen.add(key)
                refs.append(ref)
    issue.evidence_refs = refs
    if refs:
        first = refs[0]
        if not issue.source_file:
            matching = next(
                (
                    context
                    for context in contexts
                    if context.document_id == first.document_id
                ),
                None,
            )
            issue.source_file = matching.file_name if matching else ""
        if not issue.source_location:
            parts = [first.section]
            if first.page:
                parts.append(f"第{first.page}页")
            issue.source_location = "，".join(part for part in parts if part)
    return issue


def evidence_refs_json(issue: Issue) -> str:
    return json.dumps(
        [ref.model_dump(mode="json") for ref in issue.evidence_refs],
        ensure_ascii=False,
    )
