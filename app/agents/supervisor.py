from app.agents.anomaly_analyzer import AnomalyAnalyzerAgent
from app.agents.compliance_checker import ComplianceCheckerAgent
from app.agents.data_validator import DataValidatorAgent
from app.agents.document_parser import DocumentParserAgent
from app.agents.report_generator import ReportGeneratorAgent
from app.schemas.task import TaskRecord, TaskResult
from app.services.report_service import create_markdown_report


class SupervisorAgent:
    name = "总控调度智能体"

    def __init__(self) -> None:
        self.document_parser = DocumentParserAgent()
        self.compliance_checker = ComplianceCheckerAgent()
        self.data_validator = DataValidatorAgent()
        self.anomaly_analyzer = AnomalyAnalyzerAgent()
        self.report_generator = ReportGeneratorAgent()

    def run(self, task: TaskRecord) -> TaskResult:
        parsed_docs, document_result, raw_texts = self.document_parser.run(
            task.files,
            task.project_name,
        )
        compliance_result = self.compliance_checker.run(parsed_docs, raw_texts)
        data_result = self.data_validator.run(parsed_docs)
        anomaly_result = self.anomaly_analyzer.run(parsed_docs, raw_texts)

        issues = [
            *compliance_result.issues,
            *data_result.issues,
            *anomaly_result.issues,
        ]
        report_result = self.report_generator.run(parsed_docs, issues)
        agent_results = [
            document_result,
            compliance_result,
            data_result,
            anomaly_result,
            report_result,
        ]

        result = TaskResult(
            summary=report_result.summary,
            parsed_documents=parsed_docs,
            agent_results=agent_results,
            issues=issues,
        )
        create_markdown_report(task, result)
        result.report_url = f"/api/agent/tasks/{task.task_id}/report"
        return result


supervisor_agent = SupervisorAgent()

