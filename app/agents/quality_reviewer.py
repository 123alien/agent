from dataclasses import dataclass, field
from hashlib import sha256

from app.schemas.task import Issue


@dataclass
class QualityReview:
    valid_issues: list[Issue] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    retry_agent: str = ""


class QualityReviewerAgent:
    name = "结果复核智能体"

    def review(self, issues: list[Issue], raw_texts: dict[str, str]) -> QualityReview:
        valid_issues: list[Issue] = []
        findings: list[str] = []
        invalid_agents: list[str] = []
        seen: set[tuple[str, str, str, tuple[str, ...]]] = set()

        for issue in issues:
            if issue.requires_human_review:
                issue.assessment = "待人工判断"
                issue.final_status = "human_review"
            else:
                issue.assessment = "明确问题"
                issue.final_status = "confirmed"
            reason = self._invalid_reason(issue, raw_texts)
            fingerprint = (
                issue.source_file,
                issue.source_location,
                issue.issue_type,
                tuple(item.strip() for item in issue.evidence),
            )
            if fingerprint in seen:
                findings.append(f"去除重复问题: {issue.description}")
                continue
            seen.add(fingerprint)

            if reason:
                findings.append(f"{issue.agent}: {reason}")
                if issue.agent not in invalid_agents:
                    invalid_agents.append(issue.agent)
                continue
            if not issue.issue_id:
                identity = "|".join(
                    [
                        issue.agent,
                        issue.source_file,
                        issue.source_location,
                        issue.issue_type,
                        issue.description,
                        *issue.evidence,
                    ]
                )
                issue.issue_id = f"I{sha256(identity.encode('utf-8')).hexdigest()[:12]}"
            valid_issues.append(issue)

        return QualityReview(
            valid_issues=valid_issues,
            findings=findings,
            retry_agent=invalid_agents[0] if invalid_agents else "",
        )

    @staticmethod
    def _invalid_reason(issue: Issue, raw_texts: dict[str, str]) -> str:
        if not issue.description.strip():
            return "问题描述为空"
        if issue.risk_level == "高" and not issue.requires_human_review:
            return "高风险问题未标记人工复核"
        if issue.risk_level == "高" and not issue.evidence:
            return "高风险问题缺少原文证据"
        if issue.evidence:
            source_text = raw_texts.get(issue.source_file, "")
            if not source_text:
                source_text = "\n".join(raw_texts.values())
            missing = [item for item in issue.evidence if item and item not in source_text]
            if missing:
                return f"证据无法在原文中定位: {missing[0]}"
        return ""
