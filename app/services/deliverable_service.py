from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


HEADERS = {
    "compliance": ["问题编号", "风险等级", "问题类型", "问题描述", "原文证据", "依据条款", "原文出处", "整改建议", "最终状态"],
    "data": ["问题编号", "风险等级", "问题类型", "问题描述", "复算或比对证据", "核验依据", "原文出处", "处理建议", "排序/一致性结论"],
    "anomaly": ["线索编号", "风险等级", "异常类型", "线索描述", "证据链", "证据来源", "涉及主体", "分析依据", "处置建议"],
}


def create_deliverable_xlsx(payload: dict, kind: str, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = {"parse": "结构化解析", "compliance": "合规问题", "data": "数据核验", "anomaly": "异常线索"}[kind]
    if kind == "parse":
        _write_parse_package(wb, ws, payload["document_parse_package"])
    else:
        key = {"compliance": "compliance_issue_list", "data": "data_verification_result", "anomaly": "anomaly_warning_report"}[kind]
        rows = payload[key].get("issues", payload[key].get("warnings", []))
        _write_issue_sheet(ws, kind, rows)
    _style_workbook(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _write_parse_package(wb: Workbook, ws, package: dict) -> None:
    ws.append(["文件名称", "文档类型", "页数", "解析状态", "OCR", "章节数", "表格数", "项目名称", "采购人/招标人", "代理机构"])
    for doc in package.get("documents", []):
        ws.append([doc.get("filename", ""), doc.get("document_subtype", doc.get("file_type", "")), doc.get("page_count", 0), doc.get("parse_status", ""), "是" if doc.get("ocr_applied") else "否", len(doc.get("sections", [])), len(doc.get("tables", [])), doc.get("project_name", ""), doc.get("tenderer", ""), doc.get("procurement_agency", "")])
    for title, key in [("打分明细", "score_details"), ("汇总数据", "score_summaries"), ("废标说明", "rejection_records"), ("评审意见", "evaluation_opinions"), ("候选人排序", "candidate_rankings")]:
        sheet = wb.create_sheet(title)
        items = package.get(key, [])
        if not items:
            sheet.append(["暂无识别数据"])
            continue
        columns = list(dict.fromkeys(k for item in items for k in item.keys()))
        sheet.append(columns)
        for item in items:
            sheet.append([_cell(item.get(col, "")) for col in columns])


def _write_issue_sheet(ws, kind: str, rows: list[dict]) -> None:
    ws.append(HEADERS[kind])
    for item in rows:
        common = [item.get("issue_id", ""), item.get("risk_level", ""), item.get("issue_type", ""), item.get("description", "")]
        if kind == "compliance":
            values = common + [item.get("evidence", ""), item.get("basis", ""), item.get("source_location", ""), item.get("suggestion", ""), item.get("final_status", item.get("assessment", ""))]
        elif kind == "data":
            values = common + [item.get("evidence", ""), item.get("basis", ""), item.get("source_location", ""), item.get("suggestion", ""), item.get("assessment", item.get("final_status", ""))]
        else:
            values = common + [item.get("evidence", ""), _cell(item.get("evidence_refs", item.get("evidence_sources", ""))), _cell(item.get("related_entities", "")), item.get("basis", ""), item.get("suggestion", "")]
        ws.append([_cell(value) for value in values])


def _cell(value) -> str | int | float:
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    return value if isinstance(value, (str, int, float)) else str(value or "")


def _style_workbook(wb: Workbook) -> None:
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2563EB")
        for column in ws.columns:
            letter = column[0].column_letter
            ws.column_dimensions[letter].width = min(48, max(12, max(len(str(c.value or "")) for c in column) + 2))
            for cell in column:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
