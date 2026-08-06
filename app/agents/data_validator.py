from app.schemas.task import AgentResult, Issue, ParsedDocument


class DataValidatorAgent:
    name = "数据核验智能体"

    def run(self, parsed_docs: list[ParsedDocument]) -> AgentResult:
        issues: list[Issue] = []

        for doc in parsed_docs:
            if not doc.project_name:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="项目名称未识别",
                        source_file=doc.filename,
                        description="文档解析结果中未识别到项目名称。",
                        basis="核验任务需要项目名称作为跨文件一致性比对依据。",
                        suggestion="建议人工补录项目名称，或检查文件首页和封面解析效果。",
                    )
                )

            if "投标" in doc.file_type and not doc.bid_prices:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="投标报价未识别",
                        source_file=doc.filename,
                        description="投标文件中未识别到报价金额。",
                        basis="报价是投标文件数据核验的关键字段。",
                        suggestion="建议检查报价表、开标一览表或 PDF 表格解析结果。",
                    )
                )

            if "招标" in doc.file_type and not doc.scoring_criteria:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="低",
                        issue_type="评分标准未识别",
                        source_file=doc.filename,
                        description="招标文件中未识别到明显评分标准或评标办法。",
                        basis="评标办法是 AI 评标和专家评分核验的基础。",
                        suggestion="建议人工确认评分办法是否在附件或独立文件中。",
                    )
                )

        summary = f"数据核验完成，发现 {len(issues)} 项数据完整性或一致性问题。"
        return AgentResult(agent=self.name, summary=summary, issues=issues)

