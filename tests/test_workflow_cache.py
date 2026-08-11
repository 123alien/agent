import tempfile
import time
import unittest
from pathlib import Path

from app.services.workflow_cache import WorkflowResultCache


class WorkflowResultCacheTests(unittest.TestCase):
    def test_round_trip_and_version_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = WorkflowResultCache(Path(directory) / "cache.sqlite", 60)
            identity = {"file_hash": "abc", "query": "地域限制"}
            cache.set("compliance", identity, "1.0.0", "1.0.0", {"issues": []})

            self.assertEqual(
                cache.get("compliance", identity, "1.0.0", "1.0.0"),
                {"issues": []},
            )
            self.assertIsNone(
                cache.get("compliance", identity, "1.1.0", "1.0.0")
            )
            self.assertIsNone(
                cache.get("compliance", identity, "1.0.0", "2.0.0")
            )

    def test_expired_entry_is_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = WorkflowResultCache(Path(directory) / "cache.sqlite", 0)
            cache.set("compliance", "input", "1", "1", {"value": 1})
            time.sleep(1.05)

            self.assertIsNone(cache.get("compliance", "input", "1", "1"))


if __name__ == "__main__":
    unittest.main()
