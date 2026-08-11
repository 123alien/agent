from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import ensure_data_dirs, settings


class WorkflowResultCache:
    """Persistent, thread-safe cache for deterministic workflow inputs."""

    def __init__(self, path: Path | None = None, ttl_seconds: int | None = None) -> None:
        ensure_data_dirs()
        self.path = path or settings.workflow_cache_path
        self.ttl_seconds = (
            settings.rag_cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        )
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_cache (
                        cache_key TEXT PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        workflow_version TEXT NOT NULL,
                        ruleset_version TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at INTEGER NOT NULL
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

    @staticmethod
    def key(
        namespace: str,
        input_identity: Any,
        workflow_version: str,
        ruleset_version: str,
    ) -> str:
        material = json.dumps(
            {
                "namespace": namespace,
                "input": input_identity,
                "workflow_version": workflow_version,
                "ruleset_version": ruleset_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(
        self,
        namespace: str,
        input_identity: Any,
        workflow_version: str,
        ruleset_version: str,
    ) -> dict | None:
        cache_key = self.key(
            namespace, input_identity, workflow_version, ruleset_version
        )
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT payload, created_at FROM workflow_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                payload, created_at = row
                if self.ttl_seconds >= 0 and int(time.time()) - created_at > self.ttl_seconds:
                    connection.execute(
                        "DELETE FROM workflow_cache WHERE cache_key = ?", (cache_key,)
                    )
                    connection.commit()
                    return None
            finally:
                connection.close()
        value = json.loads(payload)
        return value if isinstance(value, dict) else None

    def set(
        self,
        namespace: str,
        input_identity: Any,
        workflow_version: str,
        ruleset_version: str,
        payload: dict,
    ) -> None:
        cache_key = self.key(
            namespace, input_identity, workflow_version, ruleset_version
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO workflow_cache (
                        cache_key, namespace, workflow_version,
                        ruleset_version, payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload = excluded.payload,
                        created_at = excluded.created_at
                    """,
                    (
                        cache_key,
                        namespace,
                        workflow_version,
                        ruleset_version,
                        encoded,
                        int(time.time()),
                    ),
                )
                connection.commit()
            finally:
                connection.close()


workflow_result_cache = WorkflowResultCache()
