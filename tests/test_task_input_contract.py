import unittest
from unittest.mock import Mock

from app.agents.supervisor import SupervisorAgent
from app.api.tasks import build_task
from app.schemas.task import AgentResult, ParsedDocument, UploadedFileInfo


class TaskInputContractTests(unittest.TestCase):
    def test_build_task_persists_relationship_data(self) -> None:
        task = build_task(
            "T-REL", "P-REL", "关系数据测试", "full",
            [UploadedFileInfo(
                file_id="F-1", filename="电子交易元数据.xlsx",
                file_type="业务文件", saved_path="data/F-1.xlsx",
            )],
            relationship_data={"network_features": [{"supplier_id": "S001", "ip": "203.0.113.10"}]},
        )
        self.assertEqual(task.relationship_data["network_features"][0]["supplier_id"], "S001")

    def test_supervisor_passes_task_relationship_data_to_anomaly_agent(self) -> None:
        supervisor = SupervisorAgent.__new__(SupervisorAgent)
        supervisor.anomaly_analyzer = Mock()
        supervisor.anomaly_analyzer.run_contexts.return_value = AgentResult(
            agent="异常分析智能体", summary="完成"
        )
        task = build_task("T-2", "P-2", "测试", "full", [], relationship_data={"contacts": []})
        state = {
            "task": task, "document_contexts": [], "parsed_docs": [],
            "agent_results": [], "pending_agents": ["anomaly"],
            "issues": [], "execution_trace": [],
        }
        supervisor._run_anomaly_analysis(state)
        self.assertEqual(
            supervisor.anomaly_analyzer.run_contexts.call_args.kwargs["relationship_data"],
            {"contacts": []},
        )


if __name__ == "__main__":
    unittest.main()
