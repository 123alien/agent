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
                issue.final_status = "confirmed_issue"
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

        valid_issues, merged_findings = self._merge_related_anomaly_signals(valid_issues)
        findings.extend(merged_findings)

        return QualityReview(
            valid_issues=valid_issues,
            findings=findings,
            retry_agent=invalid_agents[0] if invalid_agents else "",
        )

    @staticmethod
    def _merge_related_anomaly_signals(
        issues: list[Issue],
    ) -> tuple[list[Issue], list[str]]:
        """Combine independent anomaly signals for the same entities.

        Compliance clauses and deterministic calculation errors remain separate;
        only anomaly clues with an identical entity/location key are consolidated.
        """
        mergeable_types = {
            "设备网络与文件元数据组合异常",
            "跨文件主体联系信息重合",
            "响应文件内容高度相似",
            "投标文件内容高度相似",
            "围串标风险线索",
        }
        groups: dict[str, list[Issue]] = {}
        untouched: list[Issue] = []
        for issue in issues:
            key = issue.source_location.strip()
            if issue.agent != "异常分析智能体" or issue.issue_type not in mergeable_types or not key:
                untouched.append(issue)
                continue
            groups.setdefault(key, []).append(issue)

        findings: list[str] = []
        for key, group in groups.items():
            if len(group) == 1:
                untouched.append(group[0])
                continue
            primary = group[0]
            primary.issue_type = "多信号组合异常"
            primary.risk_level = "高" if any(x.risk_level == "高" for x in group) or len(group) >= 3 else "中"
            primary.source_file = "、".join(dict.fromkeys(
                part.strip() for item in group for part in item.source_file.split("、") if part.strip()
            ))
            primary.description = f"{key}同时出现{len(group)}类相互独立的关联异常线索，需结合完整证据链人工复核。"
            primary.basis = "；".join(dict.fromkeys(item.basis for item in group if item.basis))
            primary.suggestion = "核查相关主体关系、文件编制来源、联系方式、提交环境及文本形成过程；这些线索不得单独用于认定串通投标。"
            primary.evidence = list(dict.fromkeys(
                evidence for item in group for evidence in item.evidence if evidence
            ))[:30]
            primary.evidence_refs = [
                ref for item in group for ref in item.evidence_refs
            ][:30]
            primary.requires_human_review = True
            primary.final_status = "human_review"
            primary.assessment = "待人工判断"
            primary.confidence = max(item.confidence for item in group)
            identity = "|".join([primary.agent, key, primary.issue_type, *primary.evidence])
            primary.issue_id = f"I{sha256(identity.encode('utf-8')).hexdigest()[:12]}"
            untouched.append(primary)
            findings.append(f"将{key}的{len(group)}类关联线索归并为多信号组合异常")
        return untouched, findings

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
