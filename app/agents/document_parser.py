from __future__ import annotations

import hashlib
import json
import re

from app.agents.utils import find_lines, guess_project_name, money_values, unique_keep_order
from app.api.file_helpers import infer_document_subtype
from app.core.config import settings
from app.schemas.task import (
    AgentResult,
    DocumentQualityCheck,
    DocumentSection,
    DocumentTable,
    EvidenceRef,
    EvidenceChunk,
    ExtractedField,
    Issue,
    LayoutElement,
    CandidateRanking,
    EvaluationOpinion,
    OpeningRecord,
    ParsedDocument,
    ParseAttempt,
    ParsePlan,
    RejectionRecord,
    ScoreDetail,
    ScoreSummary,
    SealSignatureCheck,
    SourceLocation,
    UploadedFileInfo,
)
from app.services.file_parser import ParsedFileContent, parse_file
from app.services.material_inventory import classify_document_role
from app.services.document_visual_service import analyze_document_visuals
from app.services.document_semantic_enhancer import (
    DifyWorkflowError,
    document_semantic_enhancer,
)


HEADING_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千0-9]+[章节部分篇]\s*.+$"),
    re.compile(r"^[一二三四五六七八九十]+、\s*.+$"),
    re.compile(r"^\d+(?:\.\d+){0,3}[、.．]\s*.+$"),
    re.compile(r"^[（(][一二三四五六七八九十0-9]+[）)]\s*.+$"),
]

NUMBERED_HEADING_PATTERN = re.compile(r"^\d+(?:\.\d+){0,3}[、.．]\s*.+$")


def _build_parse_plan(file_info: UploadedFileInfo) -> ParsePlan:
    suffix = file_info.filename.rsplit(".", 1)[-1].lower() if "." in file_info.filename else "unknown"
    tools = {
        "pdf": ["PDF文本与版面解析", "表格还原", "OCR按需回退", "印章与签名视觉检测"],
        "docx": ["DOCX结构解析", "表格还原", "段落与标题识别"],
        "txt": ["文本编码识别", "章节与字段抽取"],
        "xlsx": ["Excel工作表解析", "表格还原", "评分与报价字段抽取"],
        "xls": ["Excel工作表解析", "表格还原", "评分与报价字段抽取"],
    }.get(suffix, ["通用文本解析", "文件类型识别"])
    tools.extend(["确定性质量门", "Dify语义增强按需回退", "证据切片与项目临时索引"])
    return ParsePlan(
        strategy="确定性解析优先，质量不达标时自动切换 OCR/语义增强，最终保留人工复核入口",
        source_format=suffix,
        planned_tools=tools,
        required_outputs=["正文", "章节", "表格", "关键字段", "来源定位", "质量报告", "证据切片"],
        quality_threshold=0.75,
        max_retries=2,
        reasons=[f"输入识别为 {file_info.file_type}", "后续智能体需要统一证据定位与可追溯数据"],
    )


def _quality_score(checks: list[DocumentQualityCheck]) -> float:
    if not checks:
        return 0.0
    weights = {"passed": 1.0, "warning": 0.55, "failed": 0.0}
    return round(sum(weights[x.status] for x in checks) / len(checks), 4)


def _chunks(document_id: str, sections: list[DocumentSection], tables: list[DocumentTable]) -> list[EvidenceChunk]:
    result: list[EvidenceChunk] = []
    for section_index, section in enumerate(sections):
        content = section.content.strip()
        if not content:
            continue
        start = 0
        part = 0
        while start < len(content):
            text = content[start:start + 1200].strip()
            if not text:
                break
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            result.append(EvidenceChunk(
                chunk_id=f"{document_id}-s{section_index + 1}-c{part + 1}-{digest[:8]}",
                document_id=document_id, content=text, page=section.page,
                section=section.title, source_hash=digest,
            ))
            if start + 1200 >= len(content):
                break
            start += 1080
            part += 1
    for table_index, table in enumerate(tables):
        text = json.dumps(table.rows, ensure_ascii=False)
        if not text or text == "[]":
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        result.append(EvidenceChunk(
            chunk_id=f"{document_id}-t{table_index + 1}-{digest[:8]}",
            document_id=document_id, content=text, content_type="table", page=table.page,
            section=table.sheet or "表格", source_hash=digest,
        ))
    return result

FIELD_PATTERNS: dict[str, tuple[str, list[re.Pattern[str]]]] = {
    "project_name": (
        "项目名称",
        [
            re.compile(r"(?:采购|招标)?项目名称[ \t]*[:：][ \t]*([^\n]+)"),
            re.compile(r"工程名称[ \t]*[:：][ \t]*([^\n]+)"),
        ],
    ),
    "budget": (
        "项目预算",
        [re.compile(r"(?:项目)?预算(?:金额)?[ \t]*[:：][ \t]*([^\n，。；、]+)")],
    ),
    "price_limit": (
        "最高投标限价",
        [
            re.compile(r"最高(?:投标)?限价(?:（如有）|\(如有\))?[ \t]*[:：][ \t]*([^\n，。；]+)"),
            re.compile(r"最高控制价[ \t]*[:：][ \t]*([^\n，。；]+)"),
        ],
    ),
    "tenderer": (
        "招标人/采购人",
        [
            re.compile(
                r"(?:招标人|采[ \t]*购[ \t]*人|招标单位|采购单位)[ \t]*[:：][ \t]*([^\n]+)"
            )
        ],
    ),
    "procurement_agency": (
        "招标代理机构/采购代理机构",
        [
            re.compile(
                r"(?:招标代理机构|采购代理机构)[ \t]*[:：][ \t]*([^\n]+)"
            )
        ],
    ),
    "deadline": (
        "截止时间",
        [
            re.compile(r"(?:投标|响应文件提交)?截止时间[ \t]*[:：][ \t]*([^\n]+)"),
            re.compile(r"提交投标文件的截止时间[ \t]*[:：][ \t]*([^\n]+)"),
            re.compile(
                r"截止至本项目投标文件截止时间[ \t]*[:：]?[ \t]*"
                r"(\d{4}[ \t]*年[ \t]*\d{1,2}[ \t]*月[ \t]*\d{1,2}[ \t]*日"
                r"[ \t]*\d{1,2}[ \t]*时[ \t]*\d{1,2}[ \t]*分)"
            ),
        ],
    ),
}

FIELD_LABELS = (
    "项目名称", "采购项目名称", "招标项目名称", "工程名称", "项目编号",
    "采购人", "招标人", "采购单位", "招标单位", "采购代理机构", "招标代理机构",
    "项目预算", "预算金额", "最高投标限价", "最高控制价", "截止时间",
)


def _clean_field_value(field_name: str, value: object) -> str:
    """Remove repeated labels and stop a value before the next inline field."""
    cleaned = re.sub(r"[ \t]+", " ", str(value or "")).strip()
    own_labels = {
        "project_name": ("项目名称", "采购项目名称", "招标项目名称", "工程名称"),
        "tenderer": ("采购人", "招标人", "采购单位", "招标单位"),
        "procurement_agency": ("采购代理机构", "招标代理机构"),
        "budget": ("项目预算", "预算金额", "预算"),
        "price_limit": ("最高投标限价", "最高控制价"),
        "deadline": ("截止时间", "投标截止时间", "响应文件提交截止时间"),
    }.get(field_name, ())
    for label in sorted(own_labels, key=len, reverse=True):
        cleaned = re.sub(rf"^(?:{re.escape(label)})\s*[:：]?\s*", "", cleaned)
    other_labels = [label for label in FIELD_LABELS if label not in own_labels]
    if other_labels:
        cleaned = re.split(
            rf"[。；;]?\s*(?=(?:{'|'.join(map(re.escape, other_labels))})\s*[:：])",
            cleaned,
            maxsplit=1,
        )[0]
    return cleaned.strip().rstrip("。；;，,")[:200]


def _heading_level(title: str) -> int:
    if title.startswith("第") and any(mark in title[:8] for mark in "篇部分章"):
        return 1
    if re.match(r"^[一二三四五六七八九十]+、", title):
        return 1
    number = re.match(r"^(\d+(?:\.\d+)*)", title)
    if number:
        return min(number.group(1).count(".") + 1, 4)
    return 2


def _is_heading(line: str) -> bool:
    if len(line) > 80:
        return False
    # “1. 投标人应当……”一类完整条款不是章节标题。数字编号标题通常较短，
    # 且不会以句号或分号结束，例如“1. 项目概况”“1.1 技术要求”。
    if NUMBERED_HEADING_PATTERN.match(line) and line.endswith(("。", "；", ";")):
        return False
    return any(pattern.match(line) for pattern in HEADING_PATTERNS)


def _page_for_offset(content: ParsedFileContent, offset: int) -> int | None:
    consumed = 0
    for page in content.pages:
        end = consumed + len(page.text)
        if consumed <= offset <= end:
            return page.number
        consumed = end + 2
    return content.pages[0].number if content.pages else None


def _extract_sections(content: ParsedFileContent) -> list[DocumentSection]:
    lines = content.text.splitlines()
    sections: list[DocumentSection] = []
    current_title = "文档正文"
    current_level = 1
    current_start = 1
    current_lines: list[str] = []
    offset = 0
    current_offset = 0

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body or current_title != "文档正文":
            sections.append(
                DocumentSection(
                    title=current_title,
                    level=current_level,
                    content=body,
                    page=_page_for_offset(content, current_offset),
                    line_start=current_start,
                )
            )

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if _is_heading(line):
            flush()
            current_title = line
            current_level = _heading_level(line)
            current_start = line_number
            current_lines = []
            current_offset = offset
        elif line:
            current_lines.append(line)
        offset += len(raw_line) + 1
    flush()
    return sections[:200]


def _line_location(text: str, start: int) -> str:
    line = text.count("\n", 0, start) + 1
    return f"第 {line} 行"


def _is_placeholder_value(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return True
    if re.fullmatch(r"[_＿—\-/.（）()\[\]【】]+(?:万元|元)?", compact):
        return True
    if re.search(r"[_＿]{2,}", compact):
        return True
    return compact.lower() in {
        "待填写",
        "未填写",
        "无",
        "暂无",
        "none",
        "null",
        "未知",
        "未提供",
        "未找到",
    }


def _is_definition_value(field_name: str, value: str) -> bool:
    if field_name != "tenderer":
        return False
    compact = re.sub(r"\s+", "", value)
    return compact.startswith("指") or (
        "本项目采购人" in compact and "见" in compact
    )


def _extend_wrapped_project_name(text: str, match: re.Match[str], value: str) -> tuple[str, str]:
    """Join a short PDF line continuation after a long project-name line."""
    remainder = text[match.end() :]
    next_line = remainder.lstrip("\n").split("\n", 1)[0].strip()
    if (
        len(value) >= 15
        and 0 < len(next_line) <= 30
        and not value.endswith(("。", "；", ";", "：", ":"))
        and not re.search(r"[:：]", next_line)
        and not _is_heading(next_line)
        and not re.fullmatch(r"(?:序号|编号)?\s*(?:内容|名称|项目|条款|备注)(?:\s+(?:内容|名称|项目|条款|备注))*", next_line)
    ):
        return value + next_line, match.group(0).strip() + "\n" + next_line
    return value, match.group(0).strip()


def _infer_subtype_from_content(document_subtype: str, text: str) -> str:
    if document_subtype not in {"业务文件", "其他资料"}:
        return document_subtype
    head = re.sub(r"[ \t]+", "", text[:2500])
    if "响应文件" in head and any(marker in head for marker in ("合作方：", "合作方:", "投标人：", "供应商：")):
        return "响应文件"
    return document_subtype


def _response_entities(text: str) -> list[str]:
    entities: list[str] = []
    patterns = (
        r"(?:合作方|投标人|供应商|受邀方|承诺单位)\s*[:：]\s*([^\n，。；:：]{2,80})",
        r"(?:合作方|投标人|供应商|受邀方|承诺单位)\s+：?\s*([^\n，。；:：]{2,80})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = re.sub(r"\s+", "", match.group(1)).strip("（）()盖章签字")
            organization_markers = (
                "公司", "集团", "大学", "学院", "研究院", "研究所", "中心",
                "事务所", "合作社", "厂", "局", "院", "所",
            )
            if 2 <= len(value) <= 50 and any(marker in value for marker in organization_markers) and not any(
                marker in value for marker in ("应当", "不得", "其他供应商", "采购人", "代理机构", "响应文件")
            ):
                entities.append(value)
    return unique_keep_order(entities)[:20]


def _response_tenderer_field(content: ParsedFileContent) -> ExtractedField | None:
    match = re.search(r"(?:^|\n)\s*致\s*([^\n:：]{4,100})\s*[:：]", content.text)
    if not match:
        return None
    value = re.sub(r"\s+", "", match.group(1)).strip()
    if not value:
        return None
    page = _page_for_offset(content, match.start())
    return ExtractedField(
        value=value,
        raw_text=match.group(0).strip(),
        source_location=f"第 {page} 页" if page else _line_location(content.text, match.start()),
        confidence=0.82,
        requires_human_review=True,
    )


def _cover_project_name_field(content: ParsedFileContent) -> ExtractedField | None:
    """Conservative cover-title fallback; an institution name alone is not a project name."""
    first_page = content.pages[0] if content.pages else None
    cover_text = first_page.text if first_page and first_page.text else content.text[:3000]
    candidates: list[str] = []
    for raw_line in cover_text.splitlines()[:50]:
        line = re.sub(r"\s+", "", raw_line).strip("：:。、；; ")
        if not (4 <= len(line) <= 100):
            continue
        if re.search(r"(?:采购人|招标人|代理机构|项目编号|日期|目录|投标人)[:：]", line):
            continue
        if not re.search(r"项目|工程", line):
            continue
        if not re.search(r"采购|招标|建设|服务|维护|运维|改造|升级|评标|工程", line):
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{0,10}(?:政府)?(?:采购|招标)项目", line):
            continue
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9（）()·]+(?:大学|学院|公司|中心|局|委员会)", line):
            continue
        candidates.append(line)
    if not candidates:
        return None
    value = max(candidates, key=lambda item: ("项目" in item, len(item)))
    page = first_page.number if first_page else None
    return ExtractedField(
        value=value,
        raw_text=value,
        source_location=f"第 {page} 页封面标题" if page else "封面标题",
        confidence=0.78,
        requires_human_review=False,
    )


def _extract_fields(content: ParsedFileContent, fallback_name: str) -> dict[str, ExtractedField]:
    fields: dict[str, ExtractedField] = {}
    for field_name, (_, patterns) in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(content.text)
            if not match:
                continue
            value = _clean_field_value(field_name, match.group(1))
            if _is_placeholder_value(value) or _is_definition_value(field_name, value):
                continue
            if field_name == "project_name":
                value, raw_text = _extend_wrapped_project_name(content.text, match, value)
                value = _clean_field_value(field_name, value)
            else:
                raw_text = match.group(0).strip()
            page = _page_for_offset(content, match.start())
            location = f"第 {page} 页" if page else _line_location(content.text, match.start())
            fields[field_name] = ExtractedField(
                value=value,
                raw_text=raw_text,
                source_location=location,
                confidence=0.95,
            )
            break
    if "project_name" not in fields:
        cover_field = _cover_project_name_field(content)
        if cover_field:
            fields["project_name"] = cover_field
    if "project_name" not in fields and fallback_name:
        fields["project_name"] = ExtractedField(
            value=fallback_name,
            raw_text="",
            source_location="任务参数",
            confidence=0.6,
            requires_human_review=True,
        )
    return fields


def _quality_checks(
    content: ParsedFileContent,
    fields: dict[str, ExtractedField],
    file_type: str,
) -> list[DocumentQualityCheck]:
    checks: list[DocumentQualityCheck] = []
    if content.text:
        checks.append(DocumentQualityCheck(code="text_extracted", status="passed", message="已提取有效文本"))
    else:
        checks.append(
            DocumentQualityCheck(
                code="text_extracted",
                status="failed",
                message="未提取到有效文本",
                requires_human_review=True,
            )
        )
    ocr_needs_review = content.is_scanned and (
        not content.ocr_applied or content.ocr_confidence < 0.75
    )
    if content.is_scanned and content.ocr_applied:
        scan_message = f"扫描件已完成 OCR，平均置信度 {content.ocr_confidence:.2f}"
    elif content.is_scanned:
        scan_message = "疑似扫描件，需要 OCR 或人工复核"
    else:
        scan_message = "未发现明显扫描件特征"
    checks.append(
        DocumentQualityCheck(
            code="scan_detection",
            status="warning" if ocr_needs_review else "passed",
            message=scan_message,
            requires_human_review=ocr_needs_review,
        )
    )
    required_fields: tuple[str, ...]
    if file_type == "招标文件":
        required_fields = ("project_name", "budget", "price_limit")
    elif file_type in {"投标文件", "评标报告", "合同文件", "业务文件"}:
        required_fields = ("project_name",)
    else:
        required_fields = ()
    missing = [
        name
        for name in required_fields
        if name not in fields
    ]
    low_confidence = [
        name
        for name in required_fields
        if name in fields and fields[name].confidence < 0.75
    ]
    conflicting = [
        name
        for name in required_fields
        if name in fields and fields[name].requires_human_review
    ]
    field_findings: list[str] = []
    if missing:
        field_findings.append(f"未识别字段: {', '.join(missing)}")
    if low_confidence:
        field_findings.append(f"低置信度字段: {', '.join(low_confidence)}")
    if conflicting:
        field_findings.append(f"存在冲突或需确认的字段: {', '.join(conflicting)}")
    checks.append(
        DocumentQualityCheck(
            code="key_fields",
            status="warning" if field_findings else "passed",
            message=(
                "；".join(field_findings)
                if field_findings
                else "关键字段检查通过或当前文档类型不要求检查"
            ),
            requires_human_review=bool(field_findings),
        )
    )
    replacement_count = content.text.count("�")
    garbled = replacement_count > max(3, len(content.text) // 200)
    checks.append(
        DocumentQualityCheck(
            code="encoding_quality",
            status="warning" if garbled else "passed",
            message="文本中存在较多乱码替换符" if garbled else "未发现明显乱码",
            requires_human_review=garbled,
        )
    )
    return checks


def _needs_semantic_enhancement(
    sections: list[DocumentSection],
    fields: dict[str, ExtractedField],
    file_type: str,
) -> bool:
    has_recognized_heading = any(section.title != "文档正文" for section in sections)
    if not has_recognized_heading:
        return True
    required = ("project_name", "budget", "price_limit") if file_type == "招标文件" else ("project_name",)
    return any(
        name not in fields
        or fields[name].confidence < 0.75
        or fields[name].requires_human_review
        for name in required
    )


def _is_unfilled_template(text: str) -> bool:
    template_markers = ("示范文本", "参考文本", "填写规则", "使用说明")
    placeholder_patterns = re.findall(
        r"(?:_{2,}|＿{2,}|□|\[待填写\]|【待填写】)",
        text,
    )
    marker_count = sum(marker in text for marker in template_markers)
    return marker_count >= 2 and len(placeholder_patterns) >= 3


def _number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group()) if match else None


def _integer(value: str) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _header_index(headers: list[str], *keywords: str) -> int | None:
    for index, header in enumerate(headers):
        compact = re.sub(r"\s+", "", header)
        if any(keyword in compact for keyword in keywords):
            return index
    return None


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def _extract_tabular_records(content: ParsedFileContent) -> tuple[
    list[OpeningRecord], list[ScoreDetail], list[ScoreSummary], list[CandidateRanking]
]:
    openings: list[OpeningRecord] = []
    details: list[ScoreDetail] = []
    summaries: list[ScoreSummary] = []
    rankings: list[CandidateRanking] = []
    for table in content.tables:
        if len(table.rows) < 2:
            continue
        headers = table.rows[0]
        bidder_i = _header_index(headers, "投标人", "供应商", "单位名称", "supplier_name", "bidder")
        expert_i = _header_index(headers, "专家", "评委")
        factor_i = _header_index(headers, "评审因素", "评分项", "评分因素")
        max_i = _header_index(headers, "满分", "最高分")
        score_i = _header_index(headers, "得分", "原始分")
        weight_i = _header_index(headers, "权重", "折算比例")
        weighted_i = _header_index(headers, "折算得分", "加权得分")
        total_i = _header_index(headers, "总得分", "总分", "合计得分")
        rank_i = _header_index(headers, "排名", "排序", "名次")
        price_i = _header_index(headers, "投标报价", "报价金额", "投标总价", "bid_price")
        lot_i = _header_index(headers, "标段", "包号", "采购包")
        if bidder_i is None:
            continue
        for offset, row in enumerate(table.rows[1:], start=2):
            bidder = _cell(row, bidder_i)
            if not bidder or bidder in {"合计", "总计"}:
                continue
            source = SourceLocation(sheet=table.sheet, row=offset, cell=f"{table.sheet}!{offset}")
            lot = _cell(row, lot_i)
            if price_i is not None and _number(_cell(row, price_i)) is not None:
                openings.append(OpeningRecord(bidder=bidder, lot=lot, bid_price=_number(_cell(row, price_i)), source=source))
            if factor_i is not None and score_i is not None:
                details.append(ScoreDetail(
                    bidder=bidder, expert=_cell(row, expert_i), lot=lot,
                    factor=_cell(row, factor_i), max_score=_number(_cell(row, max_i)),
                    raw_score=_number(_cell(row, score_i)), weight=_number(_cell(row, weight_i)),
                    weighted_score=_number(_cell(row, weighted_i)), source=source,
                ))
            if total_i is not None or rank_i is not None:
                rank = _integer(_cell(row, rank_i))
                summaries.append(ScoreSummary(bidder=bidder, lot=lot, total_score=_number(_cell(row, total_i)), rank=rank, source=source))
                if rank is not None:
                    rankings.append(CandidateRanking(bidder=bidder, lot=lot, rank=rank, source=source))
    return openings, details, summaries, rankings


def _extract_text_records(
    text: str,
    sections: list[DocumentSection],
    document_subtype: str,
) -> tuple[list[str], list[RejectionRecord], list[EvaluationOpinion], list[CandidateRanking]]:
    invalid_bid_clauses: list[str] = []
    rejections: list[RejectionRecord] = []
    opinions: list[EvaluationOpinion] = []
    rankings: list[CandidateRanking] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        source = SourceLocation(line_start=line_number)
        if any(word in line for word in ("废标", "否决投标", "投标无效", "响应无效")):
            bidder_match = re.search(r"(?:投标人|供应商)[：:]?\s*([^，,；;。]+)", line)
            result_document = document_subtype in {"评标报告", "废标说明", "评审意见", "中标候选人推荐表"}
            if not result_document:
                invalid_bid_clauses.append(line)
            elif bidder_match:
                reason_match = re.search(r"(?:原因|理由)[：:]?\s*(.+)$", line)
                rejections.append(RejectionRecord(
                    bidder=bidder_match.group(1).strip(),
                    reason=(reason_match.group(1).strip() if reason_match else line),
                    cited_clause="；".join(re.findall(r"《[^》]+》第[^，。；]+条", line)),
                    evidence=line,
                    source=source,
                ))
        if any(word in line for word in ("评审意见", "评标意见", "评审结论")):
            opinion = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
            opinions.append(EvaluationOpinion(opinion=opinion, evidence=line, source=source))
        candidate = re.search(r"第([一二三四五六七八九十\d]+)中标候选人[：:]?\s*([^，,；;。]+)", line)
        if candidate:
            chinese = "一二三四五六七八九十"
            rank_text = candidate.group(1)
            rank = int(rank_text) if rank_text.isdigit() else chinese.find(rank_text) + 1
            rankings.append(CandidateRanking(bidder=candidate.group(2).strip(), rank=rank or None, evidence=line, source=source))
    return unique_keep_order(invalid_bid_clauses)[:100], rejections[:100], opinions[:100], rankings[:100]


def _merge_semantic_records(local: list, semantic: list) -> list:
    result = list(local)
    seen = {
        (type(item).__name__, getattr(item, "evidence", ""), getattr(item, "bidder", ""), getattr(item, "opinion", ""))
        for item in result
    }
    for item in semantic:
        key = (type(item).__name__, getattr(item, "evidence", ""), getattr(item, "bidder", ""), getattr(item, "opinion", ""))
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result[:100]


def _expected_visual_marks(content: ParsedFileContent) -> list[tuple[str, int, str]]:
    requirements: list[tuple[str, int, str]] = []
    seal_patterns = (r"(?:投标人|供应商|单位)?[（(]?盖章[）)]?[：:]", r"加盖公章[：:]", r"公章[：:]")
    signature_patterns = (r"(?:法定代表人|授权代表|评委|专家|签署人)[（(]?(?:签字|签名)[）)]?[：:]",)
    for page in content.pages:
        for line in page.text.splitlines():
            compact = line.strip()
            if any(re.search(pattern, compact) for pattern in seal_patterns):
                requirements.append(("seal", page.number, compact[:200]))
            if any(re.search(pattern, compact) for pattern in signature_patterns):
                requirements.append(("signature", page.number, compact[:200]))
    return list(dict.fromkeys(requirements))[:100]


def _seal_matches_entity(recognized_text: str, entities: list[str]) -> bool | None:
    recognized = re.sub(r"系统测试专用章|测试专用章|合同专用章|财务专用章|公章", "", recognized_text)
    recognized = re.sub(r"[^\w\u4e00-\u9fff]", "", recognized)
    candidates = [re.sub(r"[^\w\u4e00-\u9fff]", "", item) for item in entities if item]
    if not recognized or not candidates:
        return None
    if any(recognized in candidate or candidate in recognized for candidate in candidates):
        return True
    # Only tolerate contiguous leading/trailing OCR omissions (handled by the
    # substring check above). A single substituted character may represent a
    # genuinely different legal entity, so edit-distance matching is unsafe.
    return False


class DocumentParserAgent:
    name = "文档解析智能体"

    def run(
        self,
        files: list[UploadedFileInfo],
        project_name: str,
        enable_semantic_enhancement: bool = True,
    ) -> tuple[list[ParsedDocument], AgentResult, dict[str, str]]:
        parsed_docs: list[ParsedDocument] = []
        raw_texts: dict[str, str] = {}
        issues: list[Issue] = []
        semantic_enhancement_count = 0

        for file_info in files:
            parse_plan = _build_parse_plan(file_info)
            parse_attempts: list[ParseAttempt] = []
            content = parse_file(file_info.saved_path)
            parse_attempts.append(ParseAttempt(
                attempt=1, action="执行确定性解析", tool=content.selected_tool,
                trigger="按文件格式执行首选解析方案", outcome="completed",
            ))
            text = content.text
            raw_texts[file_info.file_id] = text
            fields = _extract_fields(content, project_name)
            sections = _extract_sections(content)
            document_subtype = _infer_subtype_from_content(
                infer_document_subtype(file_info.filename, file_info.file_type), text
            )
            if document_subtype == "响应文件" and "tenderer" not in fields:
                response_tenderer = _response_tenderer_field(content)
                if response_tenderer:
                    fields["tenderer"] = response_tenderer
            has_recognized_heading = any(
                section.title != "文档正文" for section in sections
            )
            required_semantic_fields = (
                ("project_name", "budget", "price_limit")
                if file_info.file_type == "招标文件"
                else ("project_name",)
            )
            missing_semantic_fields = [
                name
                for name in required_semantic_fields
                if name not in fields
                or fields[name].confidence < 0.75
                or fields[name].requires_human_review
            ]
            if document_subtype in {"评标报告", "废标说明", "评审意见", "中标候选人推荐表"}:
                missing_semantic_fields.extend([
                    "rejection_records",
                    "evaluation_opinions",
                    "candidate_rankings",
                ])
                missing_semantic_fields = unique_keep_order(missing_semantic_fields)
            unfilled_template = _is_unfilled_template(text)
            semantic_rejections: list[RejectionRecord] = []
            semantic_opinions: list[EvaluationOpinion] = []
            semantic_rankings: list[CandidateRanking] = []
            if unfilled_template and missing_semantic_fields:
                content.warnings.append(
                    "检测到未填写的示范模板，跳过 Dify 语义增强并转人工复核"
                )
                content.tool_trace.append("跳过语义增强: 未填写示范模板")
            if (
                text.strip()
                and enable_semantic_enhancement
                and document_semantic_enhancer.enabled
                and not unfilled_template
                and (
                    _needs_semantic_enhancement(sections, fields, file_info.file_type)
                    or document_subtype in {"评标报告", "废标说明", "评审意见", "中标候选人推荐表"}
                )
            ):
                try:
                    semantic_result = document_semantic_enhancer.enhance(
                            text,
                            file_info.file_id,
                            requested_fields=missing_semantic_fields,
                            include_sections=not has_recognized_heading,
                            document_type=document_subtype,
                            parser_context={
                                "document_type": document_subtype,
                                "sections": [section.model_dump(mode="json") for section in sections[:100]],
                                "key_fields": {name: field.model_dump(mode="json") for name, field in fields.items()},
                                "tables": [
                                    {"page": table.page, "sheet": table.sheet, "rows": table.rows[:100]}
                                    for table in content.tables[:20]
                                ],
                            },
                        )
                    semantic_sections, semantic_fields, semantic_warnings = semantic_result
                    semantic_rejections = semantic_result.rejection_records
                    semantic_opinions = semantic_result.evaluation_opinions
                    semantic_rankings = semantic_result.candidate_rankings
                    if semantic_sections and not any(
                        section.title != "文档正文" for section in sections
                    ):
                        sections = semantic_sections
                    for name, semantic_field in semantic_fields.items():
                        semantic_field.value = _clean_field_value(name, semantic_field.value)
                        current = fields.get(name)
                        if current is None or semantic_field.confidence > current.confidence:
                            fields[name] = semantic_field
                    content.warnings.extend(semantic_warnings)
                    content.tool_trace.append("执行语义增强: Dify文档解析Workflow")
                    content.selected_tool = (
                        f"{content.selected_tool} -> Dify文档语义解析工具"
                    )
                    semantic_enhancement_count += 1
                    parse_attempts.append(ParseAttempt(
                        attempt=len(parse_attempts) + 1, action="自动语义补全重试",
                        tool="Dify文档解析Workflow", trigger="章节或关键字段质量未达到阈值",
                        outcome="completed",
                    ))
                except DifyWorkflowError as exc:
                    content.warnings.append(f"Dify 文档语义增强失败: {exc}")
                    content.tool_trace.append("语义增强失败: Dify文档解析Workflow")
                    parse_attempts.append(ParseAttempt(
                        attempt=len(parse_attempts) + 1, action="自动语义补全重试",
                        tool="Dify文档解析Workflow", trigger="章节或关键字段质量未达到阈值",
                        outcome="failed",
                    ))

            bidders = (
                _response_entities(text)
                if document_subtype == "响应文件"
                else find_lines(text, ["投标人", "供应商", "中标候选人"], limit=30)
            )
            known_entities = [fields.get("tenderer", ExtractedField()).value, *bidders]
            checks = _quality_checks(content, fields, file_info.file_type)
            visual_checks: list[SealSignatureCheck] = []
            if settings.visual_analysis_enabled and file_info.filename.lower().endswith(".pdf"):
                visual_result = analyze_document_visuals(
                    file_info.saved_path,
                    max_pages=settings.visual_analysis_max_pages,
                    model_path=settings.visual_detector_model_path,
                )
                content.tool_trace.append(
                    f"执行视觉检测: red-seal-rule-v1（{visual_result.analyzed_pages} 页）"
                )
                content.warnings.extend(visual_result.warnings)
                visual_requirements = _expected_visual_marks(content)
                for detection in visual_result.detections:
                    entity_match = _seal_matches_entity(detection.recognized_text, known_entities)
                    expected_text = next(
                        (
                            evidence
                            for target, page, evidence in visual_requirements
                            if target == detection.detection_type and page == detection.page
                        ),
                        "",
                    )
                    low_confidence = (
                        detection.confidence < settings.visual_review_threshold
                        or (detection.detection_type == "seal" and detection.ocr_confidence < 0.60)
                    )
                    status = (
                        "mismatch"
                        if entity_match is False
                        else "low_confidence"
                        if low_confidence
                        else "detected"
                    )
                    visual_checks.append(
                        SealSignatureCheck(
                            target=detection.detection_type,
                            expected=bool(expected_text),
                            status=status,
                            confidence=detection.confidence,
                            bbox=list(detection.bbox),
                            recognized_text=detection.recognized_text,
                            ocr_confidence=detection.ocr_confidence,
                            detector=detection.detector,
                            validation_message=(
                                "检测到的印章文字与已提取主体信息不一致，需人工核对"
                                if entity_match is False
                                else "检测到印章，但印章文字未能可靠识别，需核对印章主体"
                                if detection.detection_type == "seal" and detection.ocr_confidence < 0.60
                                else "视觉标记及主体信息初步匹配"
                            ),
                            source_text=expected_text,
                            source=SourceLocation(page=detection.page),
                            requires_human_review=status in {
                                "not_detected", "not_checked", "low_confidence", "mismatch"
                            },
                        )
                    )
                if document_subtype in {"响应文件", "评标报告", "废标说明", "评审意见", "中标候选人推荐表"}:
                    for target, page, evidence in visual_requirements:
                        page_matches = [item for item in visual_checks if item.target == target and item.source.page == page]
                        if page_matches:
                            for item in page_matches:
                                item.expected = True
                            continue
                        visual_checks.append(
                            SealSignatureCheck(
                                target=target,
                                expected=True,
                                status="not_detected" if target == "seal" else "not_checked",
                                detector="red-seal-rule-v1" if target == "seal" else "signature-model-required",
                                validation_message=f"第 {page} 页存在{evidence}，但未确认对应的{'印章' if target == 'seal' else '签名'}",
                                source_text=evidence,
                                source=SourceLocation(page=page),
                                requires_human_review=True,
                            )
                        )
                if visual_result.warnings or any(item.requires_human_review for item in visual_checks):
                    checks.append(
                        DocumentQualityCheck(
                            code="visual_detection",
                            status="warning",
                            message="印章视觉检测存在未完成或低置信度结果",
                            requires_human_review=True,
                        )
                    )
            for visual_check in visual_checks:
                if not visual_check.requires_human_review:
                    continue
                status_names = {
                    "not_detected": "应检测到但未检测到",
                    "not_checked": "当前能力未完成检测",
                    "low_confidence": "检测置信度不足",
                    "mismatch": "识别内容与主体信息不一致",
                    "uncertain": "检测结果不确定",
                }
                target_name = "印章" if visual_check.target == "seal" else "签名"
                status_name = status_names.get(visual_check.status, visual_check.status)
                page = visual_check.source.page
                source_text = visual_check.source_text.strip()
                refs: list[EvidenceRef] = []
                if source_text:
                    refs.append(
                        EvidenceRef(
                            document_id=file_info.file_id,
                            quote=source_text,
                            page=page,
                            section=f"{target_name}核验位置",
                            source_type="text",
                        )
                    )
                refs.append(
                    EvidenceRef(
                        document_id=file_info.file_id,
                        quote=(
                            f"状态={visual_check.status}；检测器={visual_check.detector or '未记录'}；"
                            f"检测置信度={visual_check.confidence:.2f}；OCR置信度={visual_check.ocr_confidence:.2f}；"
                            f"识别文字={visual_check.recognized_text or '未识别'}；坐标={visual_check.bbox or '未记录'}；"
                            f"说明={visual_check.validation_message or status_name}"
                        ),
                        page=page,
                        section=f"{target_name}视觉检测结果",
                        source_type="derived",
                        derived_from=[source_text] if source_text else [],
                    )
                )
                visual_evidence = [source_text] if source_text else []
                visual_evidence.append(
                    f"第{page}页{target_name}检测：状态={visual_check.status}；"
                    f"检测置信度={visual_check.confidence:.2f}；"
                    f"OCR置信度={visual_check.ocr_confidence:.2f}；"
                    f"识别文字={visual_check.recognized_text or '未识别'}"
                )
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中" if visual_check.status == "mismatch" else "低",
                        issue_type=f"{target_name}视觉核验待复核",
                        source_file=file_info.filename,
                        source_location=f"第 {page} 页/{source_text or target_name}" if page else target_name,
                        description=(
                            f"{target_name}视觉核验状态为“{status_name}”。"
                            f"{visual_check.validation_message}"
                        ),
                        basis="文档形式核验与视觉检测规则",
                        suggestion=f"请人工查看原始文件第 {page} 页，确认{target_name}是否符合文件要求。" if page else f"请人工核对原始文件中的{target_name}。",
                        evidence=visual_evidence,
                        evidence_refs=refs,
                        requires_human_review=True,
                        assessment="待人工判断",
                        final_status="human_review",
                        detection_status=visual_check.status,
                        confidence=max(visual_check.confidence, visual_check.ocr_confidence),
                    )
                )
            requires_review = any(check.requires_human_review for check in checks)
            failed = any(check.status == "failed" for check in checks)
            quality_score = _quality_score(checks)
            for attempt in parse_attempts:
                attempt.quality_score = quality_score
            if quality_score < parse_plan.quality_threshold and len(parse_attempts) == 1:
                parse_attempts.append(ParseAttempt(
                    attempt=2, action="质量门回退决策", tool="人工复核队列",
                    trigger=f"质量分 {quality_score:.2f} 低于阈值 {parse_plan.quality_threshold:.2f}，且自动工具无可用增益",
                    outcome="completed", quality_score=quality_score,
                ))
            warnings = [*content.warnings, *[check.message for check in checks if check.status != "passed"]]

            openings, score_details, score_summaries, table_rankings = _extract_tabular_records(content)
            invalid_bid_clauses, rejection_records, evaluation_opinions, text_rankings = _extract_text_records(
                text,
                sections,
                document_subtype,
            )
            rejection_records = _merge_semantic_records(rejection_records, semantic_rejections)
            evaluation_opinions = _merge_semantic_records(evaluation_opinions, semantic_opinions)
            text_rankings = _merge_semantic_records(text_rankings, semantic_rankings)
            document_tables = [DocumentTable(page=table.page, page_end=table.page_end, sheet=table.sheet, start_row=table.start_row, continued=table.continued, rows=table.rows) for table in content.tables]
            evidence_chunks = _chunks(file_info.file_id, sections, document_tables)
            parsed_docs.append(
                ParsedDocument(
                    file_id=file_info.file_id,
                    filename=file_info.filename,
                    file_type=file_info.file_type,
                    document_subtype=document_subtype,
                    document_role=(
                        file_info.document_role
                        if file_info.document_role != "other"
                        else classify_document_role(
                            file_info.filename, file_info.file_type, document_subtype
                        )
                    ),
                    text_length=len(text),
                    project_name=fields.get("project_name", ExtractedField()).value or guess_project_name(text, project_name),
                    tenderer=fields.get("tenderer", ExtractedField()).value,
                    procurement_agency=fields.get("procurement_agency", ExtractedField()).value,
                    bidders=unique_keep_order(bidders),
                    bid_prices=money_values(text),
                    qualification_requirements=find_lines(text, ["资格", "资质", "业绩", "证书"], limit=8),
                    scoring_criteria=find_lines(text, ["评分", "分值", "评审", "评标办法"], limit=8),
                    key_clauses=find_lines(text, ["不得", "必须", "应当", "须", "合同", "保证金"], limit=12),
                    page_count=content.page_count,
                    is_scanned=content.is_scanned,
                    parse_status="failed" if failed else "warning" if warnings else "success",
                    selected_tool=content.selected_tool,
                    tool_trace=content.tool_trace,
                    ocr_applied=content.ocr_applied,
                    ocr_confidence=content.ocr_confidence,
                    sections=sections,
                    tables=document_tables,
                    layout_elements=[LayoutElement(element_type=element.element_type, text=element.text, page=element.page, order=element.order, bbox=element.bbox, source_name=element.source_name) for element in content.layout_elements],
                    sheet_names=content.sheet_names,
                    opening_records=openings,
                    score_details=score_details,
                    score_summaries=score_summaries,
                    invalid_bid_clauses=invalid_bid_clauses,
                    rejection_records=rejection_records,
                    evaluation_opinions=evaluation_opinions,
                    candidate_rankings=table_rankings + text_rankings,
                    seal_signature_checks=visual_checks,
                    extracted_fields=fields,
                    quality_checks=checks,
                    parse_plan=parse_plan,
                    parse_attempts=parse_attempts,
                    quality_score=quality_score,
                    evidence_chunks=evidence_chunks,
                    warnings=unique_keep_order(warnings),
                )
            )

            if requires_review:
                issue_details = {
                    "text_extracted": (
                        "正文提取异常",
                        "请检查文件是否损坏、加密或为无文本扫描件，必要时重新上传或执行 OCR。",
                    ),
                    "scan_detection": (
                        "OCR识别质量",
                        "请对照原文件抽查关键页，并在需要时使用更高质量扫描件重新执行 OCR。",
                    ),
                    "key_fields": (
                        "关键字段待确认",
                        "请对照原文确认项目名称、预算、最高限价等字段，并处理低置信度或冲突值。",
                    ),
                    "encoding_quality": (
                        "文本编码异常",
                        "请检查源文件编码和解析结果，修正乱码后重新核验。",
                    ),
                }
                for check in checks:
                    if not check.requires_human_review:
                        continue
                    if check.code == "visual_detection":
                        continue
                    issue_type, suggestion = issue_details.get(
                        check.code,
                        ("文档解析质量", "请对照原文核对解析结果。"),
                    )
                    issues.append(
                        Issue(
                            agent=self.name,
                            risk_level="中" if check.status == "failed" else "低",
                            issue_type=issue_type,
                            source_file=file_info.filename,
                            source_location=f"质量检查/{check.code}",
                            description=check.message,
                            basis="文档解析质量核验规则",
                            suggestion=suggestion,
                            evidence=[check.message],
                            requires_human_review=True,
                            assessment="待人工判断",
                            confidence=0.35 if check.status == "failed" else 0.55,
                        )
                    )

        warning_count = sum(len(doc.warnings) for doc in parsed_docs)
        summary = (
            f"已解析 {len(parsed_docs)} 个文件，提取正文、章节、表格、关键字段及来源信息，"
            f"发现 {warning_count} 条质量告警。"
        )
        return (
            parsed_docs,
            AgentResult(
                agent=self.name,
                summary=summary,
                issues=issues,
                data={
                    "document_count": len(parsed_docs),
                    "warning_count": warning_count,
                    "requires_human_review": bool(issues),
                    "semantic_enhancement_count": semantic_enhancement_count,
                },
            ),
            raw_texts,
        )
