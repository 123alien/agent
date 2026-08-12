from __future__ import annotations

from collections import Counter

from app.schemas.task import ParsedDocument


DOCUMENT_ROLE_LABELS = {
    "procurement_document": "采购/招标文件",
    "bid_response": "投标/响应文件",
    "opening_record": "开标和报价资料",
    "evaluation_standard": "评审标准/评分办法",
    "expert_score": "专家评分资料",
    "evaluation_summary": "评审结果汇总资料",
    "evaluation_report": "评标报告",
    "transaction_metadata": "电子交易平台元数据",
    "other": "其他资料",
}

CORE_FULL_REVIEW_ROLES = (
    "procurement_document", "bid_response", "opening_record",
    "evaluation_standard", "expert_score", "evaluation_summary",
    "evaluation_report",
)


def classify_document_role(filename: str, file_type: str = "", subtype: str = "") -> str:
    text = f"{filename} {file_type} {subtype}".lower()
    rules = (
        ("transaction_metadata", ("电子交易", "交易元数据", "上传ip", "机器码", "mac地址", "加密锁")),
        ("evaluation_standard", ("评分办法", "评审标准", "评分标准", "评标办法")),
        ("expert_score", ("专家评分", "评分明细", "评分表")),
        ("evaluation_summary", ("评审汇总", "评分汇总", "评标结果汇总", "中标候选人", "候选人推荐")),
        ("opening_record", ("开标记录", "报价汇总", "报价表", "报价明细")),
        ("evaluation_report", ("评标报告",)),
        ("bid_response", ("响应文件", "投标文件", "响应资料")),
        ("procurement_document", ("采购文件", "招标文件", "磋商文件", "谈判文件")),
    )
    for role, markers in rules:
        if any(marker in text for marker in markers):
            return role
    return "other"


def build_material_inventory(documents: list[ParsedDocument], check_type: str = "auto") -> dict:
    received = []
    for document in documents:
        role = document.document_role
        if role == "other":
            role = classify_document_role(document.filename, document.file_type, document.document_subtype)
        received.append({
            "document_id": document.file_id,
            "filename": document.filename,
            "document_role": role,
            "document_role_label": DOCUMENT_ROLE_LABELS[role],
            "parse_status": document.parse_status,
        })
    counts = Counter(item["document_role"] for item in received)
    required_roles = CORE_FULL_REVIEW_ROLES if check_type in {"auto", "full"} else ()
    not_identified = [{
        "document_role": role,
        "document_role_label": DOCUMENT_ROLE_LABELS[role],
        "status": "not_identified",
        "message": "当前上传材料中未识别到该类资料，可能是未上传或分类未命中，需人工确认。",
    } for role in required_roles if counts[role] == 0]
    return {
        "check_type": check_type,
        "received_documents": received,
        "role_counts": dict(counts),
        "not_identified_required_documents": not_identified,
        "unclassified_documents": [item for item in received if item["document_role"] == "other"],
        "requires_human_review": bool(not_identified or counts["other"]),
        "note": "未识别到仅表示当前材料或分类结果中未发现，不能据此直接认定资料缺失。",
    }
