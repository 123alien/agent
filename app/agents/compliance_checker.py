from app.agents.utils import find_lines
from app.schemas.task import AgentResult, Issue, ParsedDocument
from app.services.llm_client import llm_client


class ComplianceCheckerAgent:
    name = "合规审查智能体"

    def run(self, parsed_docs: list[ParsedDocument], raw_texts: dict[str, str]) -> AgentResult:
        issues: list[Issue] = []
        risky_keywords = ["唯一", "指定品牌", "指定厂家", "排他", "本地企业", "特定供应商"]
        missing_keywords = ["资格", "评分", "投标保证金", "评标办法"]

        for doc in parsed_docs:
            text = raw_texts.get(doc.file_id, "")
            risky_lines = find_lines(text, risky_keywords, limit=5)
            for line in risky_lines:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="高",
                        issue_type="疑似限制性或排他性条款",
                        source_file=doc.filename,
                        description=f"发现可能影响公平竞争的表述: {line}",
                        basis="招投标文件通常不得设置不合理限制或排他性条件。",
                        suggestion="建议人工核验该条款是否具有合理业务依据，必要时调整表述。",
                        evidence=[line],
                    )
                )

            missing = [keyword for keyword in missing_keywords if keyword not in text]
            if missing:
                issues.append(
                    Issue(
                        agent=self.name,
                        risk_level="中",
                        issue_type="关键审查要素可能缺失",
                        source_file=doc.filename,
                        description=f"文件中未明显识别到这些要素: {'、'.join(missing)}。",
                        basis="招投标文件应包含资格、评分、保证金、评标办法等关键内容。",
                        suggestion="建议人工复核文件目录和附件，确认是否存在缺项或解析遗漏。",
                    )
                )

            issues.extend(self._llm_check(doc, text))

        summary = f"合规审查完成，发现 {len(issues)} 项待复核问题。"
        return AgentResult(agent=self.name, summary=summary, issues=issues)

    def _llm_check(self, doc: ParsedDocument, text: str) -> list[Issue]:
        if not llm_client.enabled or not text.strip():
            return []

        system_prompt = (
            "你是招投标合规审查智能体。请只输出 JSON，格式为 "
            '{"issues":[{"risk_level":"高/中/低","issue_type":"","source_location":"",'
            '"description":"","basis":"","suggestion":"","evidence":[""]}]}。'
            "如果没有明确问题，输出 {\"issues\":[]}。"
        )
        user_prompt = (
            f"文件名: {doc.filename}\n"
            "请审查下列招投标文件片段，重点识别限制性条款、排他性条款、关键要素缺失、"
            "评分办法不清晰、资格要求不合理等问题。\n\n"
            f"{text[:12000]}"
        )
        try:
            payload = llm_client.chat_json(system_prompt, user_prompt)
        except Exception:
            return []

        llm_issues: list[Issue] = []
        for item in (payload or {}).get("issues", []):
            try:
                llm_issues.append(
                    Issue(
                        agent=self.name,
                        risk_level=item.get("risk_level", "中"),
                        issue_type=item.get("issue_type", "大模型合规审查问题"),
                        source_file=doc.filename,
                        source_location=item.get("source_location", ""),
                        description=item.get("description", ""),
                        basis=item.get("basis", ""),
                        suggestion=item.get("suggestion", ""),
                        evidence=item.get("evidence", []),
                    )
                )
            except Exception:
                continue
        return llm_issues
