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
        relationship_data: dict | None = None,
        enable_dify: bool = True,
    ) -> AgentResult:
        raw_texts = {context.document_id: context.raw_text for context in contexts}
        agent_results = agent_results or []
        local_result = self._run_locally(parsed_docs, raw_texts, contexts)
        if enable_dify and dify_client.anomaly_analyzer_enabled:
            semantic_result = self._run_with_dify(
                parsed_docs,
                raw_texts,
                agent_results,
                contexts,
                local_result,
                relationship_data,
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
        relationship_data: dict | None = None,
    ) -> AgentResult:
        parsed_payload = self._parsed_documents_payload(parsed_docs)
        compliance_payload = self._agent_payload(agent_results, "合规审查智能体")
        validation_payload = self._agent_payload(agent_results, "数据核验智能体")
        relationship_payload = (
            self._relationship_context_payload(contexts, parsed_docs)
            if contexts is not None
            else self._relationship_payload(parsed_docs, raw_texts)
        )
        if relationship_data:
            relationship_payload = self._merge_relationship_data(
                relationship_payload, relationship_data
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
    def _merge_relationship_data(base: dict, external: dict) -> dict:
        """Merge caller-provided relationship signals with derived document signals."""
        merged = dict(base)
        for key, value in external.items():
            if isinstance(value, list):
                current = merged.get(key, [])
                merged[key] = [*current, *value] if isinstance(current, list) else value
            elif value not in (None, ""):
                merged[key] = value
        return merged

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
            semantic_text = " ".join(
                str(item.get(name, ""))
                for name in ("description", "basis", "suggestion")
            )
            # A bid being below the control price while its itemized sum is
            # internally consistent is not, by itself, an anomaly.
            if (
                "报价合理性" in semantic_text
                and "低于" in semantic_text
                and ("合计" in semantic_text or "总价一致" in semantic_text)
            ):
                continue
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
        issues.extend(self._metadata_overlap_anomalies(parsed_docs, raw_texts))
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
            sequences: list[list[float]] = []
            for left in range(len(unique) - 2):
                for middle in range(left + 1, len(unique) - 1):
                    gap = round(unique[middle] - unique[left], 2)
                    if gap <= 0:
                        continue
                    target = round(unique[middle] + gap, 2)
                    if target in unique[middle + 1:]:
                        sequences.append([unique[left], unique[middle], target])
            if not sequences:
                continue
            sequence = max(sequences, key=lambda values: values[0])
            gap = round(sequence[1] - sequence[0], 2)
            matched_rows = [
                (doc, item) for doc, item in rows
                if round(item.bid_price, 2) in sequence
            ]
            evidence = [self._score_evidence(doc, item, item.bid_price) for doc, item in matched_rows]
            issues.append(Issue(
                agent=self.name,
                risk_level="中",
                issue_type="报价等差规律异常",
                source_file="、".join(dict.fromkeys(doc.filename for doc, _ in matched_rows)),
                source_location=lot or "未分标段",
                description=f"检测到3个报价呈等差排列，相邻差额均约为{gap:.2f}元。",
                basis="报价规律属于统计异常线索，不能单独作为串通投标或其他违法行为的结论。",
                suggestion="结合成本构成、文件相似度、主体关系和提交环境进一步人工核查。",
                evidence=evidence,
                requires_human_review=True,
                assessment="待人工判断",
                confidence=0.65,
            ))
        return issues

    def _metadata_overlap_anomalies(
        self, docs: list[ParsedDocument], raw_texts: dict[str, str]
    ) -> list[Issue]:
        """Extract shared submission metadata from uploaded business tables."""
        rows: list[dict[str, str]] = []
        bid_rows: list[dict[str, object]] = []
        source_files: list[str] = []
        for doc in docs:
            text = raw_texts.get(doc.file_id, "")
            if "supplier_code" not in text or "machine_code" not in text:
                continue
            source_files.append(doc.filename)
            in_metadata = False
            in_bids = False
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if line.startswith("【工作表：投标记录】"):
                    in_bids = True
                    in_metadata = False
                    continue
                if line.startswith("【工作表：文件与网络元数据】"):
                    in_metadata = True
                    in_bids = False
                    continue
                if (in_metadata or in_bids) and line.startswith("【工作表："):
                    in_metadata = False
                    in_bids = False
                    continue
                if in_bids and "|" in line and not line.startswith("supplier_code"):
                    cells = [cell.strip() for cell in line.split("|")]
                    if len(cells) >= 3:
                        try:
                            bid_rows.append({"supplier_name": cells[1], "price": float(cells[2]), "raw_line": line})
                        except ValueError:
                            pass
                    continue
                if not in_metadata or "|" not in line or line.startswith("supplier_code"):
                    continue
                cells = [cell.strip() for cell in line.split("|")]
                if len(cells) < 9:
                    continue
                row = dict(zip(
                    ("supplier_code", "supplier_name", "file_author", "created_time", "creation_tool", "upload_ip", "mac_address", "machine_code", "cost_software_lock_id"),
                    cells[:9],
                ))
                row["raw_line"] = line
                rows.append(row)

        by_pair: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        strong_fields = (
            ("upload_ip", "上传IP"),
            ("mac_address", "MAC地址"),
            ("machine_code", "机器码"),
            ("cost_software_lock_id", "造价软件加密锁号"),
            ("file_author", "文件作者"),
            ("creation_tool", "创建工具"),
        )
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                pair = tuple(sorted((left["supplier_name"], right["supplier_name"])))
                for field, label in strong_fields:
                    value = left.get(field, "")
                    if value and value == right.get(field, ""):
                        by_pair[pair].append((label, value))

        issues: list[Issue] = []
        price_sequences: list[list[dict[str, object]]] = []
        ordered_bids = sorted(bid_rows, key=lambda row: float(row["price"]))
        for left in range(len(ordered_bids) - 2):
            for middle in range(left + 1, len(ordered_bids) - 1):
                gap = float(ordered_bids[middle]["price"]) - float(ordered_bids[left]["price"])
                if gap <= 0:
                    continue
                for right in ordered_bids[middle + 1:]:
                    if abs(float(right["price"]) - float(ordered_bids[middle]["price"]) - gap) <= max(0.01, gap * 0.001):
                        price_sequences.append([ordered_bids[left], ordered_bids[middle], right])
        for pair, overlaps in by_pair.items():
            labels = {label for label, _ in overlaps}
            if len(labels) < 3:
                continue
            related_rows = [row["raw_line"] for row in rows if row["supplier_name"] in pair]
            related_sequence = next(
                (
                    sequence for sequence in price_sequences
                    if set(pair).issubset({str(row["supplier_name"]) for row in sequence})
                ),
                None,
            )
            price_evidence = [str(row["raw_line"]) for row in related_sequence] if related_sequence else []
            evidence = [*related_rows[:2], *price_evidence]
            price_note = " 同时检测到包含相关主体的3个报价呈等差排列，该报价规律仅作为统计异常线索。" if price_evidence else ""
            issues.append(Issue(
                agent=self.name,
                risk_level="高",
                issue_type="设备网络与文件元数据组合异常",
                source_file="、".join(source_files),
                source_location="、".join(pair),
                description=f"{pair[0]}与{pair[1]}存在{len(labels)}项提交环境或文件制作元数据重合。{price_note}",
                basis="多个相互独立的设备、网络和文件制作信号同时重合，构成高风险关联线索；报价规律不能单独定性，所有线索均不能直接认定串通投标。",
                suggestion="核查文件编制主体、上传环境、设备使用关系及相关情况说明，形成完整证据链后由人工判断。",
                evidence=evidence,
                requires_human_review=True,
                assessment="待人工判断",
                confidence=0.85,
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
        response_docs = [
            doc for doc in docs
            if doc.document_subtype == "响应文件" or doc.file_type == "投标文件"
        ]
        for doc in response_docs:
            text = raw_texts.get(doc.file_id, "")
            for phone in set(re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)):
                values[("联系电话", phone)].add(doc.file_id)
            for email in set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)):
                values[("电子邮箱", email.lower())].add(doc.file_id)
        names = {doc.file_id: doc.filename for doc in response_docs}
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
        response_docs = [
            doc for doc in docs
            if doc.document_subtype == "响应文件" or doc.file_type == "投标文件"
        ][:30]
        line_sets: dict[str, set[str]] = {}
        line_frequency: dict[str, int] = defaultdict(int)
        for doc in response_docs:
            lines = {
                re.sub(r"\s+", "", line).strip()
                for line in raw_texts.get(doc.file_id, "").splitlines()
                if len(re.sub(r"\s+", "", line).strip()) >= 24
            }
            line_sets[doc.file_id] = lines
            for line in lines:
                line_frequency[line] += 1

        distinctive = {
            document_id: {line for line in lines if line_frequency[line] <= 2}
            for document_id, lines in line_sets.items()
        }
        for index, left in enumerate(response_docs):
            for right in response_docs[index + 1:]:
                shared = sorted(
                    distinctive.get(left.file_id, set()) & distinctive.get(right.file_id, set()),
                    key=lambda value: (-len(value), value),
                )
                # Two identical sentences are common in templated tender
                # responses. Require three distinctive long lines before
                # raising a cross-document similarity clue.
                if len(shared) < 3 or sum(len(value) for value in shared) < 80:
                    continue
                left_text = "\n".join(sorted(distinctive.get(left.file_id, set())))
                right_text = "\n".join(sorted(distinctive.get(right.file_id, set())))
                ratio = SequenceMatcher(None, left_text, right_text).ratio()
                issues.append(Issue(
                    agent=self.name,
                    risk_level="中",
                    issue_type="跨文件内容高度相似",
                    source_file=f"{left.filename}、{right.filename}",
                    source_location=f"{left.filename}、{right.filename}",
                    description=f"两份文件存在{len(shared)}处非通用长文本完全一致，去除多文件共有模板行后的相似度为{ratio:.2%}。",
                    basis="已排除至少三份响应文件共同出现的模板行；剩余非通用长文本重合仅作为异常线索，不能直接认定串通投标。",
                    suggestion="对非模板章节、错别字、排版特征和文件元数据进行进一步比对。",
                    evidence=shared[:5],
                    requires_human_review=True,
                    assessment="待人工判断",
                    confidence=0.6,
                ))
        return issues

