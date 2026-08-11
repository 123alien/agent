import shutil
import json
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.agents.supervisor import supervisor_agent
from app.api.file_helpers import infer_file_type, safe_storage_name
from app.core.config import ensure_data_dirs, settings
from app.schemas.task import (
    CreateUrlTaskRequest,
    ReviewRequest,
    TaskRecord,
    UploadedFileInfo,
)
from app.schemas.contract import ContractGenerationRequest, ContractGenerationResult
from app.services.contract_service import create_contract_docx
from app.services.dify_client import DifyWorkflowError, dify_client
from app.services.pdf_service import create_contract_pdf
from app.services.callback_service import send_task_callback
from app.services.remote_files import RemoteFileError, download_remote_file
from app.services.file_parser import document_tool_registry
from app.services.task_store import task_store
from app.services.time_utils import now_iso

router = APIRouter()


@router.post("/contracts/generate", response_model=ContractGenerationResult)
def generate_contract(request: ContractGenerationRequest) -> ContractGenerationResult:
    contract_id = f"C-{uuid.uuid4().hex[:12]}"
    contract_number = request.contract_number or contract_id
    validation_items = supervisor_agent.report_generator.validate_contract(request)
    if any(item.level == "error" for item in validation_items):
        return ContractGenerationResult(
            contract_id=contract_id,
            contract_number=contract_number,
            template_type=request.template_type,
            status="failed",
            validation_items=validation_items,
            requires_human_review=True,
        )
    clauses: dict[str, list[str]] = {}
    execution_mode = "local"
    dify_errors: list[str] = []
    if request.use_dify and dify_client.report_generator_enabled:
        try:
            payload = dify_client.run_contract_drafter(
                json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                user=request.source_task_id or contract_id,
            )
            allowed = {
                "service_level_terms",
                "data_security_terms",
                "intellectual_property_terms",
                "change_management_terms",
                "termination_terms",
                "force_majeure_terms",
            }
            raw_clauses = payload.get("supplementary_clauses", {})
            if isinstance(raw_clauses, dict):
                for key, value in raw_clauses.items():
                    if key in allowed and isinstance(value, list):
                        cleaned = [str(item).strip() for item in value if str(item).strip()]
                        if cleaned:
                            clauses[key] = cleaned[:8]
            execution_mode = "dify"
        except DifyWorkflowError as exc:
            execution_mode = "local_fallback"
            dify_errors.append(str(exc))
    create_contract_docx(
        contract_id,
        contract_number,
        request,
        validation_items,
        supplementary_clauses=clauses,
    )
    create_contract_pdf(
        contract_id,
        contract_number,
        request,
        validation_items,
        supplementary_clauses=clauses,
    )
    return ContractGenerationResult(
        contract_id=contract_id,
        contract_number=contract_number,
        template_type=request.template_type,
        status="review_required",
        validation_items=validation_items,
        requires_human_review=True,
        document_url=f"/api/agent/contracts/{contract_id}/document",
        pdf_url=f"/api/agent/contracts/{contract_id}/document.pdf",
        execution_mode=execution_mode,
        dify_errors=dify_errors,
    )


@router.get("/contracts/{contract_id}/document")
def download_contract(contract_id: str) -> FileResponse:
    if not contract_id.startswith("C-") or not contract_id[2:].isalnum():
        raise HTTPException(status_code=400, detail="合同编号格式不正确")
    path = settings.contracts_dir / f"{contract_id}.docx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="合同文档不存在")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{contract_id}_合同草案.docx",
    )


@router.get("/contracts/{contract_id}/document.pdf")
def download_contract_pdf(contract_id: str) -> FileResponse:
    if not contract_id.startswith("C-") or not contract_id[2:].isalnum():
        raise HTTPException(status_code=400, detail="合同编号格式不正确")
    path = settings.contracts_dir / f"{contract_id}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="合同 PDF 不存在")
    return FileResponse(path, media_type="application/pdf", filename=f"{contract_id}_合同草案.pdf")


@router.get("/document-tools")
def list_document_tools() -> dict[str, list[dict[str, object]]]:
    return {"tools": document_tool_registry.capabilities()}


def build_task(
    task_id: str,
    project_id: str,
    project_name: str,
    check_type: str,
    files: list[UploadedFileInfo],
    callback_url: str = "",
    system_record: dict | None = None,
    output_type: str = "综合智能核验报告",
    template_type: str = "标准审查报告",
) -> TaskRecord:
    timestamp = now_iso()
    return TaskRecord(
        task_id=task_id,
        project_id=project_id,
        project_name=project_name,
        check_type=check_type,
        status="pending",
        files=files,
        callback_url=callback_url,
        system_record=system_record or {},
        output_type=output_type,
        template_type=template_type,
        callback_status="pending" if callback_url else "not_configured",
        created_at=timestamp,
        updated_at=timestamp,
    )


def run_task(task_id: str) -> None:
    task = task_store.get(task_id)
    if task is None:
        return
    try:
        task.status = "running"
        task_store.save(task)
        outcome = supervisor_agent.run(task)
        if outcome.review_request:
            task.status = "waiting_review"
            task.review_request = outcome.review_request
        else:
            task.result = outcome.result
            task.status = "completed"
        task_store.save(task)
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)
        task_store.save(task)
    if task.status != "waiting_review":
        send_callback(task)


def resume_task(task_id: str, review: dict) -> None:
    task = task_store.get(task_id)
    if task is None:
        return
    try:
        outcome = supervisor_agent.resume(task_id, review)
        if outcome.review_request:
            task.status = "waiting_review"
            task.review_request = outcome.review_request
        else:
            task.result = outcome.result
            task.status = "completed"
            task.review_request = {}
        task_store.save(task)
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)
        task_store.save(task)
    if task.status != "waiting_review":
        send_callback(task)


def send_callback(task: TaskRecord) -> None:
    if task.callback_url:
        try:
            send_task_callback(task)
            task.callback_status = "sent"
            task.callback_error = ""
        except Exception as exc:
            task.callback_status = "failed"
            task.callback_error = str(exc)
        task_store.save(task)


@router.post("/tasks", response_model=TaskRecord)
async def create_task(
    background_tasks: BackgroundTasks,
    project_id: Annotated[str, Form()],
    project_name: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    check_type: Annotated[str, Form()] = "auto",
    callback_url: Annotated[str, Form()] = "",
    system_record: Annotated[str, Form()] = "{}",
    output_type: Annotated[str, Form()] = "综合智能核验报告",
    template_type: Annotated[str, Form()] = "标准审查报告",
) -> TaskRecord:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个待核验文件")

    ensure_data_dirs()
    task_id = f"T{uuid.uuid4().hex[:12]}"
    task_upload_dir = settings.uploads_dir / task_id
    task_upload_dir.mkdir(parents=True, exist_ok=True)

    uploaded_files: list[UploadedFileInfo] = []
    for upload in files:
        if not upload.filename:
            continue
        file_id = f"F{uuid.uuid4().hex[:10]}"
        display_name = Path(upload.filename).name
        storage_name = safe_storage_name(display_name)
        saved_path = task_upload_dir / f"{file_id}_{storage_name}"
        with saved_path.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        uploaded_files.append(
            UploadedFileInfo(
                file_id=file_id,
                filename=display_name,
                file_type=infer_file_type(display_name),
                saved_path=str(saved_path),
            )
        )

    if not uploaded_files:
        raise HTTPException(status_code=400, detail="上传文件无效")

    try:
        parsed_system_record = json.loads(system_record or "{}")
        if not isinstance(parsed_system_record, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="system_record 必须是 JSON 对象") from exc

    task = build_task(
        task_id,
        project_id,
        project_name,
        check_type,
        uploaded_files,
        callback_url,
        parsed_system_record,
        output_type,
        template_type,
    )
    task_store.save(task)
    background_tasks.add_task(run_task, task_id)
    return task


@router.get("/tasks", response_model=list[TaskRecord])
def list_tasks(limit: int = 20) -> list[TaskRecord]:
    return task_store.list_recent(limit)


@router.post("/tasks/from-urls", response_model=TaskRecord)
async def create_task_from_urls(
    request: CreateUrlTaskRequest,
    background_tasks: BackgroundTasks,
) -> TaskRecord:
    if not request.files:
        raise HTTPException(status_code=400, detail="请至少提供一个待核验文件地址")

    ensure_data_dirs()
    task_id = f"T{uuid.uuid4().hex[:12]}"
    task_upload_dir = settings.uploads_dir / task_id
    task_upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        uploaded_files = [
            await download_remote_file(item, task_upload_dir) for item in request.files
        ]
    except RemoteFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = build_task(
        task_id,
        request.project_id,
        request.project_name,
        request.check_type,
        uploaded_files,
        request.callback_url,
        request.system_record,
        request.output_type,
        request.template_type,
    )
    task_store.save(task)
    background_tasks.add_task(run_task, task_id)
    return task


@router.get("/tasks/{task_id}", response_model=TaskRecord)
def get_task(task_id: str) -> TaskRecord:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/tasks/{task_id}/report")
def download_report(task_id: str) -> FileResponse:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    report_path = settings.reports_dir / f"{task_id}.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return FileResponse(
        report_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{task_id}_智能核验报告.md",
    )


@router.get("/tasks/{task_id}/report.docx")
def download_docx_report(task_id: str) -> FileResponse:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    report_path = settings.reports_dir / f"{task_id}.docx"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Word 报告尚未生成")
    return FileResponse(
        report_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{task_id}_智能核验报告.docx",
    )


@router.get("/tasks/{task_id}/report.pdf")
def download_pdf_report(task_id: str) -> FileResponse:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    report_path = settings.reports_dir / f"{task_id}.pdf"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="PDF 报告尚未生成")
    return FileResponse(report_path, media_type="application/pdf", filename=f"{task_id}_智能核验报告.pdf")


@router.post("/tasks/{task_id}/review")
def submit_review(
    task_id: str,
    review: ReviewRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "waiting_review":
        raise HTTPException(status_code=409, detail="任务当前不处于人工复核状态")

    merged = _merge_review(task, task_store.load_review(task_id), review)
    progress = _review_progress(task, merged)
    task.review_progress = progress

    if not review.submit:
        path = task_store.save_review(task_id, merged, status="draft")
        task_store.save(task)
        return {"status": "draft_saved", "review_path": str(path), **progress}

    if progress["remaining"]:
        task_store.save_review(task_id, merged, status="draft")
        task_store.save(task)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "仍有未处理的复核条目，请完成后再正式提交",
                **progress,
            },
        )

    merged["submit"] = True
    path = task_store.save_review(task_id, merged, status="submitted")
    task.status = "running"
    task.review_request = {}
    task_store.save(task)
    background_tasks.add_task(resume_task, task_id, merged)
    return {"status": "accepted", "review_path": str(path), **progress}


@router.get("/tasks/{task_id}/review")
def get_review(task_id: str) -> dict:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    review = task_store.load_review(task_id)
    return {"review": review, "progress": _review_progress(task, review)}


def _merge_review(task: TaskRecord, saved: dict, incoming: ReviewRequest) -> dict:
    items_by_id = {
        item.get("issue_id"): item
        for item in saved.get("items", [])
        if isinstance(item, dict) and item.get("issue_id")
    }
    extra_items = [
        item
        for item in saved.get("items", [])
        if isinstance(item, dict) and not item.get("issue_id")
    ]
    for item in incoming.items:
        value = item.model_dump()
        if item.issue_id:
            items_by_id[item.issue_id] = value
        else:
            extra_items.append(value)

    if incoming.batch_decision:
        available_ids = {
            str(item.get("issue_id"))
            for item in task.review_request.get("issues", [])
            if item.get("issue_id")
        }
        targets = set(incoming.batch_issue_ids) if incoming.batch_issue_ids else available_ids
        for issue_id in targets & available_ids:
            items_by_id[issue_id] = {
                "issue_id": issue_id,
                "decision": incoming.batch_decision,
                "comment": incoming.comment,
                "corrected_text": "",
            }

    return {
        "reviewer": incoming.reviewer or saved.get("reviewer", ""),
        "comment": incoming.comment or saved.get("comment", ""),
        "items": [*items_by_id.values(), *extra_items],
        "submit": False,
    }


def _review_progress(task: TaskRecord, review: dict) -> dict:
    required_ids = [
        str(item.get("issue_id"))
        for item in task.review_request.get("issues", [])
        if item.get("issue_id")
    ]
    reviewed_ids = {
        str(item.get("issue_id"))
        for item in review.get("items", [])
        if isinstance(item, dict) and item.get("issue_id") and item.get("decision")
    }
    missing_ids = [issue_id for issue_id in required_ids if issue_id not in reviewed_ids]
    return {
        "total": len(required_ids),
        "reviewed": len(required_ids) - len(missing_ids),
        "remaining": len(missing_ids),
        "missing_issue_ids": missing_ids,
    }
