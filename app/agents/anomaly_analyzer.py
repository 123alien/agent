import re

from app.schemas.task import AgentResult, Issue, ParsedDocument


class AnomalyAnalyzerAgent:
    name = "异常分析智能体"

    def run(self, parsed_docs: list[ParsedDocument], raw_texts: dict[str, str]) -> AgentResult:
        issues: list[Issue] = []

        for doc in parsed_docs:
            text = raw_texts.get(doc.file_id, "")
            score_values = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*分", text)]
            high_scores = [v for v in score_values if v > 100]
            if high_scores:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="评分数值异常",
                        source_file=doc.filename,
                        description=f"识别到超过 100 分的评分值: {high_scores[:5]}。",
                        basis="常见评分总分通常为 100 分，超过阈值需确认是否为解析误差或特殊评分制。",
                        suggestion="建议人工核验评分表和评分总分规则。",
                    )
                )

            suspicious_terms = ["串通", "陪标", "围标", "关联关系", "异常一致", "雷同"]
            evidence = [term for term in suspicious_terms if term in text]
            if evidence:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="高",
                        issue_type="围串标风险线索",
                        source_file=doc.filename,
                        description=f"文件中出现围串标相关风险表述: {'、'.join(evidence)}。",
                        basis="围串标风险通常需要结合投标行为、文件相似度、报价规律和主体关系综合判断。",
                        suggestion="建议进一步调用相似度比对、供应商关系分析和报价分布分析工具。",
                        evidence=evidence,
                    )
                )

        summary = f"异常分析完成，发现 {len(issues)} 项异常或风险线索。"
        return AgentResult(agent=self.name, summary=summary, issues=issues)

