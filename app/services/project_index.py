from __future__ import annotations

import json
import math
import re
from collections import Counter
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

    def search(
        self,
        task_id: str,
        query: str,
        limit: int = 8,
        document_id: str = "",
        content_type: str = "",
        page: int | None = None,
    ) -> list[dict]:
        path = self.root / f"{task_id}.json"
        if not path.exists() or not query.strip():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        query_terms = _terms(query)
        chunks = [
            chunk for chunk in payload.get("chunks", [])
            if (not document_id or chunk.get("document_id") == document_id)
            and (not content_type or chunk.get("content_type") == content_type)
            and (page is None or chunk.get("page") == page)
        ]
        if not chunks:
            return []
        document_terms = [_terms(str(chunk.get("content", ""))) for chunk in chunks]
        document_frequency = Counter(
            term for terms in document_terms for term in terms
        )
        total_documents = len(chunks)
        ranked: list[tuple[float, dict]] = []
        normalized_query = re.sub(r"\s+", "", query.lower())
        for chunk, content_terms in zip(chunks, document_terms):
            overlap = query_terms & content_terms
            if not overlap:
                continue
            if len(query_terms) >= 3 and len(overlap) / len(query_terms) < 0.3:
                continue
            # IDF weighted coverage makes rare legal/business terms rank above
            # boilerplate. Exact phrase, section name and high quality evidence
            # receive small deterministic boosts.
            weighted_hit = sum(
                math.log((total_documents + 1) / (document_frequency[term] + 0.5)) + 1
                for term in overlap
            )
            weighted_query = sum(
                math.log((total_documents + 1) / (document_frequency.get(term, 0) + 0.5)) + 1
                for term in query_terms
            )
            score = weighted_hit / max(1.0, weighted_query)
            content = re.sub(r"\s+", "", str(chunk.get("content", "")).lower())
            section = re.sub(r"\s+", "", str(chunk.get("section", "")).lower())
            if normalized_query and normalized_query in content:
                score += 0.25
            if normalized_query and normalized_query in section:
                score += 0.12
            score += 0.05 * float(chunk.get("confidence", 0.0))
            if chunk.get("requires_human_review"):
                score -= 0.02
            score = min(1.0, max(0.0, score))
            ranked.append((score, {**chunk, "score": round(score, 4)}))
        ranked.sort(key=lambda item: (-item[0], item[1].get("chunk_id", "")))
        return [item for _, item in ranked[:max(1, min(limit, 50))]]


project_index_service = ProjectIndexService()
