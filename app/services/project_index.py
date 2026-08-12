from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.config import settings
from app.schemas.task import EvidenceChunk


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    chinese = {normalized[i:i + 2] for i in range(max(0, len(normalized) - 1))}
    words = set(re.findall(r"[a-z0-9_.-]{2,}", text.lower()))
    return chinese | words


class ProjectIndexService:
    """Task-scoped, rebuildable evidence index; no project data leaks across tasks."""

    @property
    def root(self) -> Path:
        path = settings.data_dir / "project_indexes"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def build(self, task_id: str, chunks: list[EvidenceChunk]) -> dict:
        payload = {
            "task_id": task_id,
            "index_type": "temporary_lexical_v1",
            "chunk_count": len(chunks),
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        }
        (self.root / f"{task_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return {key: payload[key] for key in ("task_id", "index_type", "chunk_count")}

    def search(self, task_id: str, query: str, limit: int = 8) -> list[dict]:
        path = self.root / f"{task_id}.json"
        if not path.exists() or not query.strip():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        query_terms = _terms(query)
        ranked: list[tuple[float, dict]] = []
        for chunk in payload.get("chunks", []):
            content_terms = _terms(str(chunk.get("content", "")))
            overlap = query_terms & content_terms
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_terms))
            ranked.append((score, {**chunk, "score": round(score, 4)}))
        ranked.sort(key=lambda item: (-item[0], item[1].get("chunk_id", "")))
        return [item for _, item in ranked[:max(1, min(limit, 50))]]


project_index_service = ProjectIndexService()
