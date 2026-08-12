import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.agents.document_parser import DocumentParserAgent
from app.api.file_helpers import infer_file_type, safe_storage_name
from app.core.config import ensure_data_dirs, settings
from app.schemas.agent_protocol import (
    AgentError,
    AgentExecution,
    AgentRequest,
    AgentResponse,
    response_from_agent_result,
)
from app.schemas.task import UploadedFileInfo


router = APIRouter()


def _error_response(
    *, request_id: str, code: str, message: str, status_code: int,
    retryable: bool = False, details: dict | None = None,
) -> JSONResponse:
    payload = AgentResponse(
        request_id=request_id,
        agent="document_parser",
        status="failed",
        summary=message,
        errors=[AgentError(
            code=code,
            message=message,
            retryable=retryable,
            stage="document_parser",
            details=details or {},
            trace_id=request_id,
        )],
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


@router.post(
    "/document-parser",
    response_model=AgentResponse,
    summary="Run the standalone document parser agent",
)
async def run_document_parser(
    request: Annotated[
        str,
        Form(description="Serialized AgentRequest v1 JSON; input.project_name is required"),
    ],
    files: Annotated[list[UploadFile], File(description="PDF, DOCX, TXT, Markdown or XLSX files")],
) -> AgentResponse | JSONResponse:
    request_id = f"REQ-{uuid.uuid4().hex[:12]}"
    try:
        envelope = AgentRequest.model_validate_json(request)
        request_id = envelope.request_id
    except ValidationError as exc:
        return _error_response(
            request_id=request_id,
            code="INVALID_REQUEST",
            message="request 必须是符合 AgentRequest v1 协议的 JSON",
            status_code=422,
            details={"validation_errors": exc.errors(include_url=False, include_input=False)},
        )

    project_name = str(envelope.input.get("project_name", "")).strip()
    if not project_name:
        return _error_response(
            request_id=request_id,
            code="INVALID_REQUEST",
            message="input.project_name 不能为空",
            status_code=422,
        )
    if not files:
        return _error_response(
            request_id=request_id,
            code="INVALID_REQUEST",
            message="请至少上传一个待解析文件",
            status_code=400,
        )

    ensure_data_dirs()
    upload_dir = settings.uploads_dir / "standalone" / request_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    uploaded_files: list[UploadedFileInfo] = []
    try:
        for upload in files:
            if not upload.filename:
                continue
            file_id = f"F{uuid.uuid4().hex[:10]}"
            display_name = Path(upload.filename).name
            saved_path = upload_dir / f"{file_id}_{safe_storage_name(display_name)}"
            with saved_path.open("wb") as target:
                shutil.copyfileobj(upload.file, target)
            uploaded_files.append(UploadedFileInfo(
                file_id=file_id,
                filename=display_name,
                file_type=infer_file_type(display_name),
                saved_path=str(saved_path),
            ))
    except OSError as exc:
        return _error_response(
            request_id=request_id,
            code="FILE_PARSE_FAILED",
            message="上传文件保存失败",
            status_code=500,
            retryable=True,
            details={"error_type": type(exc).__name__},
        )

    if not uploaded_files:
        return _error_response(
            request_id=request_id,
            code="INVALID_REQUEST",
            message="上传文件无效",
            status_code=400,
        )

    started_at = time.perf_counter()
    try:
        documents, agent_result, _ = DocumentParserAgent().run(
            uploaded_files,
            project_name,
            enable_semantic_enhancement=envelope.options.enable_dify,
        )
    except Exception as exc:
        return _error_response(
            request_id=request_id,
            code="FILE_PARSE_FAILED",
            message="文档解析执行失败",
            status_code=500,
            details={"error_type": type(exc).__name__},
        )

    warning_items = list(dict.fromkeys(
        warning for document in documents for warning in document.warnings
    ))
    return response_from_agent_result(
        request_id=request_id,
        agent_result=agent_result,
        result={
            **agent_result.data,
            "project_id": envelope.project_id,
            "task_id": envelope.task_id,
            "documents": [document.model_dump(mode="json") for document in documents],
        },
        warnings=warning_items,
        execution=AgentExecution(
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            model="Dify文档解析Workflow" if (
                envelope.options.enable_dify and settings.dify_document_parser_api_key
            ) else "deterministic-parser",
            workflow_version="1.0.0",
            ruleset_version=settings.ruleset_version,
            execution_mode="standalone_api",
        ),
    )
