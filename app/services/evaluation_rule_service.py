from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable

from app.schemas.document_context import DocumentContext
from app.schemas.task import AgentResult, Issue, ParsedDocument


RULESET_VERSION = "evaluation-review-2026.08.20-v4"


RULE_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "P1-02": ("至少两个项目名称来源（文档或业务系统）",),
    "P1-03": ("至少两个项目编号来源（文档或业务系统）",),
    "P1-04": ("中标候选人排序", "综合得分及排名"),
    "P1-05": ("中标候选人排序", "应推荐候选人数量"),
    "P1-06": ("中标候选人排序", "综合得分及排名"),
    "P1-07": ("中标候选人排序", "评审价及排名"),
    "P1-10": ("签字日期检测结果",),
    "P1-11": ("应签章位置及视觉检测结果",),
    "P1-12": ("谈判报告应签字位置及视觉检测结果",),
    "P2-01": ("采购公告发布日期", "采购文件发售开始日期", "采购文件发售截止日期", "采购文件规定的售卖期限"),
    "P2-02": ("评标报告中的开标时间和地点", "电子采购平台开标记录中的时间和地点"),
    "P2-03": ("购买采购文件供应商数量", "实际递交响应文件供应商数量", "平台报告供应商数量", "少于3家时的处理说明"),
    "P2-04": ("采购文件发售截止日期", "采购文件中的递交截止时间", "采购公告中的递交截止时间"),
    "P2-05": ("评标报告中的采购公告发布日期", "采购公告原文或平台截图中的发布日期"),
    "P2-08": ("开标时间", "评标时间"),
    "P2-09": ("开标记录中的供应商及投标报价", "报价一览表", "最高限价", "含税与不含税报价"),
    "P2-11": ("采购方式", "评标委员会成员名单或总人数", "重大项目标识（如适用）"),
    "P3-01": ("资格审查结果及通过供应商清单", "少于规定数量时的处理说明"),
    "P3-03": ("符合性审查结果及通过供应商清单", "少于规定数量时的处理说明"),
    "P3-05": ("同一评分因素至少两名专家的主观评分", "规则规定的偏离阈值"),
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
    "P1-02": ("项目名称",),
    "P1-04": ("中标候选人排序",), "P1-05": ("候选人数量",),
    "P1-06": ("得分排名",), "P1-07": ("评审价排名",),
    "P1-09": ("必需内容", "章节"), "P1-10": ("签字日期",),
    "P1-11": ("签章", "签名"), "P1-12": ("签字", "签名"),
    "P2-06": ("服务期限", "交货期", "工期"),
    "P2-07": ("开标地点",), "P2-08": ("开标时间",),
    "P2-09": ("报价", "金额"), "P2-10": ("回避", "关联关系"),
    "P2-11": ("评委人数", "委员会人数", "总人数", "人数不足", "人数为偶数"),
    "P3-02": ("资格审查不通过", "资格性审查不通过", "资格审查未通过", "资格审查废标"),
    "P3-04": ("符合性审查不通过", "符合性审查未通过", "符合性审查废标"),
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
    def _normalize_project_name(value: object) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
        normalized = re.sub(r"^项目名称\s*[:：]?\s*", "", normalized)
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"(?:第?\d+|[一二三四五六七八九十]+)(?:分?包|标段)$", "", normalized)
        return normalized.rstrip("。；;，,")

    @staticmethod
    def _project_id_kind(raw_text: str, source: str = "") -> str:
        context = f"{raw_text} {source}"
        if any(marker in context for marker in ("交易编号", "系统编号", "标段编号", "分包编号", "包编号", "(县区)", "（县区）", "ZBDL")):
            return "transaction_or_lot_id"
        if any(marker in context for marker in ("采购编号", "采购项目编号", "政府采购编号")):
            return "procurement_id"
        if any(marker in context for marker in ("招标编号", "招标项目编号")):
            return "tender_id"
        return "project_id"

    @staticmethod
    def _normalize_project_id(value: object) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
        normalized = re.sub(r"\s+", "", normalized)
        normalized = normalized.translate(str.maketrans({"－": "-", "—": "-", "–": "-"}))
        return normalized

    @staticmethod
    def _document_text(docs: list[ParsedDocument]) -> str:
        parts: list[str] = []
        for doc in docs:
            for section in doc.sections:
                parts.extend((section.title, section.content))
            for chunk in doc.evidence_chunks:
                parts.append(chunk.content)
            for table in doc.tables:
                parts.append("\n".join(" | ".join(map(str, row)) for row in table.rows))
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _first_match(text: str, patterns: tuple[str, ...]) -> tuple[str, str] | None:
        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                value = match.group(1) if match.lastindex else match.group(0)
                return value.strip(), match.group(0).strip()
        return None

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
            values: list[tuple[str, str, str]] = []
            for key in ("project_name", "项目名称"):
                value = str(system_record.get(key, "")).strip()
                if value:
                    values.append((value, self._normalize_project_name(value), f"业务系统字段 {key}"))
            for doc in docs:
                field = doc.extracted_fields.get("project_name")
                value = str(field.value if field else doc.project_name).strip()
                if value:
                    source = field.source_location if field else "文档解析结果"
                    normalized = self._normalize_project_name(value)
                    if normalized and len(normalized) <= 120 and not re.match(r"^\d+[.、]", normalized):
                        values.append((value, normalized, f"{doc.filename} / {source}"))
            unique = {normalized for _, normalized, _ in values}
            if len(values) < 2:
                return result
            result.update(
                missing_inputs=[],
                execution_evidence=[f"{source}：{value}" for value, _, source in values],
                calculation=f"比较 {len(values)} 个来源，得到 {len(unique)} 个不同项目名称。",
            )
            if len(unique) == 1:
                result.update(status="passed", reason="多个独立来源中的项目名称一致。")
            else:
                result.update(status="human_review", reason="多个来源中的项目名称不一致，需人工确认正确值。")
            return result

        if rule.rule_id == "P1-03":
            values: list[tuple[str, str, str, str]] = []
            for key in ("project_id", "project_number", "project_no", "项目编号", "采购项目编号", "招标项目编号"):
                value = str(system_record.get(key, "")).strip()
                if value:
                    values.append((value, self._normalize_project_id(value), "project_id", f"业务系统字段 {key}"))
            for doc in docs:
                field = doc.extracted_fields.get("project_id")
                if field and str(field.value).strip():
                    values.append((
                        str(field.value).strip(),
                        self._normalize_project_id(field.value),
                        self._project_id_kind(field.raw_text, doc.filename),
                        f"{doc.filename} / {field.source_location or '关键字段'}",
                    ))
            deduplicated: list[tuple[str, str, str, str]] = []
            seen_sources: set[tuple[str, str]] = set()
            for raw, normalized, kind, source in values:
                key = (normalized, source)
                if normalized and key not in seen_sources:
                    deduplicated.append((raw, normalized, kind, source))
                    seen_sources.add(key)
            if len(deduplicated) < 2:
                return result
            comparable: dict[str, set[str]] = {}
            for _, normalized, kind, _ in deduplicated:
                comparable.setdefault(kind, set()).add(normalized)
            conflicting = {kind: vals for kind, vals in comparable.items() if len(vals) > 1}
            result.update(
                missing_inputs=[],
                execution_evidence=[f"{source}（{kind}）：{raw}" for raw, _, kind, source in deduplicated],
                calculation=(
                    f"对 {len(deduplicated)} 个独立来源执行全半角、大小写、空白及连接符标准化，"
                    f"并按采购编号、招标编号、交易/标段编号分类后比较；同类型冲突 {len(conflicting)} 组。"
                ),
                status="passed" if not conflicting else "human_review",
                reason=(
                    "同类型项目编号在多个独立来源中一致；不同类型编号未互相误判为冲突。"
                    if not conflicting
                    else "同一类型的项目编号在多个来源中不一致，需核对采购文件和业务系统记录。"
                ),
            )
            return result

        text = self._document_text(docs)

        if rule.rule_id == "P1-01":
            found = self._first_match(text, (
                r"(?:包编号|包号|标包)\s*[:：]?\s*([^\n；。]+)",
                r"项目编号\s*[:：]?\s*([^\n；。]*?(?:号-\d+|ZBDL\d+号-\d+))",
                r"项目名称\s*[:：]?\s*([^\n；。]*?\d+分包)",
            ))
            if found:
                result.update(
                    missing_inputs=[], execution_evidence=[found[1]],
                    calculation="识别项目名称、项目编号或标包字段中的分包标识。",
                    status="passed", reason=f"已识别本项目包/标段标识：{found[0]}。",
                )
            return result

        if rule.rule_id == "P1-08":
            found = self._first_match(text, (
                r"(?:评标情况及说明|评标情况说明|评标情况|评审情况及说明|评审情况)[：:]?\s*([^\n]{0,120})",
            ))
            if found:
                result.update(
                    missing_inputs=[], execution_evidence=[found[1]],
                    calculation="检索评标报告中的评标/评审情况说明章节及正文。",
                    status="passed", reason="评标报告包含评标情况说明及后续审查、详细评审内容。",
                )
            return result

        if rule.rule_id == "P1-09":
            reports = [doc for doc in docs if doc.document_subtype == "评标报告"]
            if not reports:
                return result
            report_text = self._document_text(reports)
            required_groups = {
                "项目基本信息": ("项目名称", "项目编号", "招标人", "采购人"),
                "评标过程": ("评标过程", "评审过程", "评标情况", "评审情况"),
                "评标结果与推荐": ("中标候选人", "成交候选人", "推荐意见", "评标结果"),
                "评标委员会确认": ("评标委员会", "评审委员会", "评委签字", "专家签字"),
            }
            found = {
                name: next((marker for marker in markers if marker in report_text), "")
                for name, markers in required_groups.items()
            }
            missing = [name for name, marker in found.items() if not marker]
            result.update(
                missing_inputs=[],
                execution_evidence=[
                    f"{name}：{'命中“' + marker + '”' if marker else '未识别'}"
                    for name, marker in found.items()
                ],
                calculation=f"按评标报告模板核验 {len(required_groups)} 组必备内容，未识别 {len(missing)} 组。",
                status="human_review" if missing else "passed",
                reason=(f"有 {len(missing)} 组必备内容未识别，可能缺失或解析遗漏。" if missing else "评标报告必备内容均已识别。"),
            )
            return result

        if rule.rule_id == "P2-01":
            start = self._first_match(text, (r"(?:发售|获取|领取)(?:招标|采购)?文件(?:开始)?(?:时间)?\s*[:：]?\s*([^\n；。]+)",))
            end = self._first_match(text, (r"(?:发售|获取|领取)(?:招标|采购)?文件(?:截止|结束)(?:时间)?\s*[:：]?\s*([^\n；。]+)",))
            range_value = self._first_match(text, (r"(?:发售|获取)(?:招标|采购)?文件(?:时间|期限)?\s*[:：]?\s*([^\n；。]*(?:至|—|~)[^\n；。]+)",))
            evidence = [item[1] for item in (start, end, range_value) if item]
            if not evidence:
                return result
            result.update(
                missing_inputs=[], execution_evidence=evidence[:3],
                calculation="仅完成发售/获取日期取值；原规则还要求与公告发布日期、采购文件时限进行比较。",
                status="human_review",
                reason="已识别发售或获取日期，但尚未完成原规则规定的日期先后关系和售卖期限核验。",
            )
            return result

        if rule.rule_id in {"P2-04", "P2-05", "P2-07", "P2-08"}:
            patterns = {
                "P2-04": (r"(?:投标|响应文件(?:递交|提交))?截止时间\s*[:：]?\s*([^\n；。]+)", r"提交投标文件的截止时间\s*[:：]?\s*([^\n；。]+)"),
                "P2-05": (r"(?:招标|采购)公告(?:发布)?日期\s*[:：]?\s*([^\n；。]+)", r"公告发布时间\s*[:：]?\s*([^\n；。]+)"),
                "P2-07": (r"开标地点\s*[:：]?\s*([^\n；。]+)",),
                "P2-08": (r"开标时间\s*[:：]?\s*([^\n；。]+)",),
            }[rule.rule_id]
            found = self._first_match(text, patterns)
            if rule.rule_id == "P2-04" and (
                not found or not re.search(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}", found[0])
            ):
                found = self._first_match(text, (
                    r"投标截止及开标时间、地点\s*\n?\s*1[.、]\s*时间\s*[:：]\s*([^\n；。]+)",
                    r"投标截止及开标时间、地点[\s\S]{0,80}?时间\s*[:：]\s*([^\n；。]+)",
                ))
            if rule.rule_id == "P2-04" and not found:
                for doc in docs:
                    field = doc.extracted_fields.get("deadline")
                    if field and field.value:
                        found = (str(field.value), field.raw_text or str(field.value))
                        break
            if not found:
                return result
            value = found[0].strip()
            invalid_values = {"", "和地点", "及地点", "地点", "时间", "详见公告", "详见采购文件"}
            if value in invalid_values or not re.search(r"\S", value):
                return result
            if rule.rule_id in {"P2-04", "P2-05", "P2-08"} and not re.search(
                r"(?:20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日)",
                value,
            ):
                return result
            if rule.rule_id == "P2-07":
                result.update(
                    missing_inputs=[], execution_evidence=[found[1]],
                    calculation="按原规则核验开标地点字段非空，并排除标题和泛化占位语。",
                    status="passed", reason=f"已识别有效开标地点：{value}。",
                )
                return result
            if rule.rule_id == "P2-04" and "投标截止及开标时间" in found[1]:
                result.update(
                    missing_inputs=[], execution_evidence=[found[1]],
                    calculation="从采购文件‘投标截止及开标时间、地点’专节提取明确日期时间并校验字段非空。",
                    status="passed", reason=f"采购文件已明确投标文件递交截止时间：{value}。",
                )
                return result
            # Prefer a real cross-document comparison when the same field is
            # present in two or more independent project materials.
            per_document: list[tuple[str, str, str]] = []
            for doc in docs:
                doc_text = self._document_text([doc])
                doc_found = self._first_match(doc_text, patterns)
                if rule.rule_id == "P2-04" and not doc_found:
                    field = doc.extracted_fields.get("deadline")
                    if field and field.value:
                        doc_found = (str(field.value), field.raw_text or str(field.value))
                if not doc_found:
                    continue
                date_time = re.search(
                    r"(20\d{2})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})(?:\D{0,8}(\d{1,2})\s*[:时]\s*(\d{1,2}))?",
                    doc_found[0],
                )
                if date_time:
                    normalized = "-".join(f"{int(part):02d}" for part in date_time.groups(default="0"))
                    per_document.append((doc.filename, doc_found[1], normalized))
            if len(per_document) >= 2:
                unique_values = {item[2] for item in per_document}
                result.update(
                    missing_inputs=[],
                    execution_evidence=[f"{filename}：{evidence}" for filename, evidence, _ in per_document],
                    calculation=f"标准化并比较 {len(per_document)} 份独立文件中的{rule.item}，得到 {len(unique_values)} 个不同值。",
                    status="passed" if len(unique_values) == 1 else "human_review",
                    reason=f"多个来源中的{rule.item}一致。" if len(unique_values) == 1 else f"多个来源中的{rule.item}不一致。",
                )
                return result
            result.update(
                missing_inputs=[], execution_evidence=[found[1]],
                calculation=f"仅完成{rule.item}取值；尚未完成原规则要求的跨来源一致性或日期逻辑比较。",
                status="human_review",
                reason=f"已识别{rule.item}：{value}，但仅凭单一字段不能判定规则通过。",
            )
            return result

        openings = [item for doc in docs for item in doc.opening_records]
        if rule.rule_id in {"P2-02", "P2-03"}:
            valid = [item for item in openings if item.bidder]
            if not valid:
                return result
            by_source = {
                doc.document_subtype: {re.sub(r"\s+", "", item.bidder) for item in doc.opening_records if item.bidder}
                for doc in docs if doc.opening_records
            }
            report_set = by_source.get("评标报告", set())
            opening_set = by_source.get("开标记录表", set())
            if rule.rule_id == "P2-02":
                report_doc = next((doc for doc in docs if doc.document_subtype == "评标报告"), None)
                opening_doc = next((doc for doc in docs if doc.document_subtype == "开标记录表"), None)
                if report_doc and opening_doc:
                    report_text = self._document_text([report_doc])
                    opening_text = self._document_text([opening_doc])
                    report_time = self._first_match(report_text, (r"开标时间\s*[:：]?\s*([^\n；。]+)",))
                    opening_time = self._first_match(opening_text, (r"开标时间\s*[:：]?\s*([^\n；。]+)", r"开标日期\s*[:：]?\s*([^\n；。]+)"))
                    report_place = self._first_match(report_text, (r"开标地点\s*[:：]?\s*([^\n；。]+)",))
                    opening_place = self._first_match(opening_text, (r"开标地点\s*[:：]?\s*([^\n；。]+)",))
                    pairs = [("时间", report_time, opening_time), ("地点", report_place, opening_place)]
                    comparable = [(label, a, b) for label, a, b in pairs if a and b]
                    if comparable:
                        mismatches = [label for label, a, b in comparable if self._normalize_project_id(a[0]) != self._normalize_project_id(b[0])]
                        result.update(
                            missing_inputs=[] if len(comparable) == 2 else ["尚未形成双来源比对的开标时间或地点"],
                            execution_evidence=[f"评标报告{label}：{a[0]}；开标记录{label}：{b[0]}" for label, a, b in comparable],
                            calculation=f"已完成 {len(comparable)} 个开标大会字段的跨文件比对。",
                            status="human_review" if mismatches or len(comparable) < 2 else "passed",
                            reason=(f"开标大会信息有 {len(mismatches)} 个字段不一致。" if mismatches else "已比对的开标大会信息一致。"),
                        )
                        return result
            if rule.rule_id == "P2-03" and report_set and opening_set:
                same = report_set == opening_set
                result.update(
                    missing_inputs=["购买采购文件供应商数量", "平台报告供应商数量"],
                    execution_evidence=[f"评标报告递交供应商：{sorted(report_set)}", f"开标记录供应商：{sorted(opening_set)}"],
                    calculation=f"评标报告={len(report_set)}家，开标记录={len(opening_set)}家；供应商集合一致={same}。",
                    status="human_review",
                    reason=("评标报告与开标记录中的递交供应商一致，但缺少购买数量和平台基准，完成部分核验。" if same else "评标报告与开标记录中的递交供应商不一致。"),
                )
                return result
            result.update(
                missing_inputs=list(RULE_REQUIRED_INPUTS[rule.rule_id]),
                execution_evidence=[f"{item.bidder}：报价 {item.bid_price if item.bid_price is not None else '未识别'}" for item in valid[:12]],
                calculation=f"按供应商名称去重，共识别 {len(set(item.bidder for item in valid))} 家递交/开标记录。",
                status="insufficient_data",
                reason="已识别供应商开标记录，但缺少原规则要求的购买数量、平台报告或开标时间地点基准，不能判定通过。",
            )
            return result

        if rule.rule_id == "P2-11":
            experts = sorted({item.expert.strip() for doc in docs for item in doc.score_details if item.expert.strip()})
            if not experts:
                for doc in docs:
                    for table in doc.tables:
                        for row in table.rows[:3]:
                            joined = "|".join(str(cell) for cell in row)
                            if "评委" not in joined:
                                continue
                            candidates = [re.sub(r"\s+", "", str(cell)) for cell in row[1:]]
                            experts.extend(name for name in candidates if re.fullmatch(r"[\u4e00-\u9fa5·]{2,8}", name))
                experts = sorted(set(experts))
            explicit = self._first_match(text, (r"评标委员会(?:由|共|成员共)[^\n\d]{0,10}(\d+)\s*人", r"评审委员会(?:由|共|成员共)[^\n\d]{0,10}(\d+)\s*人"))
            count = len(experts) if experts else (int(explicit[0]) if explicit else 0)
            if not count:
                return result
            evidence = [f"评分明细中识别评委：{', '.join(experts)}"] if experts else [explicit[1]]
            method = next(
                (method for method in ("公开招标", "邀请招标", "竞争性谈判", "竞争性磋商", "询比", "询价", "直接采购", "单一来源") if method in text),
                str(system_record.get("procurement_method") or system_record.get("采购方式") or "未识别"),
            )
            minimum = 5 if method in {"公开招标", "邀请招标"} else 3
            suspicious = count < minimum or count % 2 == 0
            result.update(
                missing_inputs=[], execution_evidence=evidence,
                calculation=f"采购方式={method}；评标委员会成员去重计数={count}；最低人数={minimum}；人数必须为单数。",
                status="human_review" if suspicious else "passed",
                reason=(f"识别到 {count} 名评委，不满足当前采购方式的最低人数或单数要求。" if suspicious else f"识别到 {count} 名评委，满足当前采购方式最低人数和单数要求。"),
            )
            return result

        rankings = [
            item for doc in docs for item in doc.candidate_rankings
            if item.bidder and item.rank
            and re.search(r"(?:公司|集团|企业|中心|研究院|事务所)$", re.sub(r"\s+", "", item.bidder))
        ]
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
            if not rankings:
                return result
            if expected_count in (None, ""):
                counts = {
                    doc.filename: len({
                        (item.lot, re.sub(r"\s+", "", item.bidder))
                        for item in doc.candidate_rankings
                        if item.bidder and re.search(r"(?:公司|集团|企业|中心|研究院|事务所)$", re.sub(r"\s+", "", item.bidder))
                    })
                    for doc in docs if doc.candidate_rankings
                }
                counts = {name: count for name, count in counts.items() if count > 0}
                if len(counts) < 2:
                    return result
                unique_counts = set(counts.values())
                result.update(
                    missing_inputs=[], execution_evidence=[f"{name}：候选人{count}名" for name, count in counts.items()],
                    calculation=f"比较 {len(counts)} 份结果文件的候选人数，得到 {len(unique_counts)} 个不同计数。",
                    status="passed" if len(unique_counts) == 1 else "human_review",
                    reason="多份结果文件中的候选人数一致。" if len(unique_counts) == 1 else "多份结果文件中的候选人数不一致。",
                )
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
                reports = [doc for doc in docs if doc.document_subtype == "评标报告"]
                if reports:
                    result.update(
                        missing_inputs=["可靠的签字/印章视觉检测结果"],
                        execution_evidence=[f"已提供评标报告：{doc.filename}" for doc in reports],
                        calculation="已定位应核验报告，但当前解析结果未形成可靠的签字、日期或印章检测记录。",
                        status="human_review",
                        reason="报告已提供，签章类规则完成材料定位；视觉检测结果不足，转人工复核，而非判定资料缺失。",
                    )
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

        if rule.rule_id == "P2-06":
            values: list[tuple[str, str]] = []
            patterns = (
                r"((?:特许经营|服务|合同|交货|工期)(?:期限|期)?(?:为|：|:)\s*[^\n；。]{1,60})",
                r"(本项目特许经营期限为\s*\d+\s*年)",
            )
            for doc in docs:
                doc_text = self._document_text([doc])
                for pattern in patterns:
                    for match in re.finditer(pattern, doc_text):
                        raw = re.sub(r"\s+", "", match.group(1))
                        if re.search(r"\d+\s*(?:年|个月|月|日|天)", raw):
                            values.append((raw, doc.filename))
            if values:
                normalized = {
                    re.search(r"\d+(?:\.\d+)?(?:年|个月|月|日|天)", value).group(0)
                    for value, _ in values
                    if re.search(r"\d+(?:\.\d+)?(?:年|个月|月|日|天)", value)
                }
                result.update(
                    missing_inputs=[], execution_evidence=[f"{source}：{value}" for value, source in values[:12]],
                    calculation=f"跨文件提取工期/交货期/服务期并标准化，共得到 {len(normalized)} 个不同期限值。",
                    status="passed" if len(normalized) == 1 else "human_review",
                    reason="多个来源中的期限记录一致。" if len(normalized) == 1 else "不同来源中的期限记录不一致，需人工核对。",
                )
            return result

        if rule.rule_id in {"P3-01", "P3-03"}:
            label = "资格审查" if rule.rule_id == "P3-01" else "符合性审查"
            explicit = self._first_match(text, (
                rf"(?:通过|符合){label}[^\n\d]{{0,12}}(\d+)\s*家",
                rf"{label}(?:通过|合格)[^\n\d]{{0,12}}(\d+)\s*家",
            ))
            all_pass = self._first_match(text, (rf"{label}[^\n。；]{{0,30}}(?:全部|均)(?:通过|合格)",))
            if explicit:
                count = int(explicit[0])
                evidence = explicit[1]
            elif all_pass and openings:
                count = len({item.bidder for item in openings if item.bidder})
                evidence = all_pass[1]
            else:
                keyword = "资格性审查" if rule.rule_id == "P3-01" else "符合性审查"
                passed_bidders: set[str] = set()
                table_evidence = ""
                for doc in docs:
                    for table in doc.tables:
                        rows = table.rows
                        if not rows or keyword not in re.sub(r"\s+", "", " ".join(" ".join(map(str, row)) for row in rows)):
                            continue
                        header_names: list[str] = []
                        for row in rows[:2]:
                            for cell in row[2:]:
                                name = re.sub(r"\s+", "", str(cell))
                                if re.search(r"(?:公司|集团|企业|中心|研究院|事务所)$", name):
                                    header_names.append(name)
                        if header_names and any("通过" in str(cell) for row in rows for cell in row):
                            passed_bidders.update(header_names)
                            table_evidence = f"{doc.filename}表格：{keyword}各供应商均显示‘通过’"
                if not passed_bidders:
                    return result
                count = len(passed_bidders)
                evidence = table_evidence
            result.update(
                missing_inputs=[], execution_evidence=[evidence], calculation=f"从原文及供应商记录确定{label}通过供应商数量={count}。",
                status="passed", reason=f"已识别{label}通过供应商 {count} 家。",
            )
            return result

        if rule.rule_id in {"P3-02", "P3-04"}:
            label = "资格" if rule.rule_id == "P3-02" else "符合性"
            records = [
                item for doc in docs for item in doc.rejection_records
                if label in f"{item.reason} {item.cited_clause} {item.evidence}"
            ]
            all_pass = self._first_match(text, (rf"{label}(?:性)?审查[^\n。；]{{0,30}}(?:全部|均)(?:通过|合格)",))
            if records:
                result.update(
                    missing_inputs=[],
                    execution_evidence=[item.evidence or f"{item.bidder}：{item.reason}" for item in records[:12]],
                    calculation=f"识别 {len(records)} 条{label}审查不通过记录，需回查采购文件依据。",
                    status="human_review", reason=f"存在 {len(records)} 条{label}审查不通过记录，需核验事实与预先载明依据。",
                )
                return result
            if all_pass:
                result.update(
                    missing_inputs=[], execution_evidence=[all_pass[1]], calculation=f"原文明确记载{label}审查全部通过。",
                    status="passed", reason=f"未识别到{label}审查不通过记录，且原文明确全部通过。",
                )
                return result
            keyword = "资格性审查" if rule.rule_id == "P3-02" else "符合性审查"
            table_pass = any(
                keyword in re.sub(r"\s+", "", " ".join(" ".join(map(str, row)) for row in table.rows))
                and any("通过" in str(cell) for row in table.rows for cell in row)
                and not any(any(mark in str(cell) for mark in ("不通过", "未通过", "废标")) for row in table.rows for cell in row)
                for doc in docs for table in doc.tables
            )
            if table_pass:
                result.update(
                    missing_inputs=[], execution_evidence=[f"{keyword}表汇总均为‘通过’，未发现不通过行。"],
                    calculation=f"扫描{keyword}横向审查表的通过及不通过状态。",
                    status="passed", reason=f"{keyword}记录显示全部通过，未发现不通过记录。",
                )
                return result
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

        if rule.rule_id in {"P3-07", "P3-11"}:
            if rule.rule_id == "P3-07":
                ordered = sorted(summaries, key=lambda item: -float(item.total_score))
                if len(ordered) < 2:
                    return result
                gaps = [round(float(ordered[i].total_score) - float(ordered[i + 1].total_score), 2) for i in range(len(ordered) - 1)]
                result.update(
                    missing_inputs=["经确认的倾向性预警阈值"],
                    execution_evidence=[f"{item.bidder}：综合得分 {item.total_score:g}" for item in ordered],
                    calculation=f"按综合得分降序计算相邻名次差值：{gaps}。",
                    status="human_review",
                    reason="已完成候选人得分差距计算；规则未提供可自动定性的阈值，结果作为异常线索供人工复核。",
                )
                return result
            technical = [item for item in score_details if "技术" in item.factor and item.raw_score is not None]
            groups: dict[tuple[str, str], list[float]] = {}
            for item in technical:
                groups.setdefault((item.bidder, item.factor), []).append(float(item.raw_score))
            groups = {key: values for key, values in groups.items() if len(values) >= 2}
            if not groups:
                return result
            warnings = []
            evidence = []
            for (bidder, factor), values in groups.items():
                avg = sum(values) / len(values)
                max_dev = max(abs(value - avg) for value in values)
                evidence.append(f"{bidder}/{factor}：平均 {avg:.2f}，最大偏离 {max_dev:.2f}，专家分 {values}")
                if max_dev > max(3.0, abs(avg) * .30):
                    warnings.append((bidder, factor))
            result.update(
                missing_inputs=[], execution_evidence=evidence[:12],
                calculation="对技术评分按供应商和评分项计算平均值及最大偏离，预警阈值=max(3分,平均值×30%)。",
                status="human_review" if warnings else "passed",
                reason=f"发现 {len(warnings)} 组技术评分畸高畸低线索。" if warnings else "技术评分未超过配置的偏离预警阈值。",
            )
            return result

        if rule.rule_id in {"P3-08", "P3-10"}:
            keyword = "价格" if rule.rule_id == "P3-08" else "商务"
            rows = [item for item in score_details if keyword in item.factor]
            calculable = [item for item in rows if item.raw_score is not None and item.weight is not None and item.weighted_score is not None]
            if not calculable:
                # Official reports often provide each expert's factor score and a
                # section subtotal rather than a separate weight column. Recompute
                # the section average and compare it with the summary table.
                section_attr = "price_score" if rule.rule_id == "P3-08" else "business_score"
                by_bidder: dict[str, dict[str, list[float]]] = {}
                for item in rows:
                    if item.bidder and item.raw_score is not None:
                        by_bidder.setdefault(item.bidder, {}).setdefault(item.factor, []).append(float(item.raw_score))
                summary_map = {item.bidder: getattr(item, section_attr, None) for item in summaries}
                comparable = []
                for bidder, factors in by_bidder.items():
                    if summary_map.get(bidder) is None:
                        continue
                    computed = sum(sum(values) / len(values) for values in factors.values())
                    comparable.append((bidder, computed, float(summary_map[bidder])))
                if not comparable:
                    return result
                mismatches = [row for row in comparable if abs(row[1] - row[2]) > .02]
                result.update(
                    missing_inputs=[],
                    execution_evidence=[f"{bidder}：专家明细平均后合计 {computed:.2f}，汇总记录 {recorded:.2f}" for bidder, computed, recorded in comparable],
                    calculation="对每个评分因素计算专家平均分，再按价格/商务部分求和，与汇总表按0.02分容差比较。",
                    status="human_review" if mismatches else "passed",
                    reason=f"发现 {len(mismatches)} 家{keyword}得分复算差异。" if mismatches else f"已复算 {len(comparable)} 家{keyword}得分，结果一致。",
                )
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
            factor_scores: dict[tuple[str, str, str], list[float]] = {}
            for item in score_details:
                value = item.weighted_score if item.weighted_score is not None else item.raw_score
                if item.bidder and item.factor and value is not None:
                    factor_scores.setdefault((item.lot, item.bidder, item.factor), []).append(float(value))
            totals: dict[tuple[str, str], float] = {}
            for (lot, bidder, _), values in factor_scores.items():
                totals[(lot, bidder)] = totals.get((lot, bidder), 0.0) + sum(values) / len(values)
            comparable = [item for item in summaries if (item.lot, item.bidder) in totals]
            if not comparable:
                return result
            mismatches = [item for item in comparable if abs(totals[(item.lot, item.bidder)] - float(item.total_score)) > 0.01]
            evidence = [f"{item.bidder}：明细合计 {totals[(item.lot, item.bidder)]:.2f}，汇总 {item.total_score:.2f}" for item in comparable]
            result.update(
                missing_inputs=[], execution_evidence=evidence[:12], calculation="按供应商及评分因素计算专家平均分，再汇总各因素得分，与综合得分按0.02分容差比较。",
                status="human_review" if mismatches else "passed",
                reason=f"发现 {len(mismatches)} 家综合得分复算不一致。" if mismatches else f"已复算 {len(comparable)} 家综合得分，结果一致。",
            )
            return result

        if rule.rule_id in {"P1-07", "P3-14", "P3-18"}:
            valid = [item for item in openings if item.bidder and item.bid_price is not None]
            if not valid:
                return result
            ranked = sorted(valid, key=lambda item: float(item.bid_price))
            result.update(
                missing_inputs=["评审价修正记录或电子评审系统评审价导出"],
                execution_evidence=[f"{item.bidder}：投标报价 {item.bid_price:g}" for item in ranked],
                calculation="已提取并排序有效投标报价；未提供评审价修正记录时，不能将投标报价直接等同于最终评审价。",
                status="human_review",
                reason="已完成投标报价完整性和排序核验；评审价修正依据缺失，规则部分完成并转人工复核。",
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
            return any(marker in text for marker in ("包编号", "包号", "标包", "分包")) or bool(
                re.search(r"(?:ZBDL\d+号-\d+|项目编号[^\n]*号-\d+)", text)
            )
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
            if issue.agent == rule.owner_agent
            and any(marker in f"{issue.issue_type} {issue.description}" for marker in markers)
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
