from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import ensure_data_dirs, settings
from app.schemas.contract import ContractGenerationRequest, ContractValidationItem
from app.schemas.task import TaskRecord, TaskResult
from app.services.contract_service import amount_to_chinese
from app.services.report_service import (
    confidence_rows, final_agent_conclusion, final_report_conclusion,
    issue_is_confirmed, issue_needs_review, public_warning, report_agent_names,
    report_basis, report_conclusion_status, report_display_title,
    report_status_label, report_suggestion, select_report_issues,
)


FONT_NAME = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ChineseTitle",
            parent=base["Title"],
            fontName=FONT_NAME,
            fontSize=20,
            leading=28,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "h1": ParagraphStyle(
            "ChineseH1",
            parent=base["Heading1"],
            fontName=FONT_NAME,
            fontSize=14,
            leading=20,
            textColor=colors.HexColor("#1F4D78"),
            spaceBefore=10,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "ChineseH2",
            parent=base["Heading2"],
            fontName=FONT_NAME,
            fontSize=12,
            leading=18,
            textColor=colors.HexColor("#2E74B5"),
            spaceBefore=8,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "ChineseBody",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=10.5,
            leading=17,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "ChineseSmall",
            parent=base["BodyText"],
            fontName=FONT_NAME,
            fontSize=9,
            leading=14,
        ),
    }


def _p(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _label_value_p(label: object, value: object, style: ParagraphStyle) -> Paragraph:
    safe_label = escape(str(label))
    safe_value = escape(str(value)).replace("\n", "<br/>")
    return Paragraph(f"<b>{safe_label}：</b>{safe_value}", style)


def _page_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def _report_page_decorator(report_title: str, report_status: str):
    def decorate(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D7DEE8"))
        canvas.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(20 * mm, A4[1] - 11 * mm, report_title)
        canvas.drawRightString(A4[0] - 20 * mm, A4[1] - 11 * mm, report_status)
        canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canvas.drawString(20 * mm, 10.5 * mm, "招投标全过程智能核验 · 内部工作文件")
        canvas.drawRightString(A4[0] - 20 * mm, 10.5 * mm, f"第 {document.page} 页")
        canvas.restoreState()
    return decorate


def create_report_pdf(task: TaskRecord, result: TaskResult) -> Path:
    ensure_data_dirs()
    path = settings.reports_dir / f"{task.task_id}.pdf"
    styles = _styles()
    report_agents = [item for item in result.agent_results if item.agent == "报告生成智能体"]
    report_data = report_agents[-1].data if report_agents else {}
    package = report_data.get("report_package", {}) if isinstance(report_data, dict) else {}
    project_info = package.get("project_info", {}) if isinstance(package, dict) else {}
    status = str(report_data.get("report_status", "待复核版"))
    status_label = report_status_label(status)
    report_type = report_data.get("output_type", "综合智能核验报告")
    template_type = report_data.get("template_type", "标准审查报告")
    report_title = report_display_title(report_type, template_type)
    report_issues = select_report_issues(result, report_type)
    if template_type == "简版管理层报告":
        report_issues = report_issues[:10]
    counts = {
        level: sum(1 for issue in report_issues if issue.risk_level == level)
        for level in ("高", "中", "低")
    }
    pending = sum(1 for issue in report_issues if issue_needs_review(issue))
    confirmed = sum(1 for issue in report_issues if issue_is_confirmed(issue))
    story = [
        Spacer(1, 34 * mm),
        _p("招投标全过程智能核验", styles["h2"]),
        _p(report_title, styles["title"]),
        _p(project_info.get("project_name") or task.project_name, styles["h2"]),
        Spacer(1, 18 * mm),
    ]
    metadata = [
        [_p("任务编号", styles["small"]), _p(task.task_id, styles["small"])],
        [_p("项目编号", styles["small"]), _p(task.project_id, styles["small"])],
        [_p("核验类型", styles["small"]), _p(task.check_type, styles["small"])],
        [_p("报告类型", styles["small"]), _p(report_type, styles["small"])],
        [_p("文档模板", styles["small"]), _p(template_type, styles["small"])],
        [_p("报告状态", styles["small"]), _p(status_label, styles["small"])],
        [_p("资料数量", styles["small"]), _p(len(result.parsed_documents), styles["small"])],
        [_p("核验结论", styles["small"]), _p(final_report_conclusion(result, report_issues), styles["small"])],
    ]
    table = Table(metadata, colWidths=[35 * mm, 125 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C2CC")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 10 * mm),
            _p("内部工作文件 · 结论须按复核状态使用", styles["small"]),
            PageBreak(),
            _p("一、执行摘要", styles["h1"]),
            _p(final_report_conclusion(result, report_issues), styles["body"]),
        ]
    )
    risk_table = Table(
        [
            [_p(x, styles["small"]) for x in ("明确问题", "待复核", "高风险", "中风险", "低风险", "合计")],
            [_p(x, styles["small"]) for x in (confirmed, pending, counts["高"], counts["中"], counts["低"], len(report_issues))],
        ],
        colWidths=[26.6 * mm] * 6,
    )
    risk_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B8C2CC")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([risk_table, _p("二、项目与核验范围", styles["h1"])])
    for label, value in (
        ("项目名称", project_info.get("project_name") or task.project_name),
        ("项目编号", task.project_id),
        ("采购人/招标人", project_info.get("tenderer") or "未识别"),
        ("采购代理机构", project_info.get("procurement_agency") or "未识别"),
        ("业务系统基准数据", "已提供" if task.system_record else "未提供"),
    ):
        story.append(_label_value_p(label, value, styles["body"]))

    field_sources = project_info.get("field_sources", {})
    if isinstance(field_sources, dict) and field_sources:
        story.append(_p("关键字段来源", styles["h2"]))
        source_rows = [[_p(x, styles["small"]) for x in ("字段", "提取值", "来源文件", "位置", "置信度")]]
        labels = {
            "project_name": "项目名称", "tenderer": "采购人/招标人",
            "procurement_agency": "采购代理机构", "budget": "项目预算",
            "price_limit": "最高投标限价", "deadline": "截止时间",
        }
        for name, label in labels.items():
            source = field_sources.get(name)
            if not isinstance(source, dict):
                continue
            confidence = float(source.get("confidence", 0))
            value = "待人工确认" if source.get("requires_human_review") or confidence < 0.75 else source.get("value", "")
            source_rows.append([_p(x, styles["small"]) for x in (
                label, value, source.get("source_file", "未定位"),
                source.get("source_location", "未定位"), f"{confidence:.0%}",
            )])
        source_table = Table(source_rows, colWidths=[25*mm, 38*mm, 43*mm, 37*mm, 17*mm], repeatRows=1)
        source_table.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#B8C2CC")),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E8EEF5")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
            ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(source_table)

    story.append(_p("核验资料清单", styles["h2"]))
    document_rows = [[_p(x, styles["small"]) for x in ("序号", "文件名称", "类型", "页数", "状态", "质量提示")]]
    for index, parsed in enumerate(result.parsed_documents, start=1):
        document_rows.append(
            [
                _p(index, styles["small"]),
                _p(parsed.filename, styles["small"]),
                _p(parsed.document_subtype or parsed.file_type, styles["small"]),
                _p(parsed.page_count, styles["small"]),
                _p(parsed.parse_status, styles["small"]),
                _p("；".join(dict.fromkeys(public_warning(item) for item in parsed.warnings[:3])) or "无", styles["small"]),
            ]
        )
    documents_table = Table(document_rows, colWidths=[10*mm, 50*mm, 27*mm, 14*mm, 19*mm, 40*mm], repeatRows=1)
    documents_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#B8C2CC")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E8EEF5")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.extend([documents_table, _p("三、核验方法与执行过程", styles["h1"])])
    story.append(_p(
        "本次核验采用文档解析、确定性规则、法规知识库检索、智能体语义分析与人工复核相结合的方式，"
        "覆盖文档完整性、基础信息一致性、评审数据复算、异常关联线索和整改闭环。",
        styles["body"],
    ))
    story.append(_p("四、文档解析与数据质量", styles["h1"]))
    for parsed in result.parsed_documents:
        story.append(_p(parsed.filename, styles["h2"]))
        quality_text = (
            f"解析状态：{parsed.parse_status}；页数：{parsed.page_count}；章节：{len(parsed.sections)}；"
            f"表格：{len(parsed.tables)}；OCR：{'已启用' if parsed.ocr_applied else '未启用'}。"
        )
        story.append(_p(quality_text, styles["body"]))
        for warning in dict.fromkeys(public_warning(item) for item in parsed.warnings[:10]):
            story.append(_p(f"质量提示：{warning}", styles["small"]))
    story.append(_p("五、风险与问题总览", styles["h1"]))
    if report_issues:
        issue_rows = [[_p(x, styles["small"]) for x in ("序号", "编号", "类型", "风险", "问题摘要", "复核")]]
        for index, issue in enumerate(report_issues, start=1):
            issue_rows.append([
                _p(index, styles["small"]), _p(issue.issue_id or "未生成", styles["small"]),
                _p(issue.issue_type, styles["small"]), _p(issue.risk_level, styles["small"]),
                _p(issue.description, styles["small"]),
                _p("待复核" if issue_needs_review(issue) else "明确问题", styles["small"]),
            ])
        issue_table = Table(issue_rows, colWidths=[10*mm, 25*mm, 27*mm, 14*mm, 65*mm, 19*mm], repeatRows=1)
        issue_table.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#B8C2CC")),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E8EEF5")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
            ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(issue_table)
    story.append(_p("六、问题逐项说明", styles["h1"]))
    if not report_issues:
        story.append(_p("未发现需要输出的明确或潜在问题。", styles["body"]))
    for index, issue in enumerate(report_issues, start=1):
        story.append(_p(f"{index}. {issue.issue_type}（{issue.risk_level}风险）", styles["h2"]))
        evidence = "；".join(issue.evidence) or "；".join(ref.quote for ref in issue.evidence_refs) or "未提供"
        for label, value in (
            ("问题编号", issue.issue_id or f"R-{index:03d}"),
            ("最终状态", report_conclusion_status(issue, status)),
            ("检测状态", issue.detection_status or "不适用"),
            ("问题描述", issue.description),
            ("原文证据", evidence),
            ("判断依据", report_basis(issue, status)),
            ("修改建议", report_suggestion(issue, status)),
            ("来源智能体", issue.agent),
            ("来源位置", issue.source_location or "未定位"),
            *confidence_rows(issue, status),
            ("人工复核", "待完成" if issue_needs_review(issue) else ("已完成" if status == "正式核验版" else "无需复核")),
        ):
            story.append(_label_value_p(label, value, styles["body"]))
    story.append(_p("七、专项智能体结论", styles["h1"]))
    for agent_name in report_agent_names(result):
        story.extend(
            [
                _p(agent_name, styles["h2"]),
                _p(final_agent_conclusion(result, agent_name, report_issues), styles["body"]),
            ]
        )
    story.append(_p("八、人工复核与整改闭环", styles["h1"]))
    review_agents = [item for item in result.agent_results if item.agent == "人工复核节点"]
    story.append(_p(
        review_agents[-1].summary if review_agents else "本任务尚未形成已提交的人工复核结论。",
        styles["body"],
    ))
    for index, issue in enumerate(report_issues, start=1):
        priority = "立即" if issue.risk_level == "高" else "优先" if issue.risk_level == "中" else "常规"
        story.append(_p(f"{index}. [{priority}] {report_suggestion(issue, status)}", styles["body"]))
    story.extend([
        _p("九、综合结论", styles["h1"]),
        _p(final_report_conclusion(result, report_issues), styles["body"]),
        PageBreak(),
        _p("附录A 证据索引", styles["h1"]),
    ])
    evidence_rows = [[_p(x, styles["small"]) for x in ("证据编号", "问题编号", "文件", "页码/位置", "类型", "说明")]]
    evidence_index = 0
    for issue in report_issues:
        refs = issue.evidence_refs
        if refs:
            for ref in refs:
                evidence_index += 1
                location = ref.section or "未定位"
                if ref.page:
                    location += f"，第{ref.page}页"
                evidence_type = {"text":"原文", "table":"表格", "metadata":"元数据", "derived":"检测结果"}.get(ref.source_type, ref.source_type)
                evidence_rows.append([_p(x, styles["small"]) for x in (
                    f"E-{evidence_index:03d}", issue.issue_id or "未生成",
                    issue.source_file or ref.document_id, location, evidence_type, ref.quote,
                )])
        else:
            for quote in issue.evidence or ["未提供"]:
                evidence_index += 1
                evidence_rows.append([_p(x, styles["small"]) for x in (
                    f"E-{evidence_index:03d}", issue.issue_id or "未生成",
                    issue.source_file or "未定位", issue.source_location or "未定位", "原文", quote,
                )])
    evidence_table = Table(evidence_rows, colWidths=[18*mm, 23*mm, 31*mm, 27*mm, 19*mm, 42*mm], repeatRows=1)
    evidence_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#B8C2CC")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E8EEF5")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 3), ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story.append(evidence_table)
    story.extend([
        _p("确认与签署", styles["h1"]),
        _p("评标委员会确认意见：____________________________________________", styles["body"]),
        _p("负责人签字：________________    日期：________年____月____日", styles["body"]),
        _p("采购人/招标人确认：________________    日期：________年____月____日", styles["body"]),
    ])
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"{project_info.get('project_name') or task.project_name}智能核验报告",
    )
    document.build(story, onFirstPage=_page_footer, onLaterPages=_report_page_decorator(report_title, status_label))
    return path


def create_contract_pdf(
    contract_id: str,
    contract_number: str,
    request: ContractGenerationRequest,
    validation_items: list[ContractValidationItem],
    supplementary_clauses: dict[str, list[str]] | None = None,
) -> Path:
    ensure_data_dirs()
    path = settings.contracts_dir / f"{contract_id}.pdf"
    styles = _styles()
    clauses = supplementary_clauses or {}
    story = [
        _p(request.template_type, styles["title"]),
        _p(f"合同编号：{contract_number}", styles["body"]),
        _p(f"项目编号：{request.project_id}", styles["body"]),
        _p(f"项目名称：{request.project_name}", styles["body"]),
        _p(f"采购人（甲方）：{request.purchaser.name}", styles["body"]),
        _p(f"供应商（乙方）：{request.supplier.name}", styles["body"]),
    ]
    sections = [
        ("第一条 合同标的与服务范围", request.service_scope),
        ("第二条 合同金额", [f"合同总金额（含税）为人民币￥{request.contract_amount:,.2f}元（大写：{amount_to_chinese(request.contract_amount)}）。"]),
        ("第三条 服务期限", [f"服务期限自{request.service_start_date.isoformat()}起至{request.service_end_date.isoformat()}止。"]),
        ("第四条 付款方式", request.payment_terms),
        ("第五条 验收标准", request.acceptance_criteria),
        ("第六条 服务水平与考核", clauses.get("service_level_terms") or ["乙方应按照采购文件、响应文件及双方确认的服务水平要求提供服务。"]),
        ("第七条 数据安全与保密", clauses.get("data_security_terms") or ["乙方应遵守适用的数据安全、网络安全和保密要求。"]),
        ("第八条 知识产权", clauses.get("intellectual_property_terms") or ["双方已有知识产权归原权利人所有，新增成果权属按项目约定执行。"]),
        ("第九条 变更管理", clauses.get("change_management_terms") or ["合同变更应履行书面确认程序。"]),
        ("第十条 违约责任", request.breach_terms or ["待双方补充。"]),
        ("第十一条 合同解除与终止", clauses.get("termination_terms") or ["合同终止后应完成资料、账号和数据移交。"]),
        ("第十二条 不可抗力", clauses.get("force_majeure_terms") or ["发生不可抗力时应及时通知并提供证明。"]),
        ("第十三条 争议解决", [request.dispute_resolution]),
    ]
    for title, paragraphs in sections:
        story.append(_p(title, styles["h1"]))
        for index, text in enumerate(paragraphs, start=1):
            story.append(_p(f"{index}. {text}", styles["body"]))
    story.extend([PageBreak(), _p("生成校验与人工复核清单", styles["h1"])])
    for item in validation_items:
        story.append(_p(f"[{item.level}] {item.message}", styles["body"]))
    story.extend(
        [
            Spacer(1, 8 * mm),
            _p("甲方（盖章）：____________________", styles["body"]),
            _p("乙方（盖章）：____________________", styles["body"]),
            _p("签署日期：________年____月____日", styles["body"]),
        ]
    )
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"{request.project_name}{request.template_type}",
    )
    document.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return path
