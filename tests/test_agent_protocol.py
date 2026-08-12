import unittest

from pydantic import ValidationError

from app.schemas.agent_protocol import (
    AgentError,
    AgentFinding,
    AgentRequest,
    finding_from_issue,
    response_from_agent_result,
)
from app.schemas.task import AgentResult, EvidenceRef, Issue


class AgentProtocolTests(unittest.TestCase):
    def test_request_is_frozen_and_rejects_unknown_fields(self) -> None:
        request = AgentRequest(request_id="REQ-1", project_id="P-1")
        self.assertEqual(request.contract_version, "1.0.0")
        with self.assertRaises(ValidationError):
            AgentRequest(request_id="REQ-1", project_id="P-1", unknown=True)

    def test_visual_uncertainty_cannot_be_confirmed_directly(self) -> None:
        with self.assertRaises(ValidationError):
            AgentFinding(
                finding_id="F-1", final_status="confirmed_issue", risk_level="低",
                finding_type="印章核验", description="未检测到印章",
                detection_status="not_detected", requires_human_review=False,
            )

    def test_issue_maps_to_traceable_public_finding(self) -> None:
        issue = Issue(
            issue_id="I-1", agent="文档解析智能体", risk_level="中",
            issue_type="印章主体不一致", description="印章主体需复核",
            evidence_refs=[EvidenceRef(
                document_id="DOC-1", quote="投标人（盖章）：", page=12,
                section="授权书", source_type="derived",
            )],
            detection_status="mismatch", confidence=0.91,
        )
        finding = finding_from_issue(issue)
        self.assertEqual(finding.final_status, "human_review")
        self.assertTrue(finding.requires_human_review)
        self.assertEqual(finding.evidence[0].source_type, "visual")
        self.assertEqual(finding.evidence[0].page, 12)

    def test_agent_result_uses_uniform_response_envelope(self) -> None:
        issue = Issue(
            issue_id="I-2", agent="异常分析智能体", risk_level="高",
            issue_type="多信号异常", description="检测到关联线索",
            evidence=["B/C机器码一致"], requires_human_review=True,
        )
        response = response_from_agent_result(
            request_id="REQ-2",
            agent_result=AgentResult(agent="异常分析智能体", summary="发现1项线索", issues=[issue]),
        )
        self.assertEqual(response.agent, "anomaly_analysis")
        self.assertEqual(response.status, "completed")
        self.assertEqual(response.findings[0].final_status, "human_review")

    def test_error_codes_are_enumerated(self) -> None:
        error = AgentError(
            code="AGENT_WORKFLOW_TIMEOUT", message="语义服务超时", retryable=True,
            stage="compliance_review", trace_id="TRACE-1",
        )
        self.assertTrue(error.retryable)


if __name__ == "__main__":
    unittest.main()
