from app.agents.utils import find_lines, guess_project_name, money_values, unique_keep_order
from app.schemas.task import AgentResult, ParsedDocument, UploadedFileInfo
from app.services.file_parser import extract_text


class DocumentParserAgent:
    name = "文档解析智能体"

    def run(
        self,
        files: list[UploadedFileInfo],
        project_name: str,
    ) -> tuple[list[ParsedDocument], AgentResult, dict[str, str]]:
        parsed_docs: list[ParsedDocument] = []
        raw_texts: dict[str, str] = {}

        for file_info in files:
            text = extract_text(file_info.saved_path)
            raw_texts[file_info.file_id] = text
            bidders = find_lines(text, ["投标人", "供应商", "中标候选人"], limit=6)
            parsed_docs.append(
                ParsedDocument(
                    file_id=file_info.file_id,
                    filename=file_info.filename,
                    file_type=file_info.file_type,
                    text_length=len(text),
                    project_name=guess_project_name(text, project_name),
                    tenderer=(find_lines(text, ["招标人", "采购人"], limit=1) or [""])[0],
                    bidders=unique_keep_order(bidders),
                    bid_prices=money_values(text),
                    qualification_requirements=find_lines(text, ["资格", "资质", "业绩", "证书"], limit=8),
                    scoring_criteria=find_lines(text, ["评分", "分值", "评审", "评标办法"], limit=8),
                    key_clauses=find_lines(text, ["不得", "必须", "应当", "须", "合同", "保证金"], limit=12),
                )
            )

        summary = f"已解析 {len(parsed_docs)} 个文件，提取项目、主体、报价、资质、评分和关键条款等结构化信息。"
        return parsed_docs, AgentResult(agent=self.name, summary=summary), raw_texts

