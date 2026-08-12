import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from app.agents.compliance_checker import ComplianceCheckerAgent
from app.services.document_context import build_document_context
from app.schemas.task import DocumentSection, ExtractedField, ParsedDocument, RejectionRecord
from app.services.dify_client import DifyWorkflowError, dify_client
from app.services.workflow_cache import WorkflowResultCache


def parsed_document(file_id: str = "F001") -> ParsedDocument:
    return ParsedDocument(
        file_id=file_id,
        filename="测试招标文件.txt",
        file_type="招标文件",
        text_length=100,
    )


class ComplianceCheckerTests(unittest.TestCase):
    def test_evaluation_report_completeness_is_checked(self) -> None:
        report = ParsedDocument(
            file_id="REPORT-1",
            filename="评标报告.pdf",
            file_type="评标报告",
            document_subtype="评标报告",
            text_length=30,
            sections=[DocumentSection(title="项目基本信息", content="项目名称：测试项目")],
        )
        context = build_document_context(report, "项目名称：测试项目")
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent, "_run_locally", return_value=unittest.mock.Mock(data={}, issues=[], summary="")
        ):
            result = agent.run_contexts([context], [report])

        issue = next(item for item in result.issues if item.issue_type == "评标报告必需内容可能缺失")
        self.assertIn("评标委员会", issue.description)

    def test_numbered_recommendations_satisfy_candidate_section(self) -> None:
        report = ParsedDocument(
            file_id="R002", filename="评标报告.pdf", file_type="评标报告",
            document_subtype="评标报告", text_length=200,
        )
        text = """项目名称：测试项目
评标委员会名单
评审过程及资格审查
评审结果
第一推荐人：甲公司
第二推荐人：乙公司"""
        context = build_document_context(report, text)
        issues, _ = ComplianceCheckerAgent()._process_compliance_checks(
            [context], [report], {}
        )
        missing = [item for item in issues if item.issue_type == "评标报告必需内容可能缺失"]
        self.assertEqual(missing, [])

    def test_cross_document_project_name_conflict_is_reported(self) -> None:
        procurement = parsed_document("P-1")
        procurement.filename = "采购文件.pdf"
        procurement.extracted_fields["project_name"] = ExtractedField(
            value="甲项目", raw_text="项目名称：甲项目"
        )
        report = ParsedDocument(
            file_id="R-1", filename="评标报告.pdf", file_type="评标报告",
            document_subtype="评标报告", text_length=20,
            extracted_fields={"project_name": ExtractedField(value="乙项目", raw_text="项目名称：乙项目")},
        )
        contexts = [
            build_document_context(procurement, "项目名称：甲项目"),
            build_document_context(report, "项目名称：乙项目"),
        ]
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent, "_run_locally", return_value=unittest.mock.Mock(data={}, issues=[], summary="")
        ):
            result = agent.run_contexts(contexts, [procurement, report])

        issue = next(item for item in result.issues if item.issue_type == "跨文件基础信息不一致")
        self.assertIn("项目名称", issue.description)
        self.assertGreaterEqual(len(issue.evidence), 2)

    def test_rejection_reason_is_checked_against_procurement_document(self) -> None:
        procurement = parsed_document("P-2")
        procurement.filename = "采购文件.pdf"
        report = ParsedDocument(
            file_id="R-2", filename="评标报告.pdf", file_type="评标报告",
            document_subtype="评标报告", text_length=50,
            rejection_records=[RejectionRecord(
                bidder="乙公司",
                reason="未提交投标保证金",
                cited_clause="第二章第3.2条",
                evidence="乙公司因未提交投标保证金被否决投标。",
            )],
        )
        procurement_text = "第二章第3.2条要求投标人提交投标保证金。"
        report_text = "乙公司因未提交投标保证金被否决投标。"
        contexts = [
            build_document_context(procurement, procurement_text),
            build_document_context(report, report_text),
        ]
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent, "_run_locally", return_value=unittest.mock.Mock(data={}, issues=[], summary="")
        ):
            result = agent.run_contexts(contexts, [procurement, report])

        self.assertFalse(any(item.issue_type == "废标依据待核验" for item in result.issues))
        self.assertEqual(result.data["process_compliance"]["rejection_record_checks"], 1)

    def test_missing_procurement_document_requires_rejection_review(self) -> None:
        report = ParsedDocument(
            file_id="R-3", filename="评标报告.pdf", file_type="评标报告",
            document_subtype="评标报告", text_length=50,
            rejection_records=[RejectionRecord(
                bidder="乙公司", reason="未提交投标保证金",
                evidence="乙公司因未提交投标保证金被否决投标。",
            )],
        )
        context = build_document_context(report, "乙公司因未提交投标保证金被否决投标。")
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent, "_run_locally", return_value=unittest.mock.Mock(data={}, issues=[], summary="")
        ):
            result = agent.run_contexts([context], [report])

        issue = next(item for item in result.issues if item.issue_type == "废标依据待核验")
        self.assertIn("未同时提供采购文件", issue.description)

    def test_system_record_conflict_is_reported(self) -> None:
        report = ParsedDocument(
            file_id="R-SYSTEM", filename="评标报告.pdf", file_type="评标报告",
            document_subtype="评标报告", text_length=30,
            extracted_fields={
                "project_name": ExtractedField(
                    value="文件中的项目", raw_text="项目名称：文件中的项目"
                )
            },
        )
        context = build_document_context(report, "项目名称：文件中的项目")
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent, "_run_locally", return_value=unittest.mock.Mock(data={}, issues=[], summary="")
        ):
            result = agent.run_contexts(
                [context], [report], {"project_name": "系统登记项目"}
            )

        issue = next(item for item in result.issues if item.issue_type == "跨文件基础信息不一致")
        self.assertIn("业务系统记录=系统登记项目", issue.description)
        self.assertEqual(result.data["process_compliance"]["system_record_field_count"], 1)

    def test_known_and_unknown_legal_citations_are_distinguished(self) -> None:
        procurement = parsed_document("P-LAW")
        report = ParsedDocument(
            file_id="R-LAW", filename="评标报告.pdf", file_type="评标报告",
            document_subtype="评标报告", text_length=100,
            rejection_records=[
                RejectionRecord(
                    bidder="甲公司", reason="未提交投标保证金",
                    cited_clause="《中华人民共和国招标投标法》第三十三条",
                    evidence="甲公司依据《中华人民共和国招标投标法》第三十三条被否决。",
                ),
                RejectionRecord(
                    bidder="乙公司", reason="未提交投标保证金",
                    cited_clause="《中华人民共和国招标投标法》第九百九十九条",
                    evidence="乙公司依据《中华人民共和国招标投标法》第九百九十九条被否决。",
                ),
            ],
        )
        contexts = [
            build_document_context(
                procurement,
                "投标人应当提交投标保证金；未提交投标保证金的，投标无效。",
            ),
            build_document_context(report, "甲公司和乙公司的否决投标记录。"),
        ]
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent, "_run_locally", return_value=unittest.mock.Mock(data={}, issues=[], summary="")
        ):
            result = agent.run_contexts(contexts, [procurement, report])

        citation_issues = [
            item for item in result.issues if item.issue_type == "废标法规引用待核验"
        ]
        self.assertEqual(len(citation_issues), 1)
        self.assertIn("第九百九十九条", citation_issues[0].description)
        metrics = result.data["process_compliance"]
        self.assertEqual(metrics["legal_citation_checks"], 2)
        self.assertEqual(metrics["unsupported_legal_citation_count"], 1)

    def test_context_run_uses_persistent_workflow_cache(self) -> None:
        text = "投标人注册地址必须位于本市。"
        document = parsed_document()
        context = build_document_context(document, text)
        payload = {
            "summary": "发现一项问题",
            "issues": [
                {
                    "risk_level": "高",
                    "issue_type": "地域限制",
                    "description": "限制外地企业参与",
                    "evidence": text,
                    "requires_human_review": True,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = WorkflowResultCache(Path(directory) / "cache.sqlite", 60)
            with patch.object(dify_client, "enabled", True), patch(
                "app.agents.compliance_checker.workflow_result_cache", cache
            ), patch(
                "app.agents.compliance_checker.dify_client.run_document",
                return_value=payload,
            ) as run_document:
                first = ComplianceCheckerAgent().run_contexts([context], [document])
                second = ComplianceCheckerAgent().run_contexts([context], [document])

        self.assertEqual(run_document.call_count, 1)
        self.assertEqual(first.data["cache_misses"], 1)
        self.assertEqual(second.data["cache_hits"], 1)

    def test_shared_document_context_is_the_text_source(self) -> None:
        document = parsed_document()
        context = build_document_context(document, "统一上下文中的正文")
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", False), patch.object(
            agent,
            "_run_locally",
            return_value=unittest.mock.Mock(data={}),
        ) as run:
            result = agent.run_contexts([context], [document])

        run.assert_called_once_with(
            [document],
            {"F001": "统一上下文中的正文"},
        )
        self.assertEqual(result.data["input_contract"], "DocumentContext/1.0.0")

    def test_model_admitted_compliant_clause_is_discarded(self) -> None:
        agent = ComplianceCheckerAgent()
        document = parsed_document()
        payload = {
            "issues": [
                {
                    "risk_level": "低",
                    "issue_type": "程序合规性",
                    "description": "该条款与法律规定一致，形式上合规。",
                    "evidence": "供应商不足3家的，不予开启。",
                    "basis": "相关程序规定",
                    "suggestion": "无需处理。",
                }
            ]
        }

        issues = agent._issues_from_dify(
            document,
            payload,
            "供应商不足3家的，不予开启。",
        )

        self.assertEqual(issues, [])

    def test_dify_issue_is_normalized_and_evidence_is_verified(self) -> None:
        text = "投标人注册地址必须位于本市。"
        payload = {
            "summary": "发现一项问题",
            "issues": [
                {
                    "risk_level": "high",
                    "issue_type": "地域限制",
                    "description": "限制外地企业参与",
                    "evidence": text,
                    "basis": "公平竞争规定",
                    "suggestion": "删除地域限制",
                    "requires_human_review": "false",
                }
            ],
        }
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            return_value=payload,
        ):
            result = agent.run([parsed_document()], {"F001": text})

        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].risk_level, "高")
        self.assertTrue(result.issues[0].requires_human_review)
        self.assertEqual(result.issues[0].evidence, [text])
        self.assertEqual(result.issues[0].assessment, "待人工判断")
        self.assertEqual(result.issues[0].final_status, "human_review")

    def test_missing_high_attention_candidate_triggers_one_coverage_retry(self) -> None:
        geographic = "1. 投标人注册地址位于本市的得10分，外省企业不得分。"
        subjective = "2. 技术方案内容丰富的得20分，一般的得10分，较差的得2分。"
        text = geographic + "\n" + subjective
        first = {
            "summary": "发现一项问题",
            "issues": [{
                "risk_level": "高", "issue_type": "地域差别评分",
                "description": "按注册地评分", "evidence": geographic[3:],
                "basis": "公平竞争要求", "suggestion": "删除地域评分",
                "requires_human_review": True,
            }],
        }
        second = {
            "summary": "完成补审",
            "issues": [{
                "risk_level": "中", "issue_type": "主观评分",
                "description": "评分档次缺少客观指标", "evidence": subjective[3:],
                "basis": "需进一步量化", "suggestion": "细化评分指标",
                "requires_human_review": True,
            }],
        }
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            side_effect=[first, second],
        ) as mocked:
            result = agent.run([parsed_document()], {"F001": text})

        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(result.issues), 2)
        self.assertEqual(result.data["coverage_retry_count"], 1)
        self.assertEqual(result.data["uncovered_candidate_count"], 0)

    def test_candidate_still_missing_after_retry_is_sent_to_review(self) -> None:
        text = "技术方案内容丰富的得20分，一般的得10分，较差的得2分。"
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            side_effect=[{"issues": []}, {"issues": []}],
        ):
            result = agent.run([parsed_document()], {"F001": text})

        self.assertEqual(result.data["coverage_retry_count"], 1)
        self.assertEqual(result.data["uncovered_candidate_count"], 1)
        issue = next(
            item for item in result.issues
            if item.issue_type == "候选条款审查覆盖不足"
        )
        self.assertTrue(issue.requires_human_review)
        self.assertEqual(issue.evidence, [text])

    def test_coverage_ignores_self_check_and_blank_template_clauses(self) -> None:
        text = """2. 不得指定特定的专利、商标、品牌、供应商。
5. 不得设置注册资本、资产总额、营业收入等条件。
2. 乙方注册资本不低于【】。
本项目注册资本由特许经营者依法筹集。
费用为300万元。资金来源为特许经营者自筹资本金和融资资金，其中本项目注册资本为项目总投资的20%。
投标人注册资本不得低于5000万元。"""
        candidates = ComplianceCheckerAgent._coverage_candidates(text)
        self.assertEqual(
            [item["evidence"] for item in candidates],
            ["投标人注册资本不得低于5000万元。"],
        )

    def test_dify_issues_ignore_fair_competition_self_check_document(self) -> None:
        doc = parsed_document()
        doc.filename = "招标文件公平竞争审查自查表.pdf"
        text = "不得指定特定品牌或供应商。"
        payload = {"issues": [{
            "risk_level": "高", "issue_type": "指定品牌",
            "description": "疑似指定品牌", "evidence": text,
            "basis": "公平竞争要求", "suggestion": "删除",
            "requires_human_review": True,
        }]}
        self.assertEqual(ComplianceCheckerAgent()._issues_from_dify(doc, payload, text), [])

    def test_dify_issues_ignore_normal_control_price_rejection_rule(self) -> None:
        doc = parsed_document()
        text = "投标人报价超过控制价按废标处理。"
        payload = {"issues": [{
            "risk_level": "低", "issue_type": "废标条件设置",
            "description": "投标报价超过控制价按废标处理。", "evidence": text,
            "basis": "常见废标条件", "suggestion": "核对控制价",
            "requires_human_review": True,
        }]}
        self.assertEqual(ComplianceCheckerAgent()._issues_from_dify(doc, payload, text), [])

    def test_unverifiable_evidence_is_rejected(self) -> None:
        text = "投标人应具备履约能力。"
        payload = {
            "issues": [
                {
                    "risk_level": "高",
                    "issue_type": "指定品牌",
                    "description": "疑似指定品牌",
                    "evidence": "投标产品必须使用某品牌。",
                }
            ]
        }
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            return_value=payload,
        ):
            result = agent.run([parsed_document()], {"F001": text})

        self.assertEqual(result.issues, [])

    def test_long_document_chunks_are_deduplicated(self) -> None:
        evidence = "投标人注册地址必须位于本市。"
        text = ("普通条款。\n" * 1800) + evidence + ("\n其他条款。" * 1800)
        payload = {
            "issues": [
                {
                    "risk_level": "中",
                    "issue_type": "地域限制",
                    "description": "限制注册地址",
                    "evidence": evidence,
                    "requires_human_review": True,
                }
            ]
        }
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            return_value=payload,
        ) as mocked:
            result = agent.run([parsed_document()], {"F001": text})

        self.assertGreater(mocked.call_count, 1)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.data["successful_chunks"], mocked.call_count)

    def test_partial_chunk_failure_keeps_successful_results(self) -> None:
        evidence = "投标产品必须为指定品牌。"
        text = ("普通条款。\n" * 1800) + evidence + ("\n其他条款。" * 1800)
        success = {
            "issues": [
                {
                    "risk_level": "高",
                    "issue_type": "指定品牌",
                    "description": "指定品牌限制竞争",
                    "evidence": evidence,
                }
            ]
        }
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            side_effect=[DifyWorkflowError("分段失败"), success, success],
        ):
            result = agent.run([parsed_document()], {"F001": text})

        self.assertEqual(result.data["execution_mode"], "dify_partial")
        self.assertEqual(len(result.issues), 1)
        self.assertTrue(result.data["dify_errors"])

    def test_uncertain_legal_basis_requires_context_review(self) -> None:
        text = "本项目是否接受联合体：□是 ■否。"
        payload = {
            "issues": [
                {
                    "risk_level": "低",
                    "issue_type": "组织形式限制",
                    "description": "不接受联合体可能构成限制，需结合项目性质判断。",
                    "evidence": text,
                    "basis": "知识库检索依据不足，无法直接判断该条件是否合理。",
                    "suggestion": "需结合具体项目需求人工复核。",
                }
            ]
        }
        agent = ComplianceCheckerAgent()
        with patch.object(dify_client, "enabled", True), patch(
            "app.agents.compliance_checker.dify_client.run_document",
            return_value=payload,
        ):
            result = agent.run([parsed_document()], {"F001": text})

        issue = result.issues[0]
        self.assertEqual(issue.assessment, "待人工判断")
        self.assertEqual(issue.confidence, 0.55)
        self.assertTrue(issue.requires_human_review)
        self.assertEqual(result.data["needs_context_count"], 1)

    def test_compliant_bid_guarantee_is_resolved_with_parsed_budget(self) -> None:
        document = parsed_document()
        document.extracted_fields["budget"] = ExtractedField(
            value="145万元",
            raw_text="项目预算金额：145万元",
        )
        evidence = "投标保证金金额：2.9万元；"
        payload = {
            "issues": [
                {
                    "risk_level": "低",
                    "issue_type": "投标保证金比例异常",
                    "description": "无法判断投标保证金是否超过预算金额的2%。",
                    "evidence": evidence,
                    "basis": "投标保证金不得超过预算金额的2%。",
                    "suggestion": "核实预算金额。",
                }
            ]
        }

        issues = ComplianceCheckerAgent()._issues_from_dify(
            document,
            payload,
            evidence,
        )

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
