from dataclasses import dataclass, field

from app.schemas.task import ParsedDocument


@dataclass
class RoutingDecision:
    selected_agents: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class RoutingAgent:
    name = "任务路由智能体"

    def plan(
        self,
        parsed_docs: list[ParsedDocument],
        raw_texts: dict[str, str],
    ) -> RoutingDecision:
        selected: list[str] = []
        reasons: list[str] = []
        file_types = {doc.file_type for doc in parsed_docs}
        all_text = "\n".join(raw_texts.values())

        if parsed_docs and all(
            self._is_unfilled_template(doc, raw_texts.get(doc.file_id, ""))
            for doc in parsed_docs
        ):
            return RoutingDecision(
                selected_agents=[],
                reasons=["识别到未填写的示范模板，仅执行文档解析，不进行项目风险判定"],
            )

        has_compliance_signals = any(
            marker in file_type
            for file_type in file_types
            for marker in ("招标", "合同", "规则法规")
        ) or any(
            doc.qualification_requirements or doc.key_clauses for doc in parsed_docs
        )
        if has_compliance_signals:
            selected.append("compliance")
            reasons.append("识别到招标、合同、资格要求或关键条款，执行合规审查")

        has_data_signals = (
            any(
                marker in file_type
                for file_type in file_types
                for marker in ("招标", "投标", "评标")
            )
            or any(doc.bid_prices or doc.scoring_criteria for doc in parsed_docs)
            or any(keyword in all_text for keyword in ("预算", "限价", "报价", "分值"))
        )
        if has_data_signals:
            selected.append("data")
            reasons.append("识别到金额、报价或评分数据，执行数据核验")

        has_anomaly_signals = any(
            marker in file_type
            for file_type in file_types
            for marker in ("投标", "评标")
        ) or any(
            keyword in all_text
            for keyword in ("串通", "陪标", "围标", "关联关系", "异常一致", "雷同")
        )
        has_multiple_bid_documents = len(parsed_docs) >= 2 and sum(
            1
            for doc in parsed_docs
            if self._looks_like_bid_document(doc, raw_texts.get(doc.file_id, ""))
        ) >= 2
        if has_multiple_bid_documents:
            has_anomaly_signals = True
        if has_anomaly_signals:
            selected.append("anomaly")
            reasons.append(
                "识别到多份投标/响应文件、评标资料或异常风险特征，执行异常分析"
            )

        if not selected:
            selected = ["compliance", "data", "anomaly"]
            reasons.append("未识别到足够的分类特征，回退为完整核验")

        return RoutingDecision(selected_agents=selected, reasons=reasons)

    @staticmethod
    def _is_unfilled_template(doc: ParsedDocument, text: str) -> bool:
        filename_signal = "示范文本" in doc.filename or "模板" in doc.filename
        placeholder_count = sum(
            text.count(marker)
            for marker in ("□是", "□否", "年 月 日", "（如有）", "填写说明")
        )
        return filename_signal and placeholder_count >= 5

    @staticmethod
    def _looks_like_bid_document(doc: ParsedDocument, text: str) -> bool:
        identity = f"{doc.filename}\n{doc.file_type}\n{text[:2000]}"
        strong_markers = ("投标文件", "响应文件", "投标人", "供应商")
        data_markers = ("投标报价", "响应报价", "联系人电话", "电子邮箱")
        return any(marker in identity for marker in strong_markers) and any(
            marker in identity for marker in data_markers
        )
