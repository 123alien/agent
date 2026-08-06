from pathlib import Path

from app.core.config import ensure_data_dirs, settings
from app.schemas.task import TaskRecord, TaskResult


def create_markdown_report(task: TaskRecord, result: TaskResult) -> Path:
    ensure_data_dirs()
    report_path = settings.reports_dir / f"{task.task_id}.md"

    lines: list[str] = [
        f"# {task.project_name} 智能核验报告",
        "",
        f"- 任务编号: {task.task_id}",
        f"- 项目编号: {task.project_id}",
        f"- 核验类型: {task.check_type}",
        f"- 核验结论: {result.summary}",
        "",
        "## 一、文档解析结果",
        "",
    ]

    for doc in result.parsed_documents:
        lines.extend(
            [
                f"### {doc.filename}",
                "",
                f"- 文件类型: {doc.file_type}",
                f"- 文本长度: {doc.text_length}",
                f"- 项目名称: {doc.project_name or '未识别'}",
                f"- 招标人: {doc.tenderer or '未识别'}",
                f"- 投标人: {', '.join(doc.bidders) if doc.bidders else '未识别'}",
                f"- 报价: {', '.join(doc.bid_prices) if doc.bid_prices else '未识别'}",
                "",
            ]
        )

    lines.extend(["## 二、风险问题清单", ""])
    if not result.issues:
        lines.append("未发现明确风险问题。")
    for index, issue in enumerate(result.issues, start=1):
        lines.extend(
            [
                f"### 问题 {index}: {issue.issue_type}",
                "",
                f"- 来源智能体: {issue.agent}",
                f"- 风险等级: {issue.risk_level}",
                f"- 来源文件: {issue.source_file or '未定位'}",
                f"- 位置: {issue.source_location or '未定位'}",
                f"- 问题描述: {issue.description}",
                f"- 依据: {issue.basis or '待人工补充'}",
                f"- 建议: {issue.suggestion or '待人工确认'}",
                "",
            ]
        )

    lines.extend(["## 三、专项智能体结论", ""])
    for agent_result in result.agent_results:
        lines.extend([f"### {agent_result.agent}", "", agent_result.summary, ""])

    lines.extend(
        [
            "## 四、人工复核意见",
            "",
            "待人工复核后补充。人工复核结果可通过接口回传，用于后续规则和模型优化。",
            "",
        ]
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path

