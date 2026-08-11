import hashlib
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from statistics import mean

from app.schemas.document_context import DocumentContext
from app.schemas.task import AgentResult, Issue, ParsedDocument
from app.services.dify_client import DifyWorkflowError, dify_client


class AnomalyAnalyzerAgent:
    name = "异常分析智能体"

    # 只有描述具体异常现象时才形成线索。招标文件中的“不得串通投标”等
    # 合规提示属于正常模板文字，不能仅凭关键词判定风险。
    _collusion_patterns = (
        r"(?:投标|响应)文件.{0,20}(?:异常一致|高度一致|大面积雷同)",
        r"(?:报价|投标价).{0,20}(?:规律性差异|异常一致|呈规律|梯度)",
        r"(?:联系人|联系电话|联系地址|电子邮箱).{0,12}(?:相同|一致)",
        r"(?:IP|MAC|机器码|文件作者|制作机器).{0,12}(?:相同|一致)",
        r"(?:不同|多家)(?:投标人|供应商).{0,20}(?:关联关系|由同一人编制|混装)",
        r"(?:发现|存在|涉嫌|疑似).{0,16}(?:围标|陪标|串通投标)",
    )

    def run_contexts(
        self,
        contexts: list[DocumentContext],
        parsed_docs: list[ParsedDocument],
        agent_results: list[AgentResult] | None = None,
    ) -> AgentResult:
        raw_texts = {context.document_id: context.raw_text for context in contexts}
        agent_results = agent_results or []
        local_result = self._run_locally(parsed_docs, raw_texts, contexts)
        if dify_client.anomaly_analyzer_enabled:
            semantic_result = self._run_with_dify(
                parsed_docs,
                raw_texts,
                agent_results,
                contexts,
                local_result,
            )
            result = self._merge_results(local_result, semantic_result)
        else:
            result = local_result
        result.data = {
            **result.data,
            "input_contract": "DocumentContext/1.0.0",
            "input_document_count": len(contexts),
        }
        return result

    def _merge_results(self, local: AgentResult, semantic: AgentResult) -> AgentResult:
        issues: list[Issue] = []
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for issue in [*local.issues, *semantic.issues]:
            key = (issue.issue_type, issue.source_location, tuple(sorted(issue.evidence)))
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)
        return AgentResult(
            agent=self.name,
            summary=f"异常分析完成，发现 {len(issues)} 项需人工复核的异常线索。",
            issues=issues,
            data={**local.data, **semantic.data, "deterministic_anomaly_count": len(local.issues)},
        )

    @classmethod
    def _collusion_evidence(cls, text: str) -> list[str]:
        evidence: list[str] = []
        for pattern in cls._collusion_patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                snippet = match.group(0).strip()
                if snippet and snippet not in evidence:
                    evidence.append(snippet)
                if len(evidence) >= 5:
                    return evidence
        return evidence

    def run(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
        agent_results: list[AgentResult] | None = None,
    ) -> AgentResult:
        agent_results = agent_results or []
        local_result = self._run_locally(parsed_docs, raw_texts)
        if dify_client.anomaly_analyzer_enabled:
            semantic_result = self._run_with_dify(
                parsed_docs,
                raw_texts,
                agent_results,
                deterministic_result=local_result,
            )
            return self._merge_results(local_result, semantic_result)
        return local_result

    def _run_with_dify(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
        agent_results: list[AgentResult],
        contexts: list[DocumentContext] | None = None,
        deterministic_result: AgentResult | None = None,
    ) -> AgentResult:
        parsed_payload = self._parsed_documents_payload(parsed_docs)
        compliance_payload = self._agent_payload(agent_results, "合规审查智能体")
        validation_payload = self._agent_payload(agent_results, "数据核验智能体")
        relationship_payload = (
            self._relationship_context_payload(contexts, parsed_docs)
            if contexts is not None
            else self._relationship_payload(parsed_docs, raw_texts)
        )
        inputs = {
            "parsed_documents": json.dumps(parsed_payload, ensure_ascii=False),
            "compliance_results": json.dumps(compliance_payload, ensure_ascii=False),
            "validation_results": json.dumps(validation_payload, ensure_ascii=False),
            "relationship_data": json.dumps(relationship_payload, ensure_ascii=False),
            "anomaly_context": json.dumps(
                {
                    "summary": deterministic_result.summary if deterministic_result else "",
                    "anomalies": [
                        issue.model_dump(mode="json")
                        for issue in (deterministic_result.issues if deterministic_result else [])
                    ],
                    "metrics": deterministic_result.data if deterministic_result else {},
                },
                ensure_ascii=False,
            ),
        }
        try:
            payload = dify_client.run_anomaly_analyzer(
                **inputs,
                user="agent-anomaly-analysis",
            )
        except DifyWorkflowError as exc:
            result = self._run_locally(parsed_docs, raw_texts)
            result.data.update(
                {"execution_mode": "local_fallback", "dify_errors": [str(exc)]}
            )
            return result

        source_bundle = "\n".join(inputs.values())
        issues = self._issues_from_dify(payload, source_bundle)
        return AgentResult(
            agent=self.name,
            summary=f"Dify 异常分析完成，发现 {len(issues)} 项待人工复核异常线索。",
            issues=issues,
            data={
                "execution_mode": "dify",
                "dify_errors": [],
                "input_document_count": len(parsed_docs),
            },
        )

    @staticmethod
    def _parsed_documents_payload(parsed_docs: list[ParsedDocument]) -> dict:
        return {
            "documents": [
                {
                    "document_id": doc.file_id,
                    "filename": doc.filename,
                    "file_type": doc.file_type,
                    "project_name": doc.project_name,
                    "tenderer": doc.tenderer,
                    "bidders": doc.bidders,
                    "bid_prices": doc.bid_prices,
                    "qualification_requirements": doc.qualification_requirements,
                    "scoring_criteria": doc.scoring_criteria,
                    "key_clauses": doc.key_clauses,
                }
                for doc in parsed_docs
            ]
        }

    @staticmethod
    def _agent_payload(agent_results: list[AgentResult], agent_name: str) -> dict:
        matching = [result for result in agent_results if result.agent == agent_name]
        return {
            "results": [
                {
                    "summary": result.summary,
                    "issues": [issue.model_dump() for issue in result.issues],
                }
                for result in matching
            ]
        }

    @staticmethod
    def _relationship_payload(
        parsed_docs: list[ParsedDocument], raw_texts: dict[str, str]
    ) -> dict:
        contacts: list[dict] = []
        file_metadata: list[dict] = []
        bid_records: list[dict] = []
        for doc in parsed_docs:
            text = raw_texts.get(doc.file_id, "")
            phones = sorted(set(re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)))
            emails = sorted(
                set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
            )
            for phone in phones:
                contacts.append({"document_id": doc.file_id, "phone": phone})
            for email in emails:
                contacts.append({"document_id": doc.file_id, "email": email})
            file_metadata.append(
                {
                    "document_id": doc.file_id,
                    "filename": doc.filename,
                    "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
            for price in doc.bid_prices:
                bid_records.append(
                    {"document_id": doc.file_id, "bid_price_text": price}
                )
        return {
            "suppliers": [],
            "contacts": contacts,
            "addresses": [],
            "relationships": [],
            "file_metadata": file_metadata,
            "network_features": [],
            "bid_records": bid_records,
        }

    @staticmethod
    def _relationship_context_payload(
        contexts: list[DocumentContext], parsed_docs: list[ParsedDocument]
    ) -> dict:
        documents = {document.file_id: document for document in parsed_docs}
        contacts: list[dict] = []
        file_metadata: list[dict] = []
        bid_records: list[dict] = []
        suppliers: list[dict] = []
        for context in contexts:
            contacts.extend(
                {"document_id": context.document_id, "phone": phone}
                for phone in context.entities.contacts
            )
            contacts.extend(
                {"document_id": context.document_id, "email": email}
                for email in context.entities.emails
            )
            file_metadata.append(
                {
                    "document_id": context.document_id,
                    "filename": context.file_name,
                    "content_sha256": context.file_hash,
                    **context.file_metadata,
                }
            )
            suppliers.extend(
                {"document_id": context.document_id, "supplier_name": bidder}
                for bidder in context.entities.bidders
            )
            document = documents.get(context.document_id)
            if document:
                bid_records.extend(
                    {
                        "document_id": context.document_id,
                        "bid_price_text": price,
                    }
                    for price in document.bid_prices
                )
        return {
            "suppliers": suppliers,
            "contacts": contacts,
            "file_metadata": file_metadata,
            "network_features": [],
            "bid_records": bid_records,
        }

    def _issues_from_dify(self, payload: dict, source_bundle: str) -> list[Issue]:
        issues: list[Issue] = []
        seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
        for item in payload.get("anomalies", []):
            if not isinstance(item, dict) or item.get("is_anomaly") is not True:
                continue
            evidence = item.get("evidence", [])
            entities = item.get("related_entities", [])
            if not isinstance(evidence, list) or not isinstance(entities, list):
                continue
            evidence = [str(value).strip() for value in evidence if str(value).strip()]
            entities = [str(value).strip() for value in entities if str(value).strip()]
            if not evidence or any(value not in source_bundle for value in evidence):
                continue
            anomaly_type = str(item.get("anomaly_type", "其他待调查异常"))
            fingerprint = (anomaly_type, tuple(sorted(entities)), tuple(sorted(evidence)))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            risk_level = str(item.get("risk_level", "中"))
            if risk_level not in {"高", "中", "低"}:
                risk_level = "中"
            issues.append(
                Issue(
                    agent=self.name,
                    risk_level=risk_level,
                    issue_type=anomaly_type,
                    source_location="、".join(entities),
                    description=str(item.get("description", "")),
                    basis=str(item.get("basis", "")),
                    suggestion=str(item.get("suggestion", "")),
                    evidence=evidence,
                    requires_human_review=True,
                    assessment="待人工判断",
                    confidence=0.65 if risk_level == "高" else 0.55,
                )
            )
        return issues

    def _run_locally(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
        contexts: list[DocumentContext] | None = None,
    ) -> AgentResult:
        issues: list[Issue] = []

        for doc in parsed_docs:
            text = raw_texts.get(doc.file_id, "")
            evidence = self._collusion_evidence(text)
            if evidence:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="围串标风险线索",
                        source_file=doc.filename,
                        description="文件中出现需要结合投标主体与报价数据进一步核验的围串标异常现象。",
                        basis="围串标风险通常需要结合投标行为、文件相似度、报价规律和主体关系综合判断。",
                        suggestion="建议进一步调用相似度比对、供应商关系分析和报价分布分析工具。",
                        evidence=evidence,
                        requires_human_review=True,
                        assessment="待人工判断",
                        confidence=0.55,
                    )
                )

        issues.extend(self._expert_score_deviations(parsed_docs))
        issues.extend(self._cross_lot_score_differences(parsed_docs))
        issues.extend(self._price_pattern_anomalies(parsed_docs))
        issues.extend(self._shared_identity_anomalies(parsed_docs, raw_texts, contexts))
        issues.extend(self._document_similarity_anomalies(parsed_docs, raw_texts))

        summary = f"异常分析完成，发现 {len(issues)} 项异常或风险线索。"
        return AgentResult(
            agent=self.name,
            summary=summary,
            issues=issues,
            data={
                "execution_mode": "deterministic",
                "standards": [
                    "专家评分偏离度",
                    "同一供应商跨标段得分差异",
                    "报价规律异常",
                    "联系人及文件元数据交叉关联",
                    "响应文件内容相似度",
                ],
            },
        )

    @staticmethod
    def _score_evidence(doc: ParsedDocument, item: object, value: float) -> str:
        source = getattr(item, "source", None)
        row_number = getattr(source, "row", None)
        sheet = getattr(source, "sheet", "")
        if row_number:
            for table in doc.tables:
                if sheet and table.sheet and table.sheet != sheet:
                    continue
                index = row_number - (table.start_row or 1)
                if 0 <= index < len(table.rows):
                    return " | ".join(str(cell).strip() for cell in table.rows[index] if str(cell).strip())
        expert = getattr(item, "expert", "")
        bidder = getattr(item, "bidder", "")
        factor = getattr(item, "factor", "")
        return f"{bidder}，{expert}，{factor}，得分{value:g}"

    def _expert_score_deviations(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        for doc in docs:
            groups: dict[tuple[str, str, str], list] = defaultdict(list)
            for item in doc.score_details:
                if item.expert and item.bidder and item.raw_score is not None:
                    groups[(item.bidder.strip(), item.lot.strip(), item.factor.strip())].append(item)
            for (bidder, lot, factor), rows in groups.items():
                if len(rows) < 3:
                    continue
                for row in rows:
                    peers = [item.raw_score for item in rows if item is not row and item.raw_score is not None]
                    peer_mean = mean(peers)
                    deviation = abs(row.raw_score - peer_mean)
                    max_score = row.max_score or max([item.raw_score for item in rows if item.raw_score is not None] + [0])
                    threshold = max(5.0, max_score * 0.2)
                    if deviation < threshold:
                        continue
                    evidence = [self._score_evidence(doc, item, item.raw_score) for item in rows]
                    issues.append(Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="专家评分显著偏离",
                        source_file=doc.filename,
                        source_location=f"{bidder}/{lot or '未分标段'}/{factor}",
                        description=f"{row.expert}对{bidder}的“{factor}”评分为{row.raw_score:g}分，与其他专家平均分{peer_mean:.2f}分偏离{deviation:.2f}分。",
                        basis=f"确定性偏离度计算；预警阈值为{threshold:.2f}分。该结果仅为异常线索，不代表专家评分不当。",
                        suggestion="结合专家评分理由、评分细则及其他专家意见进行人工复核。",
                        evidence=evidence,
                        requires_human_review=True,
                        assessment="待人工判断",
                        confidence=0.8,
                    ))
        return issues

    def _cross_lot_score_differences(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        records: dict[str, list] = defaultdict(list)
        for doc in docs:
            for item in doc.score_summaries:
                if item.bidder and item.lot and item.total_score is not None:
                    records[item.bidder.strip()].append((doc, item))
        for bidder, rows in records.items():
            lots = {item.lot for _, item in rows}
            scores = [item.total_score for _, item in rows]
            if len(lots) < 2 or max(scores) - min(scores) < 10:
                continue
            evidence = [self._score_evidence(doc, item, item.total_score) for doc, item in rows]
            issues.append(Issue(
                agent=self.name,
                risk_level="中",
                issue_type="同一供应商跨标段得分差异异常",
                source_file="、".join(dict.fromkeys(doc.filename for doc, _ in rows)),
                source_location=bidder,
                description=f"{bidder}在不同标段的总分最大差异为{max(scores)-min(scores):.2f}分。",
                basis="同一供应商跨标段得分差异达到预警阈值10分，仅作为进一步核查线索。",
                suggestion="结合各标段采购需求、响应方案和评分明细核查差异是否合理。",
                evidence=evidence,
                requires_human_review=True,
                assessment="待人工判断",
                confidence=0.7,
            ))
        return issues

    def _price_pattern_anomalies(self, docs: list[ParsedDocument]) -> list[Issue]:
        issues: list[Issue] = []
        records: dict[str, list] = defaultdict(list)
        for doc in docs:
            for item in doc.opening_records:
                if item.bidder and item.bid_price is not None:
                    records[item.lot.strip()].append((doc, item))
        for lot, rows in records.items():
            unique = sorted({round(item.bid_price, 2) for _, item in rows})
            if len(unique) < 3:
                continue
            gaps = [round(unique[i + 1] - unique[i], 2) for i in range(len(unique) - 1)]
            tolerance = max(0.01, abs(gaps[0]) * 0.001)
            if gaps[0] == 0 or not all(abs(gap - gaps[0]) <= tolerance for gap in gaps[1:]):
                continue
            evidence = [self._score_evidence(doc, item, item.bid_price) for doc, item in rows]
            issues.append(Issue(
                agent=self.name,
                risk_level="中",
                issue_type="报价等差规律异常",
                source_file="、".join(dict.fromkeys(doc.filename for doc, _ in rows)),
                source_location=lot or "未分标段",
                description=f"{len(unique)}个报价呈等差排列，相邻差额均约为{gaps[0]:.2f}元。",
                basis="报价规律属于统计异常线索，不能单独作为串通投标结论。",
                suggestion="结合成本构成、文件相似度、主体关系和提交环境进一步人工核查。",
                evidence=evidence,
                requires_human_review=True,
                assessment="待人工判断",
                confidence=0.65,
            ))
        return issues

    def _shared_identity_anomalies(
        self,
        docs: list[ParsedDocument],
        raw_texts: dict[str, str],
        contexts: list[DocumentContext] | None,
    ) -> list[Issue]:
        issues: list[Issue] = []
        values: dict[tuple[str, str], set[str]] = defaultdict(set)
        for doc in docs:
            text = raw_texts.get(doc.file_id, "")
            for phone in set(re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)):
                values[("联系电话", phone)].add(doc.file_id)
            for email in set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)):
                values[("电子邮箱", email.lower())].add(doc.file_id)
        names = {doc.file_id: doc.filename for doc in docs}
        for (kind, value), document_ids in values.items():
            if len(document_ids) < 2:
                continue
            files = [names.get(document_id, document_id) for document_id in sorted(document_ids)]
            issues.append(Issue(
                agent=self.name,
                risk_level="中",
                issue_type="跨文件主体联系信息重合",
                source_file="、".join(files),
                source_location="、".join(files),
                description=f"不同文件中出现相同{kind}：{value}。",
                basis="联系信息重合属于主体关联线索，需排除代理机构、公共联系人或模板信息等合理情形。",
                suggestion="核实相关文件对应供应商、联系人身份及文件编制过程。",
                evidence=[value],
                requires_human_review=True,
                assessment="待人工判断",
                confidence=0.7,
            ))
        return issues

    def _document_similarity_anomalies(
        self, docs: list[ParsedDocument], raw_texts: dict[str, str]
    ) -> list[Issue]:
        issues: list[Issue] = []
        candidates = [(doc, re.sub(r"\s+", "", raw_texts.get(doc.file_id, ""))) for doc in docs]
        candidates = [(doc, text[:50000]) for doc, text in candidates if len(text) >= 300]
        for index, (left, left_text) in enumerate(candidates[:30]):
            for right, right_text in candidates[index + 1:30]:
                ratio = SequenceMatcher(None, left_text, right_text).ratio()
                if ratio < 0.92:
                    continue
                issues.append(Issue(
                    agent=self.name,
                    risk_level="中",
                    issue_type="跨文件内容高度相似",
                    source_file=f"{left.filename}、{right.filename}",
                    source_location=f"{left.filename}、{right.filename}",
                    description=f"两份文件的规范化文本相似度为{ratio:.2%}。",
                    basis="内容高度相似仅为异常线索，应排除统一模板、共同采购需求及法定格式造成的相似。",
                    suggestion="对非模板章节、错别字、排版特征和文件元数据进行进一步比对。",
                    evidence=[],
                    requires_human_review=True,
                    assessment="待人工判断",
                    confidence=0.6,
                ))
        return issues

