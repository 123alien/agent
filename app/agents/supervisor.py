import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Literal, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agents.anomaly_analyzer import AnomalyAnalyzerAgent
from app.agents.compliance_checker import ComplianceCheckerAgent
from app.agents.data_validator import DataValidatorAgent
from app.agents.document_parser import DocumentParserAgent
from app.services.project_index import project_index_service
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
from app.services.material_inventory import build_material_inventory


AgentNode = Literal[
    "compliance", "data", "anomaly", "review", "human_review", "report"
]

ProgressCallback = Callable[[dict], None]
_progress_callback: ContextVar[ProgressCallback | None] = ContextVar(
    "supervisor_progress_callback", default=None
)


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

    def run(
        self, task: TaskRecord, progress_callback: ProgressCallback | None = None
    ) -> SupervisorRun:
        token = _progress_callback.set(progress_callback)
        try:
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
        finally:
            _progress_callback.reset(token)

    def resume(
        self, task_id: str, review: dict,
        progress_callback: ProgressCallback | None = None,
    ) -> SupervisorRun:
        token = _progress_callback.set(progress_callback)
        try:
            output = self.graph.invoke(
                Command(resume=review), config=self._config(task_id)
            )
            return self._outcome(output)
        finally:
            _progress_callback.reset(token)

    @staticmethod
    def _emit(
        *, agent: str, node: str, status: str, goal: str,
        tools: list[str] | None = None, finding: str = "",
        decision: str = "", review_reason: str = "",
    ) -> None:
        callback = _progress_callback.get()
        if callback:
            callback({
                "agent": agent, "node": node, "status": status,
                "goal": goal, "tools": tools or [], "finding": finding,
                "decision": decision, "review_reason": review_reason,
            })

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
        self._emit(
            agent="文档解析智能体", node="document_parser", status="running",
            goal="解析文件类型、正文、章节、表格、关键字段与视觉核验线索",
            tools=["文件类型识别", "版面与表格解析", "OCR", "Dify语义增强（按配置启用）"],
        )
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
        project_index = project_index_service.build(
            task.task_id,
            [chunk for document in parsed_docs for chunk in document.evidence_chunks],
        )
        document_result.data = {
            **document_result.data,
            "document_context_contract": "1.0.0",
            "document_context_count": len(document_contexts),
            "project_index": project_index,
        }
        tools = list(dict.fromkeys(
            tool for document in parsed_docs
            for tool in ([document.selected_tool] + document.tool_trace)
            if tool
        ))
        self._emit(
            agent="文档解析智能体", node="document_parser", status="completed",
            goal="形成供后续智能体复用的统一结构化文档数据",
            tools=tools[:12],
            finding=f"已解析{len(parsed_docs)}份文件，提取{sum(len(x.sections) for x in parsed_docs)}个章节、{sum(len(x.tables) for x in parsed_docs)}个表格，形成{len(document_result.issues)}项问题线索。",
            decision=f"已建立含{project_index['chunk_count']}条证据切片的项目临时索引；进入总控路由。",
        )
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
            self._emit(
                agent=self.name, node="supervisor", status="completed",
                goal="根据人工复核结果继续执行",
                decision="人工复核已完成，进入报告生成智能体。",
            )
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
            self._emit(
                agent=self.name, node="supervisor", status="completed",
                goal="根据结果复核结论决定是否需要人工介入",
                finding=f"统一复核后保留{len(state['issues'])}项问题。",
                decision="进入人工复核。" if needs_human_review else "无需人工复核，直接生成报告。",
                review_reason="存在高风险、低置信度或明确标记需人工确认的事项。" if needs_human_review else "",
            )
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
        display = {"compliance": "合规审查智能体", "data": "数据核验智能体", "anomaly": "异常分析智能体"}
        self._emit(
            agent=self.name, node="supervisor", status="completed",
            goal="拆解任务并选择需要执行的专项智能体",
            tools=["LangGraph条件路由", "任务类型识别"],
            finding="；".join(routing.get("reasons", [])),
            decision=" → ".join(display.get(node, node) for node in plan) or "直接生成报告",
        )
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
        self._emit(
            agent=self.compliance_checker.name, node="compliance", status="running",
            goal="核验限制性条款、程序完整性、废标依据和法规引用",
            tools=["确定性合规规则", "Dify工作流", "法规RAG知识库"],
        )
        result = self.compliance_checker.run_contexts(
            state["document_contexts"],
            state["parsed_docs"],
            state["task"].system_record,
        )
        self._enrich_result_evidence(result, state["document_contexts"])
        self._emit(
            agent=result.agent, node="compliance", status="completed",
            goal="输出有原文证据和依据的合规问题",
            tools=[str(result.data.get("execution_mode", "本地规则"))],
            finding=result.summary, decision="将合规发现交给统一结果复核。",
        )
        return {
            "pending_agents": state["pending_agents"][1:],
            "agent_results": [*state["agent_results"], result],
            "issues": [*state["issues"], *result.issues],
            "execution_trace": [*state["execution_trace"], "compliance"],
        }

    def _run_data_validation(self, state: AgentState) -> dict:
        self._emit(
            agent=self.data_validator.name, node="data", status="running",
            goal="复算报价、得分、权重、排名并核对跨文档字段一致性",
            tools=["确定性计算引擎", "跨文档字段比对", "Dify语义补充"],
        )
        result = self.data_validator.run_contexts(
            state["document_contexts"],
            state["parsed_docs"],
        )
        self._enrich_result_evidence(result, state["document_contexts"])
        self._emit(
            agent=result.agent, node="data", status="completed",
            goal="输出可复算、可定位的数据问题",
            tools=[str(result.data.get("execution_mode", "本地规则"))],
            finding=result.summary, decision="将数据发现交给统一结果复核。",
        )
        return {
            "pending_agents": state["pending_agents"][1:],
            "agent_results": [*state["agent_results"], result],
            "issues": [*state["issues"], *result.issues],
            "execution_trace": [*state["execution_trace"], "data"],
        }

    def _run_anomaly_analysis(self, state: AgentState) -> dict:
        self._emit(
            agent=self.anomaly_analyzer.name, node="anomaly", status="running",
            goal="综合主体、设备、网络、报价和文本信号识别关联异常",
            tools=["多信号组合规则", "关系数据分析", "Dify语义分析"],
        )
        result = self.anomaly_analyzer.run_contexts(
            state["document_contexts"],
            state["parsed_docs"],
            state["agent_results"],
            relationship_data=state["task"].relationship_data,
        )
        self._enrich_result_evidence(result, state["document_contexts"])
        self._emit(
            agent=result.agent, node="anomaly", status="completed",
            goal="形成异常线索而非直接作出违法认定",
            tools=[str(result.data.get("execution_mode", "本地规则"))],
            finding=result.summary, decision="将组合异常交给统一结果复核。",
        )
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
        self._emit(
            agent=self.quality_reviewer.name, node="review", status="running",
            goal="核验证据、去重并检查各智能体结论质量",
            tools=["证据完整性检查", "重复问题归并", "结果质量规则"],
        )
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

        self._emit(
            agent=self.quality_reviewer.name, node="review", status="completed",
            goal="形成统一问题口径并决定重试或人工复核",
            finding=review_result.summary,
            decision=(f"退回{review.retry_agent}重试。" if should_retry else "进入总控决策。"),
        )

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
        self._emit(
            agent="人工复核节点", node="human_review", status="waiting_review",
            goal="由评审人员确认、排除或修正AI候选问题",
            tools=["原文证据链", "人工复核表单"],
            finding=f"共有{len(review_issues)}项需要人工确认。",
            decision="暂停LangGraph，等待人工提交复核结论。",
            review_reason="问题涉及高风险、证据解释或低置信度判断，系统不得自动作最终认定。",
        )
        response = interrupt(
            {
                "task_id": state["task"].task_id,
                "instruction": "请确认高风险或低置信度问题，可选择正确、误判或需修改。",
                "issues": review_issues,
                "parsed_documents": [document.model_dump(mode="json") for document in state["parsed_docs"]],
                "agent_results": [result.model_dump(mode="json") for result in state["agent_results"]],
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
                    issue.final_status = "confirmed_issue"
                    issue.confidence = 1.0
                    issue.requires_human_review = False
                reviewed_issues.append(issue)
                continue
            if item.get("decision") == "误判":
                normal_clauses.append(
                    {
                        "issue_id": issue.issue_id,
                        "final_status": "passed",
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
                issue.final_status = "confirmed_issue"
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
                    final_status="confirmed_issue",
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
        self._emit(
            agent="人工复核节点", node="human_review", status="completed",
            goal="保存人工判断并形成最终三态结论",
            finding=review_result.summary,
            decision="进入报告生成智能体。",
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
        self._emit(
            agent=self.report_generator.name, node="report", status="running",
            goal="汇总最终问题、证据和人工复核结论，生成标准化报告",
            tools=["报告内容规划", "Word生成", "PDF生成"],
        )
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
            material_inventory=build_material_inventory(
                state["parsed_docs"], task.check_type
            ),
        )
        create_reports(task, result)
        result.report_url = f"/api/agent/tasks/{task.task_id}/report"
        result.report_files = {
            "markdown": result.report_url,
            "docx": f"/api/agent/tasks/{task.task_id}/report.docx",
            "pdf": f"/api/agent/tasks/{task.task_id}/report.pdf",
        }
        self._emit(
            agent=self.report_generator.name, node="report", status="completed",
            goal="生成可交付、可追溯的核验报告",
            finding=report_result.summary,
            decision="任务完成，可下载Word、PDF和Markdown报告。",
        )
        return {
            "pending_agents": [],
            "agent_results": [*state["agent_results"], report_result],
            "execution_trace": [*state["execution_trace"], "report"],
            "result": result,
        }


supervisor_agent = SupervisorAgent()
