from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.schemas.task import CandidateRanking, DocumentSection, EvaluationOpinion, ExtractedField, RejectionRecord
from app.services.dify_client import DifyWorkflowError, dify_client


class DocumentSemanticEnhancer:
    chunk_size = 6000
    chunk_overlap = 300
    retry_min_size = 1800
    max_workers = 3
    field_keywords = {
        "project_name": ("项目名称", "工程名称"),
        "budget": ("项目预算", "预算金额", "采购预算"),
        "price_limit": ("最高限价", "投标限价", "最高控制价", "控制价"),
        "tenderer": ("采购人", "采购单位", "招标人", "招标单位"),
        "procurement_agency": ("采购代理机构", "招标代理机构"),
        "deadline": ("截止时间", "投标截止", "响应文件提交"),
        "rejection_records": ("废标", "否决投标", "无效投标", "不通过"),
        "evaluation_opinions": ("评审意见", "评标意见", "评审结论"),
        "candidate_rankings": ("中标候选人", "候选人推荐", "排名"),
    }

    @property
    def enabled(self) -> bool:
        return dify_client.document_parser_enabled

    def enhance(
        self,
        document_text: str,
        file_id: str,
        *,
        requested_fields: list[str] | None = None,
        include_sections: bool = True,
        document_type: str = "其他资料",
        parser_context: dict | None = None,
    ) -> "SemanticEnhancementResult":
        chunks = self._split_text(document_text)
        if not chunks:
            return SemanticEnhancementResult()

        selected_chunks = self._select_chunks(
            chunks,
            requested_fields=requested_fields or [],
            include_sections=include_sections,
        )

        sections: list[DocumentSection] = []
        fields: dict[str, ExtractedField] = {}
        warnings: list[str] = []
        rejection_records: list[RejectionRecord] = []
        evaluation_opinions: list[EvaluationOpinion] = []
        candidate_rankings: list[CandidateRanking] = []
        successful_chunks = 0

        jobs = [
            (chunk, f"{original_index}/{len(chunks)}")
            for original_index, chunk in selected_chunks
        ]

        def run_job(job: tuple[str, str]):
            chunk, label = job
            return self._run_chunk_with_retry(
                chunk,
                file_id=file_id,
                chunk_label=label,
                document_type=document_type,
                parser_context=parser_context or {},
                requested_fields=requested_fields or [],
                include_sections=include_sections,
            )

        if len(jobs) == 1:
            job_results = [run_job(jobs[0])]
        else:
            with ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(jobs)),
                thread_name_prefix="dify-document-chunk",
            ) as executor:
                job_results = list(executor.map(run_job, jobs))

        for chunk_results, chunk_warnings in job_results:
            warnings.extend(chunk_warnings)
            if chunk_results:
                successful_chunks += 1
            for payload, source_label in chunk_results:
                if include_sections:
                    parsed_sections = self._sections(payload.get("sections", []))
                    self._merge_sections(sections, parsed_sections)
                parsed_fields = self._fields(payload.get("key_fields", {}))
                if requested_fields:
                    parsed_fields = {
                        name: value
                        for name, value in parsed_fields.items()
                        if name in requested_fields
                    }
                parsed_fields = {
                    name: value
                    for name, value in parsed_fields.items()
                    if self._field_evidence_matches(name, value, document_text)
                }
                self._merge_fields(fields, parsed_fields, source_label, warnings)
                if self._supports_rejection_records(document_type):
                    self._merge_records(rejection_records, self._rejection_records(payload.get("rejection_records"), document_text))
                self._merge_records(evaluation_opinions, self._evaluation_opinions(payload.get("evaluation_opinions"), document_text))
                self._merge_records(candidate_rankings, self._candidate_rankings(payload.get("candidate_rankings"), document_text))
                warnings.extend(
                    self._relevant_payload_warnings(
                        payload.get("warnings", []),
                        requested_fields=requested_fields or [],
                        include_sections=include_sections,
                    )
                )

        if successful_chunks == 0:
            raise DifyWorkflowError("Dify 文档语义增强的所有分段均执行失败")
        return SemanticEnhancementResult(
            sections=sections[:200],
            fields=fields,
            warnings=self._unique(warnings),
            rejection_records=rejection_records[:100],
            evaluation_opinions=evaluation_opinions[:100],
            candidate_rankings=candidate_rankings[:100],
        )

    def _relevant_payload_warnings(
        self,
        values: object,
        *,
        requested_fields: list[str],
        include_sections: bool,
    ) -> list[str]:
        if not isinstance(values, list):
            return []
        warnings = [str(item).strip() for item in values if str(item).strip()]
        if include_sections or not requested_fields:
            return warnings
        keywords = {
            keyword
            for field_name in requested_fields
            for keyword in self.field_keywords.get(field_name, ())
        }
        return [item for item in warnings if any(keyword in item for keyword in keywords)]

    def _select_chunks(
        self,
        chunks: list[str],
        *,
        requested_fields: list[str],
        include_sections: bool,
    ) -> list[tuple[int, str]]:
        indexed = list(enumerate(chunks, start=1))
        if include_sections or not requested_fields:
            return indexed

        keywords = {
            keyword
            for field_name in requested_fields
            for keyword in self.field_keywords.get(field_name, ())
        }
        if not keywords:
            return indexed
        matched = [
            (index, chunk)
            for index, chunk in indexed
            if any(keyword in chunk for keyword in keywords)
        ]
        # A field can be described in adjacent chunks due to PDF line wrapping.
        selected_indexes = {
            nearby
            for index, _ in matched
            for nearby in (index - 1, index, index + 1)
            if 1 <= nearby <= len(chunks)
        }
        if selected_indexes:
            return [item for item in indexed if item[0] in selected_indexes]
        # No keyword hit: inspect the opening chunks rather than silently dropping
        # semantic enhancement or scanning the entire document.
        return indexed[: min(2, len(indexed))]

    def _run_chunk_with_retry(
        self,
        chunk: str,
        *,
        file_id: str,
        chunk_label: str,
        document_type: str,
        parser_context: dict,
        requested_fields: list[str],
        include_sections: bool,
        depth: int = 0,
    ) -> tuple[list[tuple[dict, str]], list[str]]:
        prompt_text = (
            f"【文档分段 {chunk_label}】\n"
            "请仅根据本分段提取结构化结果；不得补写本分段之外的内容。\n"
            f"{chunk}"
        )
        try:
            payload = dify_client.run_document_semantic_parser(
                prompt_text,
                document_type,
                json.dumps(parser_context, ensure_ascii=False),
                json.dumps(requested_fields, ensure_ascii=False),
                "true" if include_sections else "false",
                user=f"document-parser-{file_id}-{chunk_label.replace('/', '-')}",
            )
            return [(payload, f"语义分段 {chunk_label}")], []
        except DifyWorkflowError as exc:
            # 网络超时不是分段过长造成的结构化输出错误。继续二分重试会把
            # 一次超时放大为数分钟阻塞，因此超时时立即降级到本地结果。
            error_text = str(exc).lower()
            transport_errors = (
                "timed out",
                "timeout",
                "server unavailable",
                "max retries exceeded",
                "failed to resolve",
                "name resolution",
                "connection error",
                "connection refused",
            )
            if any(marker in error_text for marker in transport_errors):
                return [], [f"语义分段 {chunk_label} 执行失败: {exc}"]
            if depth >= 2 or len(chunk) <= self.retry_min_size:
                return [], [f"语义分段 {chunk_label} 执行失败: {exc}"]

            left, right = self._bisect_chunk(chunk)
            if not left or not right:
                return [], [f"语义分段 {chunk_label} 执行失败: {exc}"]
            warning = f"语义分段 {chunk_label} 首次失败，已缩小分段重试"
            left_results, left_warnings = self._run_chunk_with_retry(
                left,
                file_id=file_id,
                chunk_label=f"{chunk_label}.1",
                document_type=document_type,
                parser_context=parser_context,
                requested_fields=requested_fields,
                include_sections=include_sections,
                depth=depth + 1,
            )
            right_results, right_warnings = self._run_chunk_with_retry(
                right,
                file_id=file_id,
                chunk_label=f"{chunk_label}.2",
                document_type=document_type,
                parser_context=parser_context,
                requested_fields=requested_fields,
                include_sections=include_sections,
                depth=depth + 1,
            )
            return (
                [*left_results, *right_results],
                [warning, *left_warnings, *right_warnings],
            )

    def _split_text(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        paragraphs = [item.strip() for item in text.split("\n") if item.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_length = 0

        for paragraph in paragraphs:
            if len(paragraph) > self.chunk_size:
                if current:
                    chunks.append("\n".join(current))
                    current = []
                    current_length = 0
                start = 0
                while start < len(paragraph):
                    end = min(start + self.chunk_size, len(paragraph))
                    chunks.append(paragraph[start:end])
                    if end == len(paragraph):
                        break
                    start = max(end - self.chunk_overlap, start + 1)
                continue

            added = len(paragraph) + (1 if current else 0)
            if current and current_length + added > self.chunk_size:
                completed = "\n".join(current)
                chunks.append(completed)
                overlap = completed[-self.chunk_overlap :].lstrip()
                current = [overlap, paragraph] if overlap else [paragraph]
                current_length = sum(len(item) for item in current) + len(current) - 1
            else:
                current.append(paragraph)
                current_length += added

        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _bisect_chunk(chunk: str) -> tuple[str, str]:
        midpoint = len(chunk) // 2
        split_at = chunk.rfind("\n", 0, midpoint)
        if split_at < len(chunk) // 4:
            split_at = chunk.find("\n", midpoint)
        if split_at < 0:
            split_at = midpoint
        return chunk[:split_at].strip(), chunk[split_at:].strip()

    @staticmethod
    def _merge_sections(
        target: list[DocumentSection],
        incoming: list[DocumentSection],
    ) -> None:
        seen = {(item.title.strip(), item.content.strip()) for item in target}
        for section in incoming:
            key = (section.title.strip(), section.content.strip())
            if key not in seen:
                target.append(section)
                seen.add(key)

    @staticmethod
    def _merge_fields(
        target: dict[str, ExtractedField],
        incoming: dict[str, ExtractedField],
        source_label: str,
        warnings: list[str],
    ) -> None:
        for name, candidate in incoming.items():
            if not candidate.source_location:
                candidate.source_location = source_label
            current = target.get(name)
            if current is None:
                target[name] = candidate
                continue
            if current.value.strip() == candidate.value.strip():
                if candidate.confidence > current.confidence:
                    target[name] = candidate
                continue
            warnings.append(
                f"字段 {name} 在不同分段识别结果不一致，已标记人工复核"
            )
            selected = candidate if candidate.confidence > current.confidence else current
            selected.requires_human_review = True
            target[name] = selected

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value and value not in seen:
                result.append(value)
                seen.add(value)
        return result

    @staticmethod
    def _sections(values: object) -> list[DocumentSection]:
        if not isinstance(values, list):
            return []
        result: list[DocumentSection] = []
        for item in values:
            if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                continue
            try:
                result.append(
                    DocumentSection(
                        title=str(item["title"]).strip()[:200],
                        level=max(1, min(int(item.get("level", 1)), 6)),
                        content=str(item.get("content", "")).strip(),
                        page=item.get("page"),
                        line_start=item.get("line_start"),
                    )
                )
            except (TypeError, ValueError):
                continue
        return result[:200]

    @staticmethod
    def _fields(values: object) -> dict[str, ExtractedField]:
        if isinstance(values, list):
            values = {str(item.get("field_name", "")): item for item in values if isinstance(item, dict) and item.get("field_name")}
        if not isinstance(values, dict):
            return {}
        result: dict[str, ExtractedField] = {}
        for name, value in values.items():
            if isinstance(value, str):
                value = {"value": value}
            if not isinstance(value, dict):
                continue
            field_value = str(value.get("value", "")).strip()
            compact_value = field_value.replace(" ", "").lower()
            if (
                not field_value
                or compact_value in {
                    "none",
                    "null",
                    "无",
                    "暂无",
                    "未知",
                    "未提供",
                    "未找到",
                    "待填写",
                    "未填写",
                }
                or "____" in compact_value
                or "＿＿＿＿" in compact_value
            ):
                continue
            try:
                result[str(name)] = ExtractedField(
                    value=field_value[:500],
                    raw_text=str(value.get("raw_text", "")).strip()[:1000],
                    source_location=str(value.get("source_location", "")).strip()[:200],
                    confidence=min(max(float(value.get("confidence", 0.7)), 0.0), 1.0),
                    requires_human_review=bool(
                        value.get("requires_human_review", True)
                    ),
                )
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _field_evidence_matches(
        field_name: str,
        value: ExtractedField,
        source_text: str,
    ) -> bool:
        if value.raw_text and value.raw_text not in source_text:
            return False
        # 最高限价与预算经常具有相同金额。语义模型只有在证据原文明确
        # 表示限价/控制价时才可以填充 price_limit，严禁用预算字段代替。
        if field_name == "price_limit":
            if not value.raw_text:
                return False
            compact = value.raw_text.replace(" ", "")
            return any(keyword in compact for keyword in ("最高限价", "投标限价", "最高投标限价", "最高控制价", "控制价"))
        return True

    @staticmethod
    def _supports_rejection_records(document_type: str) -> bool:
        return document_type in {"评标报告", "废标说明", "评审意见", "中标候选人推荐表"}

    @staticmethod
    def _merge_records(target: list, incoming: list) -> None:
        seen = {(type(item).__name__, getattr(item, "evidence", ""), getattr(item, "bidder", ""), getattr(item, "opinion", "")) for item in target}
        for item in incoming:
            key = (type(item).__name__, getattr(item, "evidence", ""), getattr(item, "bidder", ""), getattr(item, "opinion", ""))
            if key not in seen:
                target.append(item)
                seen.add(key)

    @staticmethod
    def _rejection_records(values: object, source_text: str) -> list[RejectionRecord]:
        result: list[RejectionRecord] = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("evidence") or "").strip()
            if not evidence or evidence not in source_text:
                continue
            result.append(RejectionRecord(
                bidder=str(item.get("bidder") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
                cited_clause=str(item.get("cited_clause") or "").strip(),
                evidence=evidence,
                requires_human_review=bool(item.get("requires_human_review", False)),
            ))
        return result

    @staticmethod
    def _evaluation_opinions(values: object, source_text: str) -> list[EvaluationOpinion]:
        result: list[EvaluationOpinion] = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("evidence") or "").strip()
            if not evidence or evidence not in source_text:
                continue
            result.append(EvaluationOpinion(author=str(item.get("author") or "").strip(), opinion=str(item.get("opinion") or "").strip(), evidence=evidence))
        return result

    @staticmethod
    def _candidate_rankings(values: object, source_text: str) -> list[CandidateRanking]:
        result: list[CandidateRanking] = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("evidence") or "").strip()
            if not evidence or evidence not in source_text:
                continue
            try:
                rank = int(item.get("rank"))
            except (TypeError, ValueError):
                continue
            if rank < 1:
                continue
            compact_evidence = evidence.replace(" ", "")
            has_explicit_rank = bool(
                re.search(r"第[一二三四五六七八九十\d]+中标候选人", compact_evidence)
                or re.search(r"(?:排名|名次|排序)[：:]?[一二三四五六七八九十\d]+", compact_evidence)
            )
            if not has_explicit_rank:
                continue
            result.append(CandidateRanking(bidder=str(item.get("bidder") or "").strip(), rank=rank, lot=str(item.get("lot") or "").strip(), evidence=evidence))
        return result


@dataclass
class SemanticEnhancementResult:
    sections: list[DocumentSection] = field(default_factory=list)
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    rejection_records: list[RejectionRecord] = field(default_factory=list)
    evaluation_opinions: list[EvaluationOpinion] = field(default_factory=list)
    candidate_rankings: list[CandidateRanking] = field(default_factory=list)

    def __iter__(self):
        yield self.sections
        yield self.fields
        yield self.warnings


document_semantic_enhancer = DocumentSemanticEnhancer()


__all__ = [
    "DifyWorkflowError",
    "DocumentSemanticEnhancer",
    "document_semantic_enhancer",
]
