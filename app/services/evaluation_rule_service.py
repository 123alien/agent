from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from app.schemas.document_context import DocumentContext
from app.schemas.task import AgentResult, Issue, ParsedDocument


RULESET_VERSION = "evaluation-review-2026.08.12-v3"


RULE_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "P1-02": ("至少两个项目名称来源（文档或业务系统）",),
    "P1-04": ("中标候选人排序", "综合得分及排名"),
    "P1-05": ("中标候选人排序", "应推荐候选人数量"),
    "P1-06": ("中标候选人排序", "综合得分及排名"),
    "P1-07": ("中标候选人排序", "评审价及排名"),
    "P1-10": ("签字日期检测结果",),
    "P1-11": ("应签章位置及视觉检测结果",),
    "P1-12": ("谈判报告应签字位置及视觉检测结果",),
    "P2-09": ("开标记录中的供应商及投标报价",),
    "P3-05": ("同一评分因素至少两名专家的主观评分",),
    "P3-08": ("价格评分明细、权重及加权分",),
    "P3-10": ("商务评分明细、权重及加权分",),
    "P3-12": ("同一标包至少三家有效报价",),
    "P3-13": ("评分明细及综合得分汇总",),
    "P3-14": ("评审价记录及汇总",),
    "P3-17": ("同一标包至少三家有效报价",),
    "P3-18": ("评审价记录及汇总",),
}


@dataclass(frozen=True)
class ReviewRule:
    rule_id: str
    group_code: str
    group_name: str
    category: str
    item: str
    owner_agent: str
    risk_level: str
    execution_mode: str = "确定性规则"
    active: bool = True
    conditional: bool = False


def _rules(
    group_code: str,
    group_name: str,
    rows: list[tuple[str, str, str, str, str, str, bool, bool]],
) -> list[ReviewRule]:
    return [
        ReviewRule(rule_id, group_code, group_name, category, item, owner, risk,
                   execution_mode, active, conditional)
        for rule_id, category, item, owner, risk, execution_mode, active, conditional in rows
    ]


RULES: tuple[ReviewRule, ...] = tuple(
    _rules("P1", "项目基本信息 + 评标结果与推荐 + 报告签章与附件", [
        ("P1-01", "项目基本信息", "包编号", "合规审查智能体", "低", "条件确定性规则", True, True),
        ("P1-02", "项目基本信息", "项目名称", "合规审查智能体", "中", "字段一致性比对", True, False),
        ("P1-03", "项目基本信息", "项目编号", "合规审查智能体", "中", "可配置格式校验", True, True),
        ("P1-04", "评标结果与推荐", "中标候选人与排名对应", "数据核验智能体", "高", "确定性排序复算", True, False),
        ("P1-05", "评标结果与推荐", "中标候选人数量", "数据核验智能体", "中", "确定性计数", True, False),
        ("P1-06", "评标结果与推荐", "综合得分排名", "数据核验智能体", "高", "确定性排序复算", True, False),
        ("P1-07", "评标结果与推荐", "评审价排名", "数据核验智能体", "高", "确定性排序复算", True, False),
        ("P1-08", "评标结果与推荐", "评标情况说明", "合规审查智能体", "中", "事件触发语义核验", True, True),
        ("P1-09", "报告签章与附件", "必备章节完整性", "合规审查智能体", "中", "模板完整性核验", True, True),
        ("P1-10", "报告签章与附件", "签字日期", "文档解析智能体", "中", "视觉检测与字段比对", True, False),
        ("P1-11", "报告签章与附件", "评委签章完整性", "文档解析智能体", "高", "视觉检测与人工复核", True, False),
        ("P1-12", "报告签章与附件", "谈判报告签字完整性", "文档解析智能体", "中", "视觉检测与人工复核", True, True),
    ])
    + _rules("P2", "招标公告与发售情况 + 开标情况 + 评标委员会组成", [
        ("P2-01", "招标公告与发售情况", "发售起止日期", "合规审查智能体", "中", "日期逻辑比对", True, False),
        ("P2-02", "招标公告与发售情况", "开标大会信息", "合规审查智能体", "中", "字段一致性比对", True, False),
        ("P2-03", "招标公告与发售情况", "递交响应文件供应商数量", "合规审查智能体", "中", "采购方式适配计数", True, False),
        ("P2-04", "招标公告与发售情况", "递交响应文件截止时间", "合规审查智能体", "中", "日期逻辑比对", True, False),
        ("P2-05", "招标公告与发售情况", "采购公告发布日期", "合规审查智能体", "中", "日期一致性比对", True, False),
        ("P2-06", "开标情况", "工期/交货期/服务期记录", "数据核验智能体", "中", "项目类型适配比对", True, True),
        ("P2-07", "开标情况", "开标地点", "合规审查智能体", "中", "字段完整性核验", True, False),
        ("P2-08", "开标情况", "开标时间", "合规审查智能体", "中", "日期逻辑比对", True, False),
        ("P2-09", "开标情况", "投标报价记录", "数据核验智能体", "高", "报价完整性与复算", True, False),
        ("P2-10", "评标委员会组成", "评委是否具备应回避情形", "异常分析智能体", "中", "关联核验与人工裁定", True, True),
        ("P2-11", "评标委员会组成", "评标委员会总人数", "合规审查智能体", "中", "采购方式适配计数", True, False),
    ])
    + _rules("P3", "资格审查 + 符合性审查 + 详细评审/评审结果汇总", [
        ("P3-01", "资格审查", "实质性响应供应商数量", "合规审查智能体", "中", "确定性计数", True, False),
        ("P3-02", "资格审查", "资格审查不通过记录", "合规审查智能体", "高", "依据回查与人工复核", True, False),
        ("P3-03", "符合性审查", "实质性响应供应商数量", "合规审查智能体", "中", "确定性计数", True, False),
        ("P3-04", "符合性审查", "符合性审查不通过记录", "合规审查智能体", "高", "依据回查与人工复核", True, False),
        ("P3-05", "详细评审", "专家评分偏离度（主观分）", "异常分析智能体", "中", "统计偏离预警", True, False),
        ("P3-06", "详细评审", "严重不平衡报价识别", "异常分析智能体", "中", "工程专项预警", True, True),
        ("P3-07", "详细评审", "中标候选人得分差距异常（倾向性）", "异常分析智能体", "中", "可配置阈值预警", True, True),
        ("P3-08", "详细评审", "价格得分计算准确性", "数据核验智能体", "高", "确定性公式复算", True, False),
        ("P3-09", "详细评审", "同一标包报价偏差（异常低价投标识别）", "异常分析智能体", "中", "制度适配触发", True, True),
        ("P3-10", "详细评审", "商务得分计算准确性（客观分）", "数据核验智能体", "高", "确定性公式复算", True, False),
        ("P3-11", "详细评审", "技术评分畸高畸低识别（主观分）", "异常分析智能体", "中", "归一化统计预警", True, True),
        ("P3-12", "详细评审", "报价规律异常识别", "异常分析智能体", "中", "报价模式预警", True, False),
        ("P3-13", "详细评审", "综合得分计算准确性", "数据核验智能体", "高", "确定性公式复算", True, False),
        ("P3-14", "详细评审", "评审价准确性、完整性", "数据核验智能体", "高", "字段与排序复算", True, False),
        ("P3-15", "评审结果汇总", "严重不平衡报价识别", "异常分析智能体", "中", "合并至P3-06", False, True),
        ("P3-16", "评审结果汇总", "同一标包报价偏差（异常低价投标识别）", "异常分析智能体", "中", "合并至P3-09", False, True),
        ("P3-17", "评审结果汇总", "报价规律异常识别", "异常分析智能体", "中", "报价模式预警", True, False),
        ("P3-18", "评审结果汇总", "评审价准确性、完整性", "数据核验智能体", "高", "字段与排序复算", True, False),
    ])
)


ISSUE_MATCHERS: dict[str, tuple[str, ...]] = {
    "P1-02": ("项目名称", "基础信息不一致"),
    "P1-04": ("中标候选人排序",), "P1-05": ("候选人数量",),
    "P1-06": ("得分排名",), "P1-07": ("评审价排名",),
    "P1-09": ("必需内容", "章节"), "P1-10": ("签字日期",),
    "P1-11": ("签章", "签名"), "P1-12": ("签字", "签名"),
    "P2-06": ("服务期限", "交货期", "工期"),
    "P2-07": ("开标地点",), "P2-08": ("开标时间",),
    "P2-09": ("报价", "金额"), "P2-10": ("回避", "关联关系"),
    "P2-11": ("评标委员会", "评委人数"),
    "P3-02": ("资格审查", "废标依据"), "P3-04": ("符合性审查", "废标依据"),
    "P3-05": ("专家评分偏离",), "P3-06": ("不平衡报价",),
    "P3-07": ("得分差距", "倾向性"), "P3-08": ("价格得分",),
    "P3-09": ("异常低价", "报价偏差"), "P3-10": ("商务得分",),
    "P3-11": ("评分畸高", "评分畸低", "专家评分偏离"),
    "P3-12": ("报价规律",), "P3-13": ("总分复算", "综合得分"),
    "P3-14": ("评审价",), "P3-17": ("报价规律",), "P3-18": ("评审价",),
}


def public_rule_catalog() -> dict:
    groups = []
    for code in ("P1", "P2", "P3"):
        rows = [rule for rule in RULES if rule.group_code == code]
        groups.append({
            "group_code": code,
            "group_name": rows[0].group_name,
            "rule_count": len(rows),
            "active_rule_count": sum(rule.active for rule in rows),
            "rules": [rule.__dict__ for rule in rows],
        })
    return {
        "version": RULESET_VERSION,
        "total_rules": len(RULES),
        "active_rules": sum(rule.active for rule in RULES),
        "groups": groups,
    }


class EvaluationRuleService:
    @staticmethod
    def _source_label(filename: str, source: Any) -> str:
        parts = [filename]
        if getattr(source, "sheet", ""):
            parts.append(f"工作表 {source.sheet}")
        if getattr(source, "page", None):
            parts.append(f"第{source.page}页")
        if getattr(source, "row", None):
            parts.append(f"第{source.row}行")
        return " / ".join(parts)

    @staticmethod
    def _base_execution(rule: ReviewRule) -> dict:
        required = list(RULE_REQUIRED_INPUTS.get(rule.rule_id, ()))
        return {
            "status": "insufficient_data",
            "reason": "缺少完成该规则核验所需的可比数据或明确执行依据。",
            "required_inputs": required or [f"{rule.item}对应的结构化字段、原始证据和判定标准"],
            "missing_inputs": required or [f"{rule.item}的专用结构化输入或判定标准"],
            "execution_evidence": [],
            "calculation": "",
        }

    def _execute_rule(
        self, rule: ReviewRule, docs: list[ParsedDocument], system_record: dict
    ) -> dict:
        result = self._base_execution(rule)

        if rule.rule_id == "P1-02":
            values: list[tuple[str, str]] = []
            for key in ("project_name", "项目名称"):
                value = str(system_record.get(key, "")).strip()
                if value:
                    values.append((value, f"业务系统字段 {key}"))
            for doc in docs:
                field = doc.extracted_fields.get("project_name")
                value = str(field.value if field else doc.project_name).strip()
                if value:
                    source = field.source_location if field else "文档解析结果"
                    values.append((value, f"{doc.filename} / {source}"))
            unique = {value for value, _ in values}
            if len(values) < 2:
                return result
            result.update(
                missing_inputs=[],
                execution_evidence=[f"{source}：{value}" for value, source in values],
                calculation=f"比较 {len(values)} 个来源，得到 {len(unique)} 个不同项目名称。",
            )
            if len(unique) == 1:
                result.update(status="passed", reason="多个独立来源中的项目名称一致。")
            else:
                result.update(status="human_review", reason="多个来源中的项目名称不一致，需人工确认正确值。")
            return result

        rankings = [item for doc in docs for item in doc.candidate_rankings if item.bidder and item.rank]
        summaries = [item for doc in docs for item in doc.score_summaries if item.bidder and item.total_score is not None]
        if rule.rule_id in {"P1-04", "P1-06"}:
            if not rankings or not summaries:
                return result
            expected = sorted(summaries, key=lambda item: (-float(item.total_score), item.bidder))
            expected_ranks = {item.bidder: index + 1 for index, item in enumerate(expected)}
            actual_ranks = {item.bidder: int(item.rank) for item in rankings}
            common = sorted(set(expected_ranks) & set(actual_ranks))
            if not common:
                return result
            mismatches = [name for name in common if expected_ranks[name] != actual_ranks[name]]
            result.update(
                missing_inputs=[],
                execution_evidence=[
                    f"{name}：报告排名 {actual_ranks[name]}，按综合得分复算排名 {expected_ranks[name]}"
                    for name in common
                ],
                calculation="按综合得分降序重排，并与候选人报告排名逐一比较。",
            )
            if mismatches:
                result.update(status="human_review", reason=f"发现 {len(mismatches)} 家供应商排名不一致。")
            else:
                result.update(status="passed", reason=f"已复算并核对 {len(common)} 家供应商，排名一致。")
            return result

        if rule.rule_id == "P1-05":
            expected_count = system_record.get("candidate_count") or system_record.get("中标候选人数量")
            if not rankings or expected_count in (None, ""):
                return result
            actual = len({(item.lot, item.bidder) for item in rankings})
            try:
                expected = int(expected_count)
            except (TypeError, ValueError):
                return result
            result.update(
                missing_inputs=[], execution_evidence=[f"报告候选人 {actual} 名", f"基准要求 {expected} 名"],
                calculation=f"候选人去重计数={actual}，基准数量={expected}。",
                status="passed" if actual == expected else "human_review",
                reason="候选人数量符合基准要求。" if actual == expected else "候选人数量与基准要求不一致。",
            )
            return result

        checks = [check for doc in docs for check in doc.seal_signature_checks if check.expected]
        if rule.rule_id in {"P1-10", "P1-11", "P1-12"}:
            filtered = checks
            if rule.rule_id == "P1-10":
                filtered = [item for item in checks if "日期" in f"{item.target}{item.source_text}"]
            elif rule.rule_id == "P1-12":
                filtered = [item for item in checks if "谈判" in f"{item.target}{item.source_text}"]
            if not filtered:
                return result
            bad = [item for item in filtered if item.status != "detected"]
            result.update(
                missing_inputs=[],
                execution_evidence=[f"{item.target or item.source_text}：{item.status}（置信度 {item.confidence:.2f}）" for item in filtered],
                calculation=f"核验 {len(filtered)} 个应检测位置，其中 {len(bad)} 个未达到 detected。",
                status="human_review" if bad else "passed",
                reason=f"存在 {len(bad)} 个未检测、未检查、低置信度或不一致位置。" if bad else "所有应检测位置均已检测到。",
            )
            return result

        openings = [item for doc in docs for item in doc.opening_records]
        if rule.rule_id == "P2-09":
            valid = [item for item in openings if item.bidder and item.bid_price is not None]
            if not valid:
                return result
            incomplete = [item for item in openings if not item.bidder or item.bid_price is None]
            result.update(
                missing_inputs=[],
                execution_evidence=[f"{item.bidder}：{item.bid_price:g}" for item in valid],
                calculation=f"共识别 {len(openings)} 条开标记录，{len(valid)} 条包含供应商和报价。",
                status="human_review" if incomplete else "passed",
                reason=f"存在 {len(incomplete)} 条报价记录不完整。" if incomplete else "投标报价记录字段完整。",
            )
            return result

        score_details = [item for doc in docs for item in doc.score_details]
        if rule.rule_id == "P3-05":
            groups: dict[tuple[str, str, str], list[float]] = {}
            for item in score_details:
                if item.bidder and item.expert and item.factor and item.raw_score is not None:
                    groups.setdefault((item.lot, item.bidder, item.factor), []).append(float(item.raw_score))
            groups = {key: scores for key, scores in groups.items() if len(scores) >= 2}
            if not groups:
                return result
            warnings = []
            evidence = []
            for (_, bidder, factor), scores in groups.items():
                average = sum(scores) / len(scores)
                deviation = max(abs(score - average) for score in scores)
                evidence.append(f"{bidder}/{factor}：{scores}，平均值 {average:.2f}，最大偏离 {deviation:.2f}")
                if deviation > max(3.0, abs(average) * 0.30):
                    warnings.append((bidder, factor))
            result.update(
                missing_inputs=[], execution_evidence=evidence[:12],
                calculation="按供应商和评分因素分组，计算专家评分平均值及最大绝对偏离；预警阈值=max(3分,平均值×30%)。",
                status="human_review" if warnings else "passed",
                reason=f"发现 {len(warnings)} 组专家评分显著偏离。" if warnings else f"已计算 {len(groups)} 组专家评分，未超过预警阈值。",
            )
            return result

        if rule.rule_id in {"P3-08", "P3-10"}:
            keyword = "价格" if rule.rule_id == "P3-08" else "商务"
            rows = [item for item in score_details if keyword in item.factor]
            calculable = [item for item in rows if item.raw_score is not None and item.weight is not None and item.weighted_score is not None]
            if not calculable:
                return result
            mismatches = []
            evidence = []
            for item in calculable:
                expected = float(item.raw_score) * float(item.weight)
                diff = abs(expected - float(item.weighted_score))
                evidence.append(f"{item.bidder}/{item.factor}：{item.raw_score:g}×{item.weight:g}={expected:.4f}，记录值={item.weighted_score:g}")
                if diff > 0.01:
                    mismatches.append(item)
            result.update(
                missing_inputs=[], execution_evidence=evidence[:12], calculation="逐项复算 raw_score × weight，并按0.01分容差比较加权分。",
                status="human_review" if mismatches else "passed",
                reason=f"发现 {len(mismatches)} 条{keyword}得分复算差异。" if mismatches else f"已复算 {len(calculable)} 条{keyword}得分，结果一致。",
            )
            return result

        if rule.rule_id == "P3-13":
            if not score_details or not summaries:
                return result
            totals: dict[tuple[str, str], float] = {}
            for item in score_details:
                value = item.weighted_score if item.weighted_score is not None else item.raw_score
                if item.bidder and value is not None:
                    totals[(item.lot, item.bidder)] = totals.get((item.lot, item.bidder), 0.0) + float(value)
            comparable = [item for item in summaries if (item.lot, item.bidder) in totals]
            if not comparable:
                return result
            mismatches = [item for item in comparable if abs(totals[(item.lot, item.bidder)] - float(item.total_score)) > 0.01]
            evidence = [f"{item.bidder}：明细合计 {totals[(item.lot, item.bidder)]:.2f}，汇总 {item.total_score:.2f}" for item in comparable]
            result.update(
                missing_inputs=[], execution_evidence=evidence[:12], calculation="按供应商汇总加权分（缺少加权分时使用原始分），与综合得分按0.01分容差比较。",
                status="human_review" if mismatches else "passed",
                reason=f"发现 {len(mismatches)} 家综合得分复算不一致。" if mismatches else f"已复算 {len(comparable)} 家综合得分，结果一致。",
            )
            return result

        if rule.rule_id in {"P3-12", "P3-17"}:
            by_lot: dict[str, list[float]] = {}
            for item in openings:
                if item.bid_price is not None:
                    by_lot.setdefault(item.lot, []).append(float(item.bid_price))
            eligible = {lot: sorted(set(values)) for lot, values in by_lot.items() if len(set(values)) >= 3}
            if not eligible:
                return result
            patterns = []
            evidence = []
            for lot, values in eligible.items():
                gaps = [round(values[index + 1] - values[index], 6) for index in range(len(values) - 1)]
                evidence.append(f"标包 {lot or '默认'}：报价 {values}，相邻差额 {gaps}")
                if gaps and max(gaps) - min(gaps) <= 0.01:
                    patterns.append(lot)
            result.update(
                missing_inputs=[], execution_evidence=evidence, calculation="对同一标包有效报价排序，计算相邻差额并识别等差模式；该结果仅作为异常线索。",
                status="human_review" if patterns else "passed",
                reason=f"发现 {len(patterns)} 个标包报价呈等差规律，需人工复核。" if patterns else f"已检查 {len(eligible)} 个标包，未发现等差报价模式。",
            )
            return result

        return result
    @staticmethod
    def _procurement_method(
        contexts: list[DocumentContext], docs: list[ParsedDocument], system_record: dict
    ) -> tuple[str, str]:
        for key in ("procurement_method", "purchase_method", "采购方式"):
            value = str(system_record.get(key, "")).strip()
            if value:
                return value, f"业务系统字段:{key}"
        for doc in docs:
            field = doc.extracted_fields.get("procurement_method")
            if field and str(field.value).strip():
                return str(field.value).strip(), f"{doc.filename}:{field.source_location or '关键字段'}"
        text = "\n".join(context.raw_text for context in contexts)
        for method in ("公开招标", "邀请招标", "竞争性谈判", "竞争性磋商", "询价", "单一来源"):
            if method in text:
                return method, "文档原文识别"
        return "未识别", "未提供明确采购方式"

    @staticmethod
    def _conditional_applies(rule: ReviewRule, procurement_method: str, text: str) -> bool:
        if not rule.conditional:
            return True
        if rule.rule_id == "P1-01":
            return any(marker in text for marker in ("包编号", "包号", "标包"))
        if rule.rule_id == "P1-12":
            return "谈判" in procurement_method or "谈判报告" in text
        if rule.rule_id in {"P3-06", "P3-09"}:
            return any(marker in text for marker in ("工程量清单", "不平衡报价", "异常低价"))
        if rule.rule_id == "P2-10":
            return any(marker in text for marker in ("评标委员会", "评审专家", "回避"))
        return True

    def build_execution_plan(
        self,
        contexts: list[DocumentContext],
        docs: list[ParsedDocument],
        system_record: dict,
        check_type: str = "full",
    ) -> dict:
        text = "\n".join(context.raw_text for context in contexts)
        procurement_method, method_source = self._procurement_method(contexts, docs, system_record)
        matched = [
            rule for rule in RULES
            if rule.active
            and self._conditional_applies(rule, procurement_method, text)
        ]
        allowed_by_check = {
            "compliance": {"合规审查智能体"},
            "data": {"数据核验智能体"},
            "anomaly": {"异常分析智能体"},
        }.get(check_type.strip().lower())
        if allowed_by_check:
            matched = [rule for rule in matched if rule.owner_agent in allowed_by_check]
        executable = [rule for rule in matched if self._has_input(rule, docs, text)]
        owner_to_node = {
            "合规审查智能体": "compliance",
            "数据核验智能体": "data",
            "异常分析智能体": "anomaly",
        }
        selected_agents = []
        for rule in executable:
            node = owner_to_node.get(rule.owner_agent)
            if node and node not in selected_agents:
                selected_agents.append(node)
        phases = [
            {
                "uc_id": "UC-04", "name": "文本内容核对",
                "agents": ["compliance"],
                "rule_ids": [r.rule_id for r in matched if r.owner_agent == "合规审查智能体"],
            },
            {
                "uc_id": "UC-05", "name": "分值复算与价格分预警",
                "agents": ["data", "anomaly"],
                "rule_ids": [r.rule_id for r in matched if r.rule_id in {"P2-09", "P3-06", "P3-08", "P3-09", "P3-12", "P3-13", "P3-14", "P3-17", "P3-18"}],
            },
            {
                "uc_id": "UC-06", "name": "客观评分一致性核对",
                "agents": ["data"],
                "rule_ids": [r.rule_id for r in matched if r.rule_id in {"P1-04", "P1-05", "P1-06", "P1-07", "P3-10"}],
            },
            {
                "uc_id": "UC-07", "name": "主观评分偏离度监测",
                "agents": ["anomaly"],
                "rule_ids": [r.rule_id for r in matched if r.rule_id in {"P2-10", "P3-05", "P3-07", "P3-11"}],
            },
        ]
        return {
            "ruleset_version": RULESET_VERSION,
            "procurement_method": procurement_method,
            "procurement_method_source": method_source,
            "matched_rule_ids": [rule.rule_id for rule in matched],
            "matched_rule_count": len(matched),
            "executable_rule_count": len(executable),
            "selected_agents": selected_agents,
            "phases": phases,
            "decision": "按采购方式与资料条件匹配规则后调度专项智能体",
        }

    @staticmethod
    def _has_input(rule: ReviewRule, docs: list[ParsedDocument], text: str) -> bool:
        if rule.category == "项目基本信息":
            return bool(docs)
        if rule.category == "评标结果与推荐":
            return any(doc.candidate_rankings or doc.score_summaries for doc in docs)
        if rule.category == "报告签章与附件":
            return any(doc.document_subtype == "评标报告" for doc in docs)
        if rule.category == "招标公告与发售情况":
            return "公告" in text or "发售" in text
        if rule.category == "开标情况":
            return "开标" in text or any(doc.opening_records for doc in docs)
        if rule.category == "评标委员会组成":
            return any(marker in text for marker in ("评标委员会", "评审委员会", "评委", "专家"))
        if rule.category in {"资格审查", "符合性审查"}:
            return rule.category in text
        if rule.category in {"详细评审", "评审结果汇总"}:
            return any(doc.score_details or doc.score_summaries or doc.opening_records for doc in docs)
        return False

    @staticmethod
    def _matched_issues(rule: ReviewRule, issues: Iterable[Issue]) -> list[Issue]:
        markers = ISSUE_MATCHERS.get(rule.rule_id, (rule.item,))
        return [
            issue for issue in issues
            if any(marker in f"{issue.issue_type} {issue.description}" for marker in markers)
        ]

    def evaluate(
        self,
        contexts: list[DocumentContext],
        docs: list[ParsedDocument],
        agent_results: list[AgentResult],
        issues: list[Issue],
        execution_plan: dict | None = None,
        system_record: dict | None = None,
    ) -> dict:
        text = "\n".join(context.raw_text for context in contexts)
        rows: list[dict] = []
        matched_rule_ids = set((execution_plan or {}).get("matched_rule_ids", []))
        for rule in RULES:
            matches = self._matched_issues(rule, issues)
            execution = self._base_execution(rule)
            if not rule.active:
                status, reason = "disabled", f"重复规则已停用，执行结果复用{rule.execution_mode.replace('合并至', '')}。"
            elif execution_plan is not None and rule.rule_id not in matched_rule_ids:
                status, reason = "not_applicable", "按采购方式、核验类型或触发条件，本次任务不适用该规则。"
            elif matches:
                status = "human_review" if any(x.final_status == "human_review" for x in matches) else "confirmed_issue"
                reason = f"命中{len(matches)}项现有智能体结果。"
            else:
                execution = self._execute_rule(rule, docs, system_record or {})
                status, reason = execution["status"], execution["reason"]
            rows.append({
                **rule.__dict__, "status": status, "reason": reason,
                "issue_ids": [issue.issue_id for issue in matches if issue.issue_id],
                "evidence": [value for issue in matches for value in issue.evidence][:6],
                "required_inputs": execution["required_inputs"],
                "missing_inputs": [] if matches else execution["missing_inputs"],
                "execution_evidence": execution["execution_evidence"],
                "calculation": execution["calculation"],
            })
        counts = Counter(row["status"] for row in rows)
        group_summaries = []
        for code in ("P1", "P2", "P3"):
            subset = [row for row in rows if row["group_code"] == code]
            group_summaries.append({
                "group_code": code, "group_name": subset[0]["group_name"],
                "rule_count": len(subset),
                "status_counts": dict(Counter(row["status"] for row in subset)),
                "rules": subset,
            })
        return {
            "version": RULESET_VERSION,
            "ruleset_version": RULESET_VERSION,
            "rule_count": len(rows),
            "active_rule_count": sum(rule.active for rule in RULES),
            "status_counts": dict(counts),
            "summary": {
                "total": len(rows),
                "executed": counts["passed"] + counts["confirmed_issue"] + counts["human_review"],
                "passed": counts["passed"],
                "confirmed_issue": counts["confirmed_issue"],
                "human_review": counts["human_review"],
                "insufficient_data": counts["insufficient_data"],
                "disabled": counts["disabled"],
                "not_applicable": counts["not_applicable"],
            },
            "groups": group_summaries,
            "results": rows,
            "agent_result_count": len(agent_results),
            "status_note": "资料不足未执行不等于通过；待人工复核与明确问题沿用系统统一三态口径。",
        }


evaluation_rule_service = EvaluationRuleService()
