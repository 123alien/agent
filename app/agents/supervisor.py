import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agents.anomaly_analyzer import AnomalyAnalyzerAgent
from app.agents.compliance_checker import ComplianceCheckerAgent
from app.agents.data_validator import DataValidatorAgent
from app.agents.document_parser import DocumentParserAgent
from app.agents.quality_reviewer import QualityReviewerAgent
from app.agents.report_generator import ReportGeneratorAgent
from app.agents.routing_agent import RoutingAgent
from app.core.config import ensure_data_dirs, settings
from app.schemas.document_context import (
    ClauseGroups,
    ContextClause,
    ContextField,
    DocumentContext,
    DocumentEntities,
    DocumentQuality,
    SourceLocation,
)
from app.schemas.task import (
    AgentResult,
    DocumentQualityCheck,
    DocumentSection,
    DocumentTable,
    EvidenceRef,
    ExtractedField,
    Issue,
    ParsedDocument,
    TaskRecord,
    TaskResult,
)
from app.services.report_service import create_reports
from app.services.document_context import build_document_context
from app.services.evidence_locator import enrich_issue_evidence


AgentNode = Literal[
    "compliance", "data", "anomaly", "review", "human_review", "report"
]


@dataclass
class SupervisorRun:
    result: TaskResult | None = None
    review_request: dict | None = None


class AgentState(TypedDict):
    task: TaskRecord
    parsed_docs: list[ParsedDocument]
    raw_texts: dict[str, str]
    document_contexts: list[DocumentContext]
    pending_agents: list[AgentNode]
    agent_results: list[AgentResult]
    issues: list[Issue]
    execution_trace: list[str]
    retry_counts: dict[str, int]
    review_completed: bool
    human_review_completed: bool
    human_review: dict
    routing: dict
    result: TaskResult | None


class SupervisorAgent:
    name = "总控调度智能体"

    def __init__(self) -> None:
        self.document_parser = DocumentParserAgent()
        self.compliance_checker = ComplianceCheckerAgent()
        self.data_validator = DataValidatorAgent()
        self.anomaly_analyzer = AnomalyAnalyzerAgent()
        self.quality_reviewer = QualityReviewerAgent()
        self.routing_agent = RoutingAgent()
        self.report_generator = ReportGeneratorAgent()
        ensure_data_dirs()
        self._checkpoint_connection = sqlite3.connect(
            settings.graph_checkpoint_path,
            check_same_thread=False,
        )
        serializer = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("app.schemas.task", "TaskRecord"),
                ("app.schemas.task", "ParsedDocument"),
                ("app.schemas.task", "SourceLocation"),
                ("app.schemas.task", "ScoreDetail"),
                ("app.schemas.task", "ScoreSummary"),
                ("app.schemas.task", "OpeningRecord"),
                ("app.schemas.task", "RejectionRecord"),
                ("app.schemas.task", "EvaluationOpinion"),
                ("app.schemas.task", "CandidateRanking"),
                ("app.schemas.task", "SealSignatureCheck"),
                ("app.schemas.task", "DocumentSection"),
                ("app.schemas.task", "DocumentTable"),
                ("app.schemas.task", "LayoutElement"),
                ("app.schemas.task", "ExtractedField"),
                ("app.schemas.task", "DocumentQualityCheck"),
                ("app.schemas.task", "AgentResult"),
                ("app.schemas.task", "Issue"),
                ("app.schemas.task", "EvidenceRef"),
                ("app.schemas.document_context", "DocumentContext"),
                ("app.schemas.document_context", "DocumentQuality"),
                ("app.schemas.document_context", "DocumentEntities"),
                ("app.schemas.document_context", "ClauseGroups"),
                ("app.schemas.document_context", "ContextClause"),
                ("app.schemas.document_context", "ContextField"),
                ("app.schemas.document_context", "SourceLocation"),
            ]
        )
        self._checkpointer = SqliteSaver(
            self._checkpoint_connection,
            serde=serializer,
        )
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("document_parser", self._parse_documents)
        builder.add_node("supervisor", self._plan_or_continue)
        builder.add_node("compliance", self._run_compliance)
        builder.add_node("data", self._run_data_validation)
        builder.add_node("anomaly", self._run_anomaly_analysis)
        builder.add_node("review", self._review_results)
        builder.add_node("human_review", self._human_review)
        builder.add_node("report", self._generate_report)

        builder.add_edge(START, "document_parser")
        builder.add_edge("document_parser", "supervisor")
        builder.add_conditional_edges(
            "supervisor",
            self._route_next,
            {
                "compliance": "compliance",
                "data": "data",
                "anomaly": "anomaly",
                "review": "review",
                "human_review": "human_review",
                "report": "report",
            },
        )
        builder.add_edge("compliance", "supervisor")
        builder.add_edge("data", "supervisor")
        builder.add_edge("anomaly", "supervisor")
        builder.add_edge("review", "supervisor")
        builder.add_edge("human_review", "supervisor")
        builder.add_edge("report", END)
        return builder.compile(
            checkpointer=self._checkpointer,
            name="tender-review-supervisor",
        )

    def run(self, task: TaskRecord) -> SupervisorRun:
        output = self.graph.invoke(
            {
                "task": task,
                "parsed_docs": [],
                "raw_texts": {},
                "document_contexts": [],
                "pending_agents": [],
                "agent_results": [],
                "issues": [],
                "execution_trace": [],
                "retry_counts": {},
                "review_completed": False,
                "human_review_completed": False,
                "human_review": {},
                "routing": {},
                "result": None,
            },
            config=self._config(task.task_id),
        )
        return self._outcome(output)

    def resume(self, task_id: str, review: dict) -> SupervisorRun:
        output = self.graph.invoke(
            Command(resume=review),
            config=self._config(task_id),
        )
        return self._outcome(output)

    @staticmethod
    def _config(task_id: str) -> dict:
        return {"configurable": {"thread_id": task_id}}

    @staticmethod
    def _outcome(output: dict) -> SupervisorRun:
        interrupts = output.get("__interrupt__", [])
        if interrupts:
            return SupervisorRun(review_request=interrupts[0].value)
        result = output.get("result")
        if result is None:
            raise RuntimeError("LangGraph 未生成任务结果或中断请求")
        return SupervisorRun(result=result)

    def _parse_documents(self, state: AgentState) -> dict:
        task = state["task"]
        parsed_docs, document_result, raw_texts = self.document_parser.run(
            task.files,
            task.project_name,
        )
        file_paths = {file.file_id: file.saved_path for file in task.files}
        document_contexts = [
            build_document_context(
                document,
                raw_texts.get(document.file_id, ""),
                file_paths.get(document.file_id),
            )
            for document in parsed_docs
        ]
        document_result.data = {
            **document_result.data,
            "document_context_contract": "1.0.0",
            "document_context_count": len(document_contexts),
        }
        return {
            "parsed_docs": parsed_docs,
            "raw_texts": raw_texts,
            "document_contexts": document_contexts,
            "agent_results": [*state["agent_results"], document_result],
            "issues": [*state["issues"], *document_result.issues],
            "execution_trace": [*state["execution_trace"], "document_parser"],
        }

    def _plan_or_continue(self, state: AgentState) -> dict:
        if state["pending_agents"]:
            return {
                "execution_trace": [*state["execution_trace"], "supervisor:continue"]
            }

        if state["human_review_completed"]:
            return {
                "pending_agents": ["report"],
                "execution_trace": [*state["execution_trace"], "supervisor:report"],
            }

        if state["review_completed"]:
            needs_human_review = any(
                issue.risk_level == "高" or issue.requires_human_review
                for issue in state["issues"]
            )
            next_node: AgentNode = "human_review" if needs_human_review else "report"
            return {
                "pending_agents": [next_node],
                "execution_trace": [
                    *state["execution_trace"],
                    f"supervisor:{next_node}",
                ],
            }

        completed = {result.agent for result in state["agent_results"]}
        specialist_names = {
            self.compliance_checker.name,
            self.data_validator.name,
            self.anomaly_analyzer.name,
        }
        if completed & specialist_names:
            return {
                "pending_agents": ["review"],
                "execution_trace": [*state["execution_trace"], "supervisor:review"],
            }

        check_type = state["task"].check_type.strip().lower()
        plans: dict[str, list[AgentNode]] = {
            "compliance": ["compliance"],
            "data": ["data"],
            "anomaly": ["anomaly"],
            "full": ["compliance", "data", "anomaly"],
        }
        if check_type == "auto":
            decision = self.routing_agent.plan(state["parsed_docs"], state["raw_texts"])
            plan = [node for node in decision.selected_agents if node in plans["full"]]
            routing = {
                "mode": "auto",
                "selected_agents": plan,
                "reasons": decision.reasons,
            }
        else:
            effective_type = check_type if check_type in plans else "full"
            plan = plans[effective_type]
            routing = {
                "mode": "explicit",
                "requested_check_type": check_type or "full",
                "selected_agents": plan,
                "reasons": [f"调用方指定核验类型: {effective_type}"],
            }
        return {
            "pending_agents": plan,
            "routing": routing,
            "execution_trace": [
                *state["execution_trace"],
                f"supervisor:plan:{check_type or 'full'}",
            ],
        }

    @staticmethod
    def _route_next(state: AgentState) -> AgentNode:
        return state["pending_agents"][0] if state["pending_agents"] else "report"

    def _run_compliance(self, state: AgentState) -> dict:
        result = self.compliance_checker.run_contexts(
            state["document_contexts"],
            state["parsed_docs"],
            state["task"].system_record,
        )
        self._enrich_result_evidence(result, state["document_contexts"])
        return {
            "pending_agents": state["pending_agents"][1:],
            "agent_results": [*state["agent_results"], result],
            "issues": [*state["issues"], *result.issues],
            "execution_trace": [*state["execution_trace"], "compliance"],
        }

    def _run_data_validation(self, state: AgentState) -> dict:
        result = self.data_validator.run_contexts(
            state["document_contexts"],
            state["parsed_docs"],
        )
        self._enrich_result_evidence(result, state["document_contexts"])
        return {
            "pending_agents": state["pending_agents"][1:],
            "agent_results": [*state["agent_results"], result],
            "issues": [*state["issues"], *result.issues],
            "execution_trace": [*state["execution_trace"], "data"],
        }

    def _run_anomaly_analysis(self, state: AgentState) -> dict:
        result = self.anomaly_analyzer.run_contexts(
            state["document_contexts"],
            state["parsed_docs"],
            state["agent_results"],
        )
        self._enrich_result_evidence(result, state["document_contexts"])
        return {
            "pending_agents": state["pending_agents"][1:],
            "agent_results": [*state["agent_results"], result],
            "issues": [*state["issues"], *result.issues],
            "execution_trace": [*state["execution_trace"], "anomaly"],
        }

    @staticmethod
    def _enrich_result_evidence(
        result: AgentResult,
        contexts: list[DocumentContext],
    ) -> None:
        for issue in result.issues:
            enrich_issue_evidence(issue, contexts)

    def _review_results(self, state: AgentState) -> dict:
        source_texts = {
            doc.filename: state["raw_texts"].get(doc.file_id, "")
            for doc in state["parsed_docs"]
        }
        review = self.quality_reviewer.review(state["issues"], source_texts)
        retry_node = self._agent_node(review.retry_agent)
        retry_counts = dict(state["retry_counts"])
        should_retry = bool(retry_node and retry_counts.get(retry_node, 0) < 1)

        review_result = AgentResult(
            agent=self.quality_reviewer.name,
            summary=(
                f"结果复核完成，保留 {len(review.valid_issues)} 项问题，"
                f"发现 {len(review.findings)} 项质量问题。"
            ),
            data={
                "findings": review.findings,
                "retry_agent": review.retry_agent if should_retry else "",
            },
        )
        agent_results = [*state["agent_results"], review_result]
        if should_retry and review.retry_agent:
            agent_results = [
                result
                for result in agent_results
                if result.agent != review.retry_agent or result is review_result
            ]
        pending_agents: list[AgentNode] = []
        review_completed = True

        if should_retry and retry_node:
            retry_counts[retry_node] = retry_counts.get(retry_node, 0) + 1
            pending_agents = [retry_node, "review"]
            review_completed = False

        return {
            "pending_agents": pending_agents,
            "agent_results": agent_results,
            "issues": review.valid_issues,
            "execution_trace": [*state["execution_trace"], "review"],
            "retry_counts": retry_counts,
            "review_completed": review_completed,
        }

    def _human_review(self, state: AgentState) -> dict:
        review_issues = [
            issue.model_dump()
            for issue in state["issues"]
            if issue.risk_level == "高" or issue.requires_human_review
        ]
        response = interrupt(
            {
                "task_id": state["task"].task_id,
                "instruction": "请确认高风险或低置信度问题，可选择正确、误判或需修改。",
                "issues": review_issues,
            }
        )
        review_items = {
            item.get("issue_id"): item
            for item in response.get("items", [])
            if isinstance(item, dict) and item.get("issue_id")
        }
        reviewed_issues: list[Issue] = []
        normal_clauses: list[dict] = []
        for issue in state["issues"]:
            item = review_items.get(issue.issue_id)
            if not item or item.get("decision") == "正确":
                if item and item.get("decision") == "正确":
                    issue.assessment = "明确问题"
                    issue.final_status = "confirmed"
                    issue.confidence = 1.0
                    issue.requires_human_review = False
                reviewed_issues.append(issue)
                continue
            if item.get("decision") == "误判":
                normal_clauses.append(
                    {
                        "issue_id": issue.issue_id,
                        "evidence": issue.evidence,
                        "description": issue.description,
                        "review_comment": item.get("comment", ""),
                    }
                )
                continue
            if item.get("decision") == "需修改":
                corrected_text = str(item.get("corrected_text", "")).strip()
                if corrected_text:
                    issue.description = corrected_text
                issue.assessment = "明确问题"
                issue.final_status = "confirmed"
                issue.confidence = 1.0
                issue.requires_human_review = False
                reviewed_issues.append(issue)
                continue
            reviewed_issues.append(issue)

        missed_items = [
            item
            for item in response.get("items", [])
            if isinstance(item, dict)
            and item.get("decision") == "漏判"
            and not item.get("issue_id")
        ]
        for item in missed_items:
            description = str(
                item.get("corrected_text") or item.get("comment") or "人工复核补充问题"
            ).strip()
            identity = f"人工复核节点|人工补充|{description}"
            reviewed_issues.append(
                Issue(
                    issue_id=f"I{sha256(identity.encode('utf-8')).hexdigest()[:12]}",
                    agent="人工复核节点",
                    risk_level="中",
                    issue_type="人工补充",
                    description=description,
                    basis="人工复核发现自动审查存在漏判。",
                    suggestion="请结合原文证据进一步核实并完善审查结论。",
                    requires_human_review=False,
                    assessment="明确问题",
                    confidence=1.0,
                )
            )

        review_result = AgentResult(
            agent="人工复核节点",
            summary=(
                f"人工复核完成，复核前 {len(state['issues'])} 项，"
                f"复核后保留 {len(reviewed_issues)} 项。"
            ),
            data={
                "reviewer": response.get("reviewer", ""),
                "normal_clauses": normal_clauses,
                "normal_clause_count": len(normal_clauses),
                "missed_issue_count": len(missed_items),
            },
        )
        return {
            "pending_agents": [],
            "agent_results": [*state["agent_results"], review_result],
            "issues": reviewed_issues,
            "execution_trace": [*state["execution_trace"], "human_review"],
            "human_review": response,
            "human_review_completed": True,
        }

    def _agent_node(self, agent_name: str) -> AgentNode | None:
        return {
            self.compliance_checker.name: "compliance",
            self.data_validator.name: "data",
            self.anomaly_analyzer.name: "anomaly",
        }.get(agent_name)

    def _generate_report(self, state: AgentState) -> dict:
        task = state["task"]
        report_result = self.report_generator.run(
            state["parsed_docs"],
            state["issues"],
            task=task,
            agent_results=state["agent_results"],
            human_review=state["human_review"],
            output_type=task.output_type,
            template_type=task.template_type,
        )
        report_result.data["execution_trace"] = [*state["execution_trace"], "report"]
        result = TaskResult(
            summary=report_result.summary,
            routing=state["routing"],
            parsed_documents=state["parsed_docs"],
            agent_results=[*state["agent_results"], report_result],
            issues=state["issues"],
        )
        create_reports(task, result)
        result.report_url = f"/api/agent/tasks/{task.task_id}/report"
        result.report_files = {
            "markdown": result.report_url,
            "docx": f"/api/agent/tasks/{task.task_id}/report.docx",
            "pdf": f"/api/agent/tasks/{task.task_id}/report.pdf",
        }
        return {
            "pending_agents": [],
            "agent_results": [*state["agent_results"], report_result],
            "execution_trace": [*state["execution_trace"], "report"],
            "result": result,
        }


supervisor_agent = SupervisorAgent()
