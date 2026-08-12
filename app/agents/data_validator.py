import json
import re
from collections import defaultdict
from math import isclose

from app.schemas.document_context import DocumentContext
from app.schemas.task import AgentResult, Issue, ParsedDocument
from app.services.dify_client import DifyWorkflowError, dify_client


class DataValidatorAgent:
    name = "数据核验智能体"

    def run_contexts(
        self,
        contexts: list[DocumentContext],
        parsed_docs: list[ParsedDocument],
        enable_dify: bool = True,
    ) -> AgentResult:
        raw_texts = {context.document_id: context.raw_text for context in contexts}
        context_map = {context.document_id: context for context in contexts}
        local_result = self._run_locally(parsed_docs, raw_texts)
        if enable_dify and dify_client.data_validator_enabled:
            dify_result = self._run_with_dify(
                parsed_docs, raw_texts, context_map, local_result
            )
            result = self._merge_results(local_result, dify_result)
        else:
            result = local_result
        result.data = {
            **result.data,
            "input_contract": "DocumentContext/1.0.0",
            "input_document_count": len(contexts),
        }
        return result

    def _merge_results(self, local: AgentResult, semantic: AgentResult) -> AgentResult:
        issues = self._deduplicate([*local.issues, *semantic.issues])
        return AgentResult(
            agent=self.name,
            summary=f"数据核验完成，发现 {len(issues)} 项具有证据的数据问题。",
            issues=issues,
            data={**local.data, **semantic.data, "deterministic_issue_count": len(local.issues)},
        )

    def run(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str] | None = None,
    ) -> AgentResult:
        raw_texts = raw_texts or {}
        local_result = self._run_locally(parsed_docs, raw_texts)
        if dify_client.data_validator_enabled:
            semantic_result = self._run_with_dify(
                parsed_docs,
                raw_texts,
                deterministic_result=local_result,
            )
            return self._merge_results(local_result, semantic_result)
        return local_result

    def _run_with_dify(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
        context_map: dict[str, DocumentContext] | None = None,
        deterministic_result: AgentResult | None = None,
    ) -> AgentResult:
        issues: list[Issue] = []
        errors: list[str] = []
        successful_documents = 0

        for doc in parsed_docs:
            source_text = raw_texts.get(doc.file_id, "")
            context = (context_map or {}).get(doc.file_id)
            parsed_payload = json.dumps(
                {
                    "contract_version": context.contract_version if context else "legacy",
                    "document_id": doc.file_id,
                    "project_name": self._context_value(context, "project_name", doc.project_name),
                    "tenderer": context.entities.tenderer if context else doc.tenderer,
                    "bid_prices": doc.bid_prices,
                    "extracted_fields": {
                        name: field.model_dump(mode="json")
                        for name, field in (
                            context.key_fields.items()
                            if context
                            else doc.extracted_fields.items()
                        )
                    },
                    "tables": [
                        table.model_dump(mode="json")
                        for table in (context.tables if context else doc.tables)
                    ],
                },
                ensure_ascii=False,
            )
            try:
                payload = dify_client.run_data_validator(
                    source_text,
                    parsed_payload,
                    json.dumps(
                        {
                            "summary": deterministic_result.summary if deterministic_result else "",
                            "issues": [
                                item.model_dump(mode="json")
                                for item in (deterministic_result.issues if deterministic_result else [])
                            ],
                            "metrics": deterministic_result.data if deterministic_result else {},
                        },
                        ensure_ascii=False,
                    ),
                    user=f"agent-data-{doc.file_id}",
                )
            except DifyWorkflowError as exc:
                errors.append(f"{doc.filename}: {exc}")
                continue
            successful_documents += 1
            issues.extend(self._issues_from_dify(doc, payload, source_text))

        if successful_documents == 0:
            result = self._run_locally(parsed_docs)
            result.data.update(
                {"execution_mode": "local_fallback", "dify_errors": errors}
            )
            return result

        issues = self._deduplicate(issues)
        return AgentResult(
            agent=self.name,
            summary=(
                f"Dify 数据核验完成，发现 {len(issues)} 项具有明确证据的数据问题。"
            ),
            issues=issues,
            data={
                "execution_mode": "dify_partial" if errors else "dify",
                "successful_documents": successful_documents,
                "dify_errors": errors,
            },
        )

    @staticmethod
    def _context_value(
        context: DocumentContext | None,
        field_name: str,
        fallback: str,
    ) -> object:
        if context and field_name in context.key_fields:
            return context.key_fields[field_name].value
        return fallback

    def _issues_from_dify(
        self,
        doc: ParsedDocument,
        payload: dict,
        source_text: str,
    ) -> list[Issue]:
        issues: list[Issue] = []
        for item in payload.get("issues", []):
            if not isinstance(item, dict) or item.get("is_issue") is not True:
                continue
            combined_text = " ".join(
                str(item.get(name, ""))
                for name in ("description", "basis", "suggestion")
            )
            non_issue_markers = (
                "不构成冲突",
                "未发现冲突",
                "无需处理",
                "不存在不一致",
                "数据一致",
                "金额一致",
                "两者一致",
                "与投标总价一致",
                "属于不同字段",
            )
            if any(marker in combined_text for marker in non_issue_markers):
                continue
            value_1 = self._normalized_value(item.get("value_1", ""))
            value_2 = self._normalized_value(item.get("value_2", ""))
            if value_1 and value_2 and value_1 == value_2:
                continue
            evidence = item.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence]
            evidence = [str(value).strip() for value in evidence if str(value).strip()]
            if len(evidence) < 2 or any(value not in source_text for value in evidence):
                continue
            needs_review = bool(item.get("requires_human_review", False))
            risk_level = str(item.get("risk_level", "中"))
            if risk_level not in {"高", "中", "低"}:
                risk_level = "中"
            issues.append(
                Issue(
                    agent=self.name,
                    risk_level=risk_level,
                    issue_type=str(item.get("issue_type", "数据不一致")),
                    source_file=doc.filename,
                    source_location=str(item.get("field_name", "")),
                    description=str(item.get("description", "")),
                    basis=str(item.get("basis", "")),
                    suggestion=str(item.get("suggestion", "")),
                    evidence=evidence,
                    requires_human_review=needs_review or risk_level == "高",
                    assessment="待人工判断" if needs_review else "明确问题",
                    confidence=0.6 if needs_review else 0.9,
                )
            )
        return issues

    @staticmethod
    def _normalized_value(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "")).strip().lower()

    @staticmethod
    def _deduplicate(issues: list[Issue]) -> list[Issue]:
        result: list[Issue] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for issue in issues:
            key = (issue.source_file, issue.issue_type, tuple(issue.evidence))
            if key in seen:
                continue
            seen.add(key)
            result.append(issue)
        return result

    def _run_locally(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str] | None = None,
    ) -> AgentResult:
        issues: list[Issue] = []
        raw_texts = raw_texts or {}

        for doc in parsed_docs:
            if not doc.project_name:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="项目名称未识别",
                        source_file=doc.filename,
                        description="文档解析结果中未识别到项目名称。",
                        basis="核验任务需要项目名称作为跨文件一致性比对依据。",
                        suggestion="建议人工补录项目名称，或检查文件首页和封面解析效果。",
                    )
                )

            if "投标" in doc.file_type and not doc.bid_prices:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="投标报价未识别",
                        source_file=doc.filename,
                        description="投标文件中未识别到报价金额。",
                        basis="报价是投标文件数据核验的关键字段。",
                        suggestion="建议检查报价表、开标一览表或 PDF 表格解析结果。",
                    )
                )

            if "招标" in doc.file_type and not doc.scoring_criteria:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="低",
                        issue_type="评分标准未识别",
                        source_file=doc.filename,
                        description="招标文件中未识别到明显评分标准或评标办法。",
                        basis="评标办法是 AI 评标和专家评分核验的基础。",
                        suggestion="建议人工确认评分办法是否在附件或独立文件中。",
                    )
                )

        issues.extend(self._validate_weighted_scores(parsed_docs))
        issues.extend(self._validate_score_totals(parsed_docs))
        issues.extend(self._validate_rankings(parsed_docs))
        issues.extend(self._validate_cross_document_prices(parsed_docs))
        issues.extend(self._validate_text_price_totals(parsed_docs, raw_texts))
        issues = self._deduplicate(issues)
        summary = f"数据核验完成，发现 {len(issues)} 项数据完整性、计算或一致性问题。"
        return AgentResult(
            agent=self.name,
            summary=summary,
            issues=issues,
            data={
                "execution_mode": "deterministic",
                "standards": [
                    "分项得分与权重折算复算",
                    "评审因素合计与总分复算",
                    "得分排名与中标候选人排序核对",
                    "评标报告与开标记录报价一致性",
                ],
                "score_detail_count": sum(len(doc.score_details) for doc in parsed_docs),
                "score_summary_count": sum(len(doc.score_summaries) for doc in parsed_docs),
                "opening_record_count": sum(len(doc.opening_records) for doc in parsed_docs),
            },
        )

    def _validate_text_price_totals(
        self,
        docs: list[ParsedDocument],
        raw_texts: dict[str, str],
    ) -> list[Issue]:
        issues: list[Issue] = []
        amount_pattern = re.compile(
            r"(?P<label>[^。；\n]{1,40}?)报价(?:为|：|:)?人民币?\s*"
            r"(?P<amount>[0-9,]+(?:\.[0-9]+)?)\s*(?P<unit>万元|元)"
        )
        total_pattern = re.compile(
            r"投标总价(?:为|：|:)?人民币?\s*"
            r"(?P<amount>[0-9,]+(?:\.[0-9]+)?)\s*(?P<unit>万元|元)"
        )
        for doc in docs:
            text = raw_texts.get(doc.file_id, "")
            if not text:
                continue
            total_match = total_pattern.search(text)
            if not total_match:
                continue
            total = self._money_value(total_match.group("amount"), total_match.group("unit"))
            parts: list[tuple[str, float]] = []
            for match in amount_pattern.finditer(text):
                label = match.group("label").strip(" \t：:")
                if "投标总价" in label or any(word in label for word in ("预算", "限价")):
                    continue
                parts.append((match.group(0).strip(), self._money_value(match.group("amount"), match.group("unit"))))
            # Repeated quotations in narrative sections are too ambiguous. Only
            # calculate when at least two distinct component quotation clauses exist.
            unique_parts = list(dict.fromkeys(parts))
            if len(unique_parts) < 2:
                continue
            calculated = sum(value for _, value in unique_parts)
            if isclose(calculated, total, abs_tol=0.01):
                continue
            total_evidence = total_match.group(0).strip()
            issues.append(Issue(
                agent=self.name,
                risk_level="高",
                issue_type="分项报价合计不一致",
                source_file=doc.filename,
                source_location="投标总价",
                description=f"分项报价合计为{calculated:.2f}元，与投标总价{total:.2f}元不一致。",
                basis="确定性金额复算：" + " + ".join(f"{value:.2f}" for _, value in unique_parts) + f" = {calculated:.2f}元。",
                suggestion="核对分项报价及投标总价，修正后确保合计一致。",
                evidence=[*[quote for quote, _ in unique_parts], total_evidence],
                requires_human_review=True,
                confidence=0.99,
            ))
        return issues

    @staticmethod
    def _money_value(amount: str, unit: str) -> float:
        value = float(amount.replace(",", ""))
        return value * 10000 if unit == "万元" else value

    @staticmethod
    def _record_evidence(doc: ParsedDocument, source: object, fallback: str) -> str:
        row_number = getattr(source, "row", None)
        sheet = getattr(source, "sheet", "")
        if row_number:
            for table in doc.tables:
                if sheet and table.sheet and table.sheet != sheet:
                    continue
                start = table.start_row or 1
                index = row_number - start
                if 0 <= index < len(table.rows):
                    return " | ".join(str(cell).strip() for cell in table.rows[index] if str(cell).strip())
        return fallback

    def _validate_weighted_scores(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        for doc in docs:
            for item in doc.score_details:
                if item.raw_score is None or item.weight is None or item.weighted_score is None:
                    continue
                ratio = item.weight / 100 if item.weight > 1 else item.weight
                expected = item.raw_score * ratio
                if isclose(expected, item.weighted_score, abs_tol=0.02):
                    continue
                evidence = self._record_evidence(
                    doc,
                    item.source,
                    f"{item.bidder}，{item.factor}：原始分{item.raw_score}，权重{item.weight}，折算得分{item.weighted_score}",
                )
                issues.append(Issue(
                    agent=self.name,
                    risk_level="高",
                    issue_type="权重折算错误",
                    source_file=doc.filename,
                    source_location=item.factor,
                    description=f"{item.bidder or '该投标人'}的“{item.factor or '评审因素'}”折算得分为{item.weighted_score}，复算值为{expected:.2f}。",
                    basis=f"确定性复算：{item.raw_score} × {ratio:g} = {expected:.2f}。",
                    suggestion="核对原始得分、权重及折算公式，并修正折算得分。",
                    evidence=[evidence],
                    requires_human_review=True,
                    confidence=0.99,
                ))
        return issues

    def _validate_score_totals(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        for doc in docs:
            groups: dict[tuple[str, str], list] = defaultdict(list)
            for detail in doc.score_details:
                if detail.bidder:
                    groups[(detail.bidder.strip(), detail.lot.strip())].append(detail)
            summaries = {(s.bidder.strip(), s.lot.strip()): s for s in doc.score_summaries if s.bidder and s.total_score is not None}
            for key, details in groups.items():
                summary = summaries.get(key)
                if not summary:
                    continue
                # Multiple experts may score the same factor. Average each factor first,
                # then sum the factor scores; a single row naturally keeps its value.
                factors: dict[str, list[float]] = defaultdict(list)
                for detail in details:
                    value = detail.weighted_score if detail.weighted_score is not None else detail.raw_score
                    if value is not None:
                        factors[detail.factor or f"row-{len(factors)}"].append(value)
                calculated = sum(sum(values) / len(values) for values in factors.values())
                if not factors or isclose(calculated, summary.total_score, abs_tol=0.02):
                    continue
                evidence = [self._record_evidence(doc, d.source, f"{d.factor}：{d.weighted_score if d.weighted_score is not None else d.raw_score}") for d in details]
                evidence.append(self._record_evidence(doc, summary.source, f"{summary.bidder}总分：{summary.total_score}"))
                issues.append(Issue(
                    agent=self.name,
                    risk_level="高",
                    issue_type="总分复算不一致",
                    source_file=doc.filename,
                    source_location=summary.bidder,
                    description=f"{summary.bidder}记录总分为{summary.total_score}，按分项得分复算为{calculated:.2f}。",
                    basis="同一投标人的各评审因素得分应与汇总总分一致；存在多位专家时先按评审因素计算平均值。",
                    suggestion="核对评分明细、平均值及汇总公式，修正总分或分项得分。",
                    evidence=evidence[:20],
                    requires_human_review=True,
                    confidence=0.99,
                ))
        return issues

    def _validate_rankings(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        summaries = []
        candidates = []
        for doc in docs:
            summaries.extend((doc, item) for item in doc.score_summaries if item.bidder and item.total_score is not None)
            candidates.extend((doc, item) for item in doc.candidate_rankings if item.bidder and item.rank)
        by_lot: dict[str, list] = defaultdict(list)
        for doc, item in summaries:
            by_lot[item.lot.strip()].append((doc, item))
        for lot, rows in by_lot.items():
            if len(rows) < 2:
                continue
            ordered = sorted(rows, key=lambda pair: (-pair[1].total_score, pair[1].bidder))
            expected = {item.bidder.strip(): index + 1 for index, (_, item) in enumerate(ordered)}
            for doc, item in rows:
                if item.rank is None or item.rank == expected[item.bidder.strip()]:
                    continue
                issues.append(Issue(
                    agent=self.name,
                    risk_level="高",
                    issue_type="得分排名不一致",
                    source_file=doc.filename,
                    source_location=item.bidder,
                    description=f"{item.bidder}总分为{item.total_score}，记录排名为{item.rank}，按总分降序应为第{expected[item.bidder.strip()]}名。",
                    basis="确定性排序复算：在同一标段内按总分从高到低排序。",
                    suggestion="核对总分及排名生成规则，修正排名记录。",
                    evidence=[self._record_evidence(doc, item.source, f"{item.bidder}，总分{item.total_score}，排名{item.rank}")],
                    requires_human_review=True,
                    confidence=0.99,
                ))
        summary_rank = {(item.bidder.strip(), item.lot.strip()): (doc, item) for doc, item in summaries if item.rank}
        for doc, candidate in candidates:
            match = summary_rank.get((candidate.bidder.strip(), candidate.lot.strip()))
            if not match or match[1].rank == candidate.rank:
                continue
            issues.append(Issue(
                agent=self.name,
                risk_level="高",
                issue_type="中标候选人排序不一致",
                source_file=doc.filename,
                source_location=candidate.bidder,
                description=f"{candidate.bidder}候选人排序为第{candidate.rank}名，但评分汇总排名为第{match[1].rank}名。",
                basis="评标报告中的中标候选人排序应与评分汇总排名保持一致；法定例外情形需另行说明。",
                suggestion="核对评分汇总表、评标报告及候选人推荐顺序，并补充合法调整依据（如适用）。",
                evidence=[candidate.evidence or f"{candidate.bidder}，候选人排名{candidate.rank}", self._record_evidence(match[0], match[1].source, f"{match[1].bidder}，评分排名{match[1].rank}")],
                requires_human_review=True,
                confidence=0.95,
            ))
        return issues

    def _validate_cross_document_prices(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        records: dict[tuple[str, str], list] = defaultdict(list)
        for doc in docs:
            for item in doc.opening_records:
                if item.bidder and item.bid_price is not None:
                    records[(item.bidder.strip(), item.lot.strip())].append((doc, item))
        for (bidder, lot), values in records.items():
            distinct = {round(item.bid_price, 2) for _, item in values}
            if len(distinct) < 2:
                continue
            evidence = [self._record_evidence(doc, item.source, f"{bidder}报价：{item.bid_price}") for doc, item in values]
            issues.append(Issue(
                agent=self.name,
                risk_level="高",
                issue_type="跨文件报价不一致",
                source_file="、".join(dict.fromkeys(doc.filename for doc, _ in values)),
                source_location=bidder,
                description=f"{bidder}在不同资料中的报价不一致：{', '.join(str(v) for v in sorted(distinct))}。",
                basis="同一投标人、同一标段在开标记录、投标报价及评标报告中的报价应一致。",
                suggestion="对照开标记录表、报价表和评标报告原件，确认有效报价并统一记录。",
                evidence=evidence[:20],
                requires_human_review=True,
                confidence=0.98,
            ))
        return issues

