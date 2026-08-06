import shutil
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
from app.services.callback_service import send_task_callback
from app.services.remote_files import RemoteFileError, download_remote_file
from app.services.task_store import task_store
from app.services.time_utils import now_iso

router = APIRouter()


def build_task(
    task_id: str,
    project_id: str,
    project_name: str,
    check_type: str,
    files: list[UploadedFileInfo],
    callback_url: str = "",
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
        task.result = supervisor_agent.run(task)
        task.status = "completed"
        task_store.save(task)
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)
        task_store.save(task)
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
    check_type: Annotated[str, Form()] = "full",
    callback_url: Annotated[str, Form()] = "",
    files: Annotated[list[UploadFile], File()] = [],
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

    task = build_task(
        task_id,
        project_id,
        project_name,
        check_type,
        uploaded_files,
        callback_url,
    )
    task_store.save(task)
    background_tasks.add_task(run_task, task_id)
    return task


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


@router.post("/tasks/{task_id}/review")
def submit_review(task_id: str, review: ReviewRequest) -> dict[str, str]:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    path = task_store.save_review(task_id, review.model_dump())
    return {"status": "ok", "review_path": str(path)}
