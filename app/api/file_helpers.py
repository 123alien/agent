def safe_storage_name(filename: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in filename).strip()
    return cleaned or "uploaded_file"


def infer_file_type(filename: str) -> str:
    name = filename.lower()
    if "招标" in filename or "采购文件" in filename or "磋商文件" in filename:
        return "招标文件"
    if "投标" in filename:
        return "投标文件"
    if any(key in filename for key in ("开标记录", "报价表")):
        return "开标记录"
    if any(key in filename for key in ("评分表", "评分汇总", "评审汇总")):
        return "评审评分表"
    if "评标" in filename or "评分" in filename:
        return "评标报告"
    if "合同" in filename:
        return "合同文件"
    if "规则" in filename or "制度" in filename or "法规" in filename:
        return "规则法规文件"
    if name.endswith((".pdf", ".doc", ".docx", ".txt", ".md", ".xlsx")):
        return "业务文件"
    return "未知文件"


def infer_document_subtype(filename: str, file_type: str = "") -> str:
    rules = (
        (("资格审查",), "资格审查表"),
        (("符合性审查",), "符合性审查表"),
        (("废标", "否决投标", "无效投标"), "废标说明"),
        (("评分汇总", "评审汇总"), "评分汇总表"),
        (("专家评分",), "专家评分表"),
        (("开标记录",), "开标记录表"),
        (("报价表", "报价明细"), "报价表"),
        (("中标候选人", "候选人推荐"), "中标候选人推荐表"),
        (("评审意见", "评标意见"), "评审意见"),
        (("评标报告",), "评标报告"),
        (("投标", "响应文件"), "响应文件"),
        (("招标", "采购文件", "磋商文件"), "采购文件"),
    )
    for keywords, subtype in rules:
        if any(keyword in filename for keyword in keywords):
            return subtype
    return file_type or "其他资料"
