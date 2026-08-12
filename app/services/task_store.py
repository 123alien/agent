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
        path = self._path(task.task_id)
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(
            task.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def get(self, task_id: str) -> TaskRecord | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_recent(self, limit: int = 20) -> list[TaskRecord]:
        tasks: list[TaskRecord] = []
        paths = sorted(
            settings.tasks_dir.glob("T*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in paths[: max(1, min(limit, 100))]:
            try:
                tasks.append(TaskRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return tasks

    def load_review(self, task_id: str) -> dict:
        path = settings.reviews_dir / f"{task_id}.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("review", {})
        except (json.JSONDecodeError, OSError):
            return {}

    def save_review(self, task_id: str, payload: dict, status: str = "draft") -> Path:
        path = settings.reviews_dir / f"{task_id}.json"
        data = {
            "task_id": task_id,
            "updated_at": now_iso(),
            "status": status,
            "review": payload,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


task_store = TaskStore()

