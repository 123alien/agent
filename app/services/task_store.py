import json
from pathlib import Path

from app.core.config import ensure_data_dirs, settings
from app.schemas.task import TaskRecord
from app.services.time_utils import now_iso


class TaskStore:
    def __init__(self) -> None:
        ensure_data_dirs()

    def _path(self, task_id: str) -> Path:
        return settings.tasks_dir / f"{task_id}.json"

    def save(self, task: TaskRecord) -> None:
        task.updated_at = now_iso()
        self._path(task.task_id).write_text(
            task.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def get(self, task_id: str) -> TaskRecord | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save_review(self, task_id: str, payload: dict) -> Path:
        path = settings.reviews_dir / f"{task_id}.json"
        data = {"task_id": task_id, "created_at": now_iso(), "review": payload}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


task_store = TaskStore()

