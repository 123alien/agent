from app.schemas.task import AgentResult, Issue, ParsedDocument


class ReportGeneratorAgent:
    name = "报告生成智能体"

    def run(self, parsed_docs: list[ParsedDocument], issues: list[Issue]) -> AgentResult:
        high = sum(1 for issue in issues if issue.risk_level == "高")
        medium = sum(1 for issue in issues if issue.risk_level == "中")
        low = sum(1 for issue in issues if issue.risk_level == "低")
        summary = (
            f"已汇总 {len(parsed_docs)} 份文档的核验结果，共发现 {len(issues)} 项问题，"
            f"其中高风险 {high} 项、中风险 {medium} 项、低风险 {low} 项。"
        )
        return AgentResult(
            agent=self.name,
            summary=summary,
            data={"high": high, "medium": medium, "low": low},
        )

