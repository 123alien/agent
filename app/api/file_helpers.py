def safe_storage_name(filename: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in filename).strip()
    return cleaned or "uploaded_file"


def infer_file_type(filename: str) -> str:
    name = filename.lower()
    if "招标" in filename:
        return "招标文件"
    if "投标" in filename:
        return "投标文件"
    if "评标" in filename or "评分" in filename:
        return "评标报告"
    if "合同" in filename:
        return "合同文件"
    if "规则" in filename or "制度" in filename or "法规" in filename:
        return "规则法规文件"
    if name.endswith((".pdf", ".doc", ".docx", ".txt", ".md")):
        return "业务文件"
    return "未知文件"
