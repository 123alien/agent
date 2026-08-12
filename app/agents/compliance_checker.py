from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from app.agents.utils import find_lines
from app.core.config import settings
from app.schemas.document_context import DocumentContext
from app.schemas.task import AgentResult, Issue, ParsedDocument
from app.services.dify_client import DifyWorkflowError, dify_client
from app.services.llm_client import llm_client
from app.services.workflow_cache import workflow_result_cache


class ComplianceCheckerAgent:
    name = "合规审查智能体"
    chunk_size = 9000
    chunk_overlap = 500
    max_workers = 3
    evaluation_required_sections = {
        "项目基本信息": ("项目名称", "项目概况", "基本情况"),
        "评标委员会": ("评标委员会", "评审委员会", "评委名单", "专家名单"),
        "评审过程": ("评标过程", "评审过程", "资格审查", "符合性审查"),
        "评审结果": ("评标结果", "评审结果", "评审结论"),
        "中标候选人推荐": ("中标候选人", "成交候选人", "推荐意见"),
    }
    consistency_fields = {
        "project_name": "项目名称",
        "tenderer": "采购人/招标人",
        "budget": "项目预算",
        "price_limit": "最高投标限价",
        "deadline": "截止时间",
    }

    def run_contexts(
        self,
        contexts: list[DocumentContext],
        parsed_docs: list[ParsedDocument],
        system_record: dict | None = None,
        enable_dify: bool = True,
    ) -> AgentResult:
        """Run from the shared v1 contract while preserving legacy rule helpers."""
        raw_texts = {context.document_id: context.raw_text for context in contexts}
        file_hashes = {context.document_id: context.file_hash for context in contexts}
        if enable_dify and dify_client.enabled:
            result = self._run_with_dify(parsed_docs, raw_texts, file_hashes)
        else:
            result = self._run_locally(parsed_docs, raw_texts)
        process_issues, process_data = self._process_compliance_checks(
            contexts,
            parsed_docs,
            system_record or {},
        )
        model_issues = result.issues if isinstance(result.issues, list) else []
        result.issues = self._deduplicate_issues([*process_issues, *model_issues])
        confirmed_count = sum(item.assessment == "明确问题" for item in result.issues)
        review_count = sum(item.assessment == "待人工判断" for item in result.issues)
        result.summary = (
            f"合规审查完成：明确问题 {confirmed_count} 项，"
            f"待人工判断 {review_count} 项；已执行评标报告完整性、"
            "基础信息一致性和废标依据回查。"
        )
        result.data = {
            **result.data,
            "input_contract": "DocumentContext/1.0.0",
            "input_document_count": len(contexts),
            "process_compliance": process_data,
        }
        return result

    def _process_compliance_checks(
        self,
        contexts: list[DocumentContext],
        parsed_docs: list[ParsedDocument],
        system_record: dict,
    ) -> tuple[list[Issue], dict]:
        issues: list[Issue] = []
        report_docs = [
            doc
            for doc in parsed_docs
            if doc.document_subtype == "评标报告" or doc.file_type == "评标报告"
        ]
        procurement_docs = [
            doc
            for doc in parsed_docs
            if doc.document_subtype == "采购文件" or doc.file_type == "招标文件"
        ]

        missing_sections: dict[str, list[str]] = {}
        for doc in report_docs:
            searchable = "\n".join(
                [
                    *(section.title + "\n" + section.content for section in doc.sections),
                    *(item.evidence for item in doc.evaluation_opinions),
                    *(item.evidence for item in doc.candidate_rankings),
                ]
            )
            missing = [
                label
                for label, markers in self.evaluation_required_sections.items()
                if not any(marker in searchable for marker in markers)
            ]
            if missing:
                missing_sections[doc.filename] = missing
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="评标报告必需内容可能缺失",
                        source_file=doc.filename,
                        description=f"未在评标报告中明确识别到：{'、'.join(missing)}。",
                        basis="评标报告应完整记录评标委员会组成、评审过程、评审结果及候选人推荐等关键内容。当前也可能存在解析遗漏。",
                        suggestion="请结合报告目录、正文及附件人工核对；确认缺失时补充对应章节和原始记录。",
                        requires_human_review=True,
                        assessment="待人工判断",
                        confidence=0.65,
                    )
                )

        conflicts = self._field_consistency_issues(parsed_docs, system_record)
        issues.extend(conflicts)

        rejection_checks = 0
        unsupported_rejections = 0
        legal_citation_checks = 0
        unsupported_legal_citations = 0
        procurement_text = "\n".join(
            context.raw_text
            for context in contexts
            if any(context.document_id == doc.file_id for doc in procurement_docs)
        )
        for doc in report_docs:
            for record in doc.rejection_records:
                rejection_checks += 1
                cited_found = bool(record.cited_clause and record.cited_clause in procurement_text)
                reason_terms = self._meaningful_terms(record.reason)
                reason_found = any(term in procurement_text for term in reason_terms)
                if procurement_text and (cited_found or reason_found):
                    pass
                else:
                    unsupported_rejections += 1
                    reason = (
                        "本次任务未同时提供采购文件，无法回查废标依据。"
                        if not procurement_text
                        else "未在采购文件中定位到该废标理由或所引条款。"
                    )
                    issues.append(
                        Issue(
                            agent=self.name,
                            risk_level="高",
                            issue_type="废标依据待核验",
                            source_file=doc.filename,
                            description=f"{record.bidder or '相关投标人'}的废标记录缺少可核验依据：{reason}",
                            basis="废标或否决投标应以采购文件预先载明的实质性要求和法定依据为基础。",
                            suggestion="补充上传对应采购文件，并核对废标理由、采购文件条款及法规依据是否一致。",
                            evidence=[record.evidence] if record.evidence else [],
                            requires_human_review=True,
                            assessment="待人工判断",
                            confidence=0.6,
                        )
                    )

                citations = self._extract_legal_citations(
                    " ".join([record.cited_clause, record.reason, record.evidence])
                )
                for law_name, article in citations:
                    legal_citation_checks += 1
                    if self._citation_supported(law_name, article):
                        continue
                    unsupported_legal_citations += 1
                    issues.append(
                        Issue(
                            agent=self.name,
                            risk_level="高",
                            issue_type="废标法规引用待核验",
                            source_file=doc.filename,
                            description=(
                                f"{record.bidder or '相关投标人'}的废标记录引用"
                                f"《{law_name}》{article}，本地法规库未能确认该引用。"
                            ),
                            basis="法规名称、条款号和条文内容必须与现行法规原文一致。未确认不等同于认定引用错误。",
                            suggestion="请对照现行有效法规原文核对法规名称、条款号、适用范围及条文内容。",
                            evidence=[record.evidence] if record.evidence else [],
                            requires_human_review=True,
                            assessment="待人工判断",
                            confidence=0.65,
                        )
                    )

        return issues, {
            "evaluation_report_count": len(report_docs),
            "procurement_document_count": len(procurement_docs),
            "missing_sections": missing_sections,
            "field_conflict_count": len(conflicts),
            "system_record_field_count": len(system_record),
            "rejection_record_checks": rejection_checks,
            "unsupported_rejection_count": unsupported_rejections,
            "legal_citation_checks": legal_citation_checks,
            "unsupported_legal_citation_count": unsupported_legal_citations,
            "standards": [
                "评标报告必需内容完整性",
                "跨文件基础信息一致性",
                "废标理由与采购文件载明条款一致性",
                "基础信息与业务系统记录一致性",
                "废标法规引用准确性",
            ],
        }

    def _field_consistency_issues(
        self,
        parsed_docs: list[ParsedDocument],
        system_record: dict | None = None,
    ) -> list[Issue]:
        issues: list[Issue] = []
        for field_name, label in self.consistency_fields.items():
            values: dict[str, list[tuple[str, str]]] = {}
            for doc in parsed_docs:
                if field_name == "tenderer":
                    value = doc.tenderer
                    raw_text = doc.extracted_fields.get("tenderer")
                else:
                    raw_text = doc.extracted_fields.get(field_name)
                    value = raw_text.value if raw_text else ""
                normalized = self._normalize_compare_value(value)
                if normalized:
                    values.setdefault(normalized, []).append(
                        (doc.filename, raw_text.raw_text if raw_text else value)
                    )
            expected = (system_record or {}).get(field_name)
            expected_normalized = self._normalize_compare_value(expected)
            if expected_normalized:
                values.setdefault(expected_normalized, []).append(
                    ("业务系统记录", str(expected))
                )
            if len(values) <= 1:
                continue
            samples = [items[0] for items in values.values()]
            issues.append(
                Issue(
                    agent=self.name,
                    risk_level="高",
                    issue_type="跨文件基础信息不一致",
                    source_file="",
                    description=(
                        f"不同项目文件中的{label}不一致："
                        + "；".join(f"{filename}={raw}" for filename, raw in samples)
                    ),
                    basis="评标报告中的项目基础信息应与采购文件、开标记录和其他原始资料保持一致。",
                    suggestion=f"请以原始采购及系统记录为准核实{label}，统一相关文件。",
                    evidence=[raw for _, raw in samples if raw],
                    requires_human_review=True,
                    assessment="明确问题",
                    confidence=0.9,
                )
            )
        return issues

    @staticmethod
    def _extract_legal_citations(text: str) -> list[tuple[str, str]]:
        citations: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for name, article in re.findall(
            r"《([^》]{2,80})》\s*第([一二三四五六七八九十百零〇0-9]+)条",
            text or "",
        ):
            citation = (name.strip(), f"第{article}条")
            if citation not in seen:
                seen.add(citation)
                citations.append(citation)
        return citations

    @staticmethod
    @lru_cache(maxsize=128)
    def _citation_supported(law_name: str, article: str) -> bool:
        root = Path(__file__).resolve().parents[2] / "docs" / "knowledge_base" / "dify_upload"
        normalized_name = law_name.replace("中华人民共和国", "")
        for path in root.glob("*"):
            if normalized_name not in path.name.replace("中华人民共和国", ""):
                continue
            if path.suffix.lower() not in {".txt", ".html", ".htm"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            compact = re.sub(r"\s+", "", content)
            if article in compact:
                return True
        return False

    @staticmethod
    def _normalize_compare_value(value: object) -> str:
        text = re.sub(r"[\s，,。；;:：()（）]", "", str(value)).lower()
        return text.replace("人民币", "").replace("元整", "元")

    @staticmethod
    def _meaningful_terms(reason: str) -> list[str]:
        terms = re.findall(r"[\u4e00-\u9fff]{4,}", reason or "")
        stop = ("未按照", "不符合", "相关规定", "采购文件", "投标文件", "响应文件")
        return [term for term in terms if term not in stop][:8]

    def run(self, parsed_docs: list[ParsedDocument], raw_texts: dict[str, str]) -> AgentResult:
        if dify_client.enabled:
            return self._run_with_dify(parsed_docs, raw_texts)
        return self._run_locally(parsed_docs, raw_texts)

    def _run_with_dify(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
        file_hashes: dict[str, str] | None = None,
    ) -> AgentResult:
        issues: list[Issue] = []
        summaries: list[str] = []
        errors: list[str] = []
        successful_chunks = 0
        total_chunks = 0
        cache_hits = 0
        cache_misses = 0
        coverage_retries = 0
        uncovered_candidates: list[dict[str, str]] = []

        for doc in parsed_docs:
            text = raw_texts.get(doc.file_id, "")
            if not text.strip():
                continue
            chunks = self._split_text(text)
            total_chunks += len(chunks)

            def run_chunk(
                job: tuple[int, str],
            ) -> tuple[int, dict | None, str, bool]:
                index, chunk = job
                file_hash = (file_hashes or {}).get(doc.file_id, "")
                cache_identity = {
                    "file_hash": file_hash,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "chunk_sha256": hashlib.sha256(
                        chunk.encode("utf-8")
                    ).hexdigest(),
                }
                cached = (
                    workflow_result_cache.get(
                        "compliance-review",
                        cache_identity,
                        settings.compliance_workflow_version,
                        settings.ruleset_version,
                    )
                    if file_hash
                    else None
                )
                if cached is not None:
                    return index, cached, "", True
                chunk_input = (
                    f"【合规审查分段 {index}/{len(chunks)}】\n"
                    "请仅审查本分段；evidence 必须逐字引用本分段原文。\n"
                    "不要仅因条款设置期限、材料格式、联合体选择、开启程序或表单填写要求就认定为问题。\n"
                    "若分析结论是与法规一致、形式上合规、未明确违法或只是正常模板说明，"
                    "不得放入 issues。只有存在具体风险依据的条款才输出；依据不足但确需结合项目判断的，"
                    "必须明确写明需人工判断。\n"
                    f"{chunk}"
                )
                try:
                    payload = dify_client.run_document(
                        chunk_input,
                        user=f"agent-{doc.file_id}-{index}",
                    )
                    missing = self._uncovered_candidates(chunk, payload)
                    retry_count = 0
                    if missing:
                        retry_count = 1
                        retry_input = (
                            f"{chunk_input}\n\n"
                            "【候选覆盖补审】\n"
                            "首次审查未覆盖下列高关注候选条款。请逐项判断，不能因候选较多而遗漏；"
                            "正常条款不输出，存在潜在风险但依据不足的条款输出并标记人工复核。\n"
                            + json.dumps(missing, ensure_ascii=False)
                        )
                        retry_payload = dify_client.run_document(
                            retry_input,
                            user=f"agent-{doc.file_id}-{index}-coverage",
                        )
                        payload = self._merge_dify_payloads(payload, retry_payload)
                        missing = self._uncovered_candidates(chunk, payload)
                    payload["_coverage_retry_count"] = retry_count
                    payload["_uncovered_candidates"] = missing
                    if file_hash:
                        workflow_result_cache.set(
                            "compliance-review",
                            cache_identity,
                            settings.compliance_workflow_version,
                            settings.ruleset_version,
                            payload,
                        )
                    return index, payload, "", False
                except DifyWorkflowError as exc:
                    return index, None, str(exc), False

            jobs = list(enumerate(chunks, start=1))
            if len(jobs) == 1:
                chunk_results = [run_chunk(jobs[0])]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(self.max_workers, len(jobs)),
                    thread_name_prefix="dify-compliance-chunk",
                ) as executor:
                    chunk_results = list(executor.map(run_chunk, jobs))

            document_successes = 0
            for index, payload, error, cache_hit in chunk_results:
                if cache_hit:
                    cache_hits += 1
                else:
                    cache_misses += 1
                if error:
                    errors.append(f"{doc.filename} 分段 {index}/{len(chunks)}: {error}")
                    continue
                if payload is None:
                    continue
                successful_chunks += 1
                document_successes += 1
                issues.extend(self._issues_from_dify(doc, payload, text))
                coverage_retries += int(payload.get("_coverage_retry_count", 0))
                for candidate in payload.get("_uncovered_candidates", []):
                    if not isinstance(candidate, dict):
                        continue
                    uncovered_candidates.append(candidate)
                    evidence = str(candidate.get("evidence", "")).strip()
                    if evidence:
                        issues.append(self._coverage_review_issue(doc, candidate))
                if payload.get("summary"):
                    summaries.append(str(payload["summary"]))

            if document_successes == 0:
                local_result = self._run_locally([doc], {doc.file_id: text})
                issues.extend(local_result.issues)

        if total_chunks and successful_chunks == 0:
            local_result = self._run_locally(parsed_docs, raw_texts)
            local_result.data["dify_errors"] = errors
            local_result.data["execution_mode"] = "local_fallback"
            return local_result

        issues = self._deduplicate_issues(issues)
        confirmed_count = sum(issue.assessment == "明确问题" for issue in issues)
        review_count = sum(issue.assessment == "待人工判断" for issue in issues)
        summary = (
            f"Dify 合规审查完成：明确问题 {confirmed_count} 项，"
            f"待人工判断 {review_count} 项。"
        )
        return AgentResult(
            agent=self.name,
            summary=summary,
            issues=issues,
            data={
                "execution_mode": "dify_partial" if errors else "dify",
                "total_chunks": total_chunks,
                "successful_chunks": successful_chunks,
                "dify_errors": errors,
                "confirmed_issue_count": confirmed_count,
                "needs_context_count": review_count,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "coverage_retry_count": coverage_retries,
                "uncovered_candidate_count": len(uncovered_candidates),
                "workflow_version": settings.compliance_workflow_version,
                "ruleset_version": settings.ruleset_version,
            },
        )

    @classmethod
    def _coverage_candidates(cls, text: str) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if len(line) < 8 or len(line) > 500:
                continue
            category = ""
            if (
                ("注册地址" in line or "本市设立" in line or "本地企业" in line)
                and any(word in line for word in ("必须", "不得", "得", "满"))
            ):
                category = "地域或本地条件"
            elif "品牌" in line and any(
                word in line for word in ("必须", "指定", "不接受", "不得分", "得分")
            ):
                category = "品牌限制或品牌评分"
            elif (
                any(word in line for word in ("丰富", "全面深入", "优秀", "良好", "一般", "较差"))
                and re.search(r"得\s*\d+(?:\.\d+)?\s*分", line)
            ):
                category = "主观评分"
            elif (
                any(word in line for word in ("调整", "改变"))
                and any(word in line for word in ("中标候选人", "评标结果", "评审结果"))
            ) or "无需说明理由" in line:
                category = "程序权限"
            elif any(word in line for word in ("注册资本", "成立满", "项目业绩")) and re.search(
                r"\d", line
            ):
                category = "资格门槛"
            if not category:
                continue
            normalized = cls._normalize_clause(line)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append({"evidence": line, "category": category})
        return candidates[:20]

    @classmethod
    def _uncovered_candidates(cls, text: str, payload: dict) -> list[dict[str, str]]:
        returned: list[str] = []
        for item in payload.get("issues", []):
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence", "")
            if isinstance(evidence, list):
                returned.extend(str(value) for value in evidence)
            else:
                returned.append(str(evidence))
        normalized_returned = [cls._normalize_clause(value) for value in returned if value]
        return [
            candidate
            for candidate in cls._coverage_candidates(text)
            if not any(
                cls._normalize_clause(candidate["evidence"]) in value
                or value in cls._normalize_clause(candidate["evidence"])
                for value in normalized_returned
                if value
            )
        ]

    @staticmethod
    def _normalize_clause(value: str) -> str:
        text = re.sub(r"^\s*(?:第?[一二三四五六七八九十百零〇0-9]+[、.．）)])\s*", "", value)
        return re.sub(r"[\s，,。；;：:]", "", text)

    @staticmethod
    def _merge_dify_payloads(first: dict, second: dict) -> dict:
        merged = dict(first)
        issues = [
            *([item for item in first.get("issues", []) if isinstance(item, dict)]),
            *([item for item in second.get("issues", []) if isinstance(item, dict)]),
        ]
        seen: set[tuple[str, str]] = set()
        merged_issues: list[dict] = []
        for item in issues:
            evidence = str(item.get("evidence", "")).strip()
            key = (evidence, str(item.get("issue_type", "")).strip())
            if key in seen:
                continue
            seen.add(key)
            merged_issues.append(item)
        merged["issues"] = merged_issues
        if second.get("summary"):
            merged["summary"] = second["summary"]
        return merged

    def _coverage_review_issue(
        self,
        doc: ParsedDocument,
        candidate: dict[str, str],
    ) -> Issue:
        evidence = str(candidate.get("evidence", "")).strip()
        category = str(candidate.get("category", "候选条款"))
        return Issue(
            agent=self.name,
            risk_level="中",
            issue_type="候选条款审查覆盖不足",
            source_file=doc.filename,
            description=f"{category}候选条款经补审后仍未获得明确审查结论。",
            basis="该条款命中高关注审查模式，但模型未返回可验证结论，不能静默忽略。",
            suggestion="请结合完整采购需求和适用法规对该条款进行人工复核。",
            evidence=[evidence],
            requires_human_review=True,
            assessment="待人工判断",
            confidence=0.55,
        )

    def _issues_from_dify(
        self,
        doc: ParsedDocument,
        payload: dict,
        source_text: str,
    ) -> list[Issue]:
        issues: list[Issue] = []
        for item in payload.get("issues", []):
            if not isinstance(item, dict):
                continue
            if self._is_non_issue(item):
                continue
            evidence = item.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence] if evidence else []
            evidence = [str(value).strip() for value in evidence if str(value).strip()]
            if evidence and any(value not in source_text for value in evidence):
                continue
            if self._is_resolved_by_document_context(doc, item, evidence):
                continue
            risk_level = self._risk_level(item.get("risk_level", "中"))
            assessment, confidence = self._assessment(item)
            requires_human_review = self._as_bool(
                item.get("requires_human_review", False)
            ) or risk_level == "高" or assessment == "待人工判断"
            try:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level=risk_level,
                        issue_type=item.get("issue_type", "Dify 合规审查问题"),
                        source_file=doc.filename,
                        source_location=item.get("source_location", ""),
                        description=item.get("description", ""),
                        basis=item.get("basis", ""),
                        suggestion=item.get("suggestion", ""),
                        evidence=evidence,
                        requires_human_review=requires_human_review,
                        assessment=assessment,
                        confidence=confidence,
                    )
                )
            except Exception:
                continue
        return issues

    @classmethod
    def _is_resolved_by_document_context(
        cls,
        doc: ParsedDocument,
        item: dict,
        evidence: list[str],
    ) -> bool:
        statement = " ".join(
            [
                str(item.get("issue_type", "")),
                str(item.get("description", "")),
                *evidence,
            ]
        )
        if "投标保证金" not in statement:
            return False
        budget_field = doc.extracted_fields.get("budget")
        if not budget_field:
            return False
        budget = cls._money_yuan(budget_field.value)
        evidence_amounts = [cls._money_yuan(value) for value in evidence]
        guarantee = next((value for value in evidence_amounts if value is not None), None)
        if budget is None or guarantee is None or budget <= 0:
            return False
        return guarantee <= budget * 0.02 + 0.01

    @staticmethod
    def _money_yuan(value: object) -> float | None:
        text = str(value).replace(",", "").replace("，", "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*(亿元|万元|元)", text)
        if not match:
            return None
        amount = float(match.group(1))
        multiplier = {"亿元": 100_000_000, "万元": 10_000, "元": 1}[match.group(2)]
        return amount * multiplier

    def _split_text(self, text: str) -> list[str]:
        paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
        if not paragraphs:
            return []
        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for paragraph in paragraphs:
            added = len(paragraph) + (1 if current else 0)
            if current and current_length + added > self.chunk_size:
                completed = "\n".join(current)
                chunks.append(completed)
                overlap = completed[-self.chunk_overlap :].lstrip()
                current = [overlap, paragraph] if overlap else [paragraph]
                current_length = sum(len(value) for value in current) + len(current) - 1
            else:
                current.append(paragraph)
                current_length += added
        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _is_non_issue(item: dict) -> bool:
        statement = " ".join(
            str(item.get(name, ""))
            for name in ("description", "basis", "suggestion")
        )
        non_issue_markers = (
            "形式上合规",
            "未明确违反法律法规",
            "未明确违反相关规定",
            "未明确违反",
            "与候选条款精神一致",
            "与法律规定一致",
            "与法规一致",
            "属于正常模板",
            "不构成合规问题",
            "无需处理",
        )
        return any(marker in statement for marker in non_issue_markers)

    @staticmethod
    def _risk_level(value: object) -> str:
        normalized = str(value).strip().lower()
        return {
            "高": "高",
            "high": "高",
            "中": "中",
            "medium": "中",
            "低": "低",
            "low": "低",
        }.get(normalized, "中")

    @staticmethod
    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "是", "需要"}

    @staticmethod
    def _assessment(item: dict) -> tuple[str, float]:
        statement = " ".join(
            str(item.get(name, ""))
            for name in ("description", "basis", "suggestion")
        )
        uncertainty_markers = (
            "知识库检索依据不足",
            "无法直接判断",
            "无法确认",
            "需结合项目",
            "需结合具体",
            "需进一步核实",
            "需人工复核适用性",
            "可能合理",
            "未提供具体条款",
            "未明确要求",
            "缺乏直接法律依据",
        )
        if any(marker in statement for marker in uncertainty_markers):
            return "待人工判断", 0.55
        if not str(item.get("basis", "")).strip():
            return "待人工判断", 0.45
        return "明确问题", 0.85

    @staticmethod
    def _deduplicate_issues(issues: list[Issue]) -> list[Issue]:
        result: list[Issue] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for issue in issues:
            fingerprint = (
                issue.source_file,
                issue.issue_type,
                tuple(value.strip() for value in issue.evidence),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            result.append(issue)
        return result

    def _run_locally(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
    ) -> AgentResult:
        issues: list[Issue] = []
        risky_keywords = ["唯一", "指定品牌", "指定厂家", "排他", "本地企业", "特定供应商"]
        missing_keywords = ["资格", "评分", "投标保证金", "评标办法"]

        for doc in parsed_docs:
            text = raw_texts.get(doc.file_id, "")
            risky_lines = find_lines(text, risky_keywords, limit=5)
            for line in risky_lines:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="高",
                        issue_type="疑似限制性或排他性条款",
                        source_file=doc.filename,
                        description=f"发现可能影响公平竞争的表述: {line}",
                        basis="招投标文件通常不得设置不合理限制或排他性条件。",
                        suggestion="建议人工核验该条款是否具有合理业务依据，必要时调整表述。",
                        evidence=[line],
                        requires_human_review=True,
                    )
                )

            missing = [keyword for keyword in missing_keywords if keyword not in text]
            if missing:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="关键审查要素可能缺失",
                        source_file=doc.filename,
                        description=f"文件中未明显识别到这些要素: {'、'.join(missing)}。",
                        basis="招投标文件应包含资格、评分、保证金、评标办法等关键内容。",
                        suggestion="建议人工复核文件目录和附件，确认是否存在缺项或解析遗漏。",
                    )
                )

            issues.extend(self._llm_check(doc, text))

        summary = f"合规审查完成，发现 {len(issues)} 项待复核问题。"
        return AgentResult(agent=self.name, summary=summary, issues=issues)

    def _llm_check(self, doc: ParsedDocument, text: str) -> list[Issue]:
        if not llm_client.enabled or not text.strip():
            return []

        system_prompt = (
            "你是招投标合规审查智能体。请只输出 JSON，格式为 "
            '{"issues":[{"risk_level":"高/中/低","issue_type":"","source_location":"",'
            '"description":"","basis":"","suggestion":"","evidence":[""]}]}。'
            "如果没有明确问题，输出 {\"issues\":[]}。"
        )
        user_prompt = (
            f"文件名: {doc.filename}\n"
            "请审查下列招投标文件片段，重点识别限制性条款、排他性条款、关键要素缺失、"
            "评分办法不清晰、资格要求不合理等问题。\n\n"
            f"{text[:12000]}"
        )
        try:
            payload = llm_client.chat_json(system_prompt, user_prompt)
        except Exception:
            return []

        llm_issues: list[Issue] = []
        for item in (payload or {}).get("issues", []):
            try:
                llm_issues.append(
                    Issue(
                        agent=self.name,
                        risk_level=item.get("risk_level", "中"),
                        issue_type=item.get("issue_type", "大模型合规审查问题"),
                        source_file=doc.filename,
                        source_location=item.get("source_location", ""),
                        description=item.get("description", ""),
                        basis=item.get("basis", ""),
                        suggestion=item.get("suggestion", ""),
                        evidence=item.get("evidence", []),
                        requires_human_review=item.get("risk_level") == "高",
                    )
                )
            except Exception:
                continue
        return llm_issues
