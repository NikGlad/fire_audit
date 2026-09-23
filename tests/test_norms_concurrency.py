from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fire_audit.norms import NormativeIndex
from fire_audit.norm_lock import NormativeUpdateBusy, normative_update_lock
from fire_audit.paths import get_paths


class NormativeConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = get_paths(Path(self.temp.name) / "app")
        with zipfile.ZipFile(Path(self.temp.name) / "fire-docs-main.zip", "w") as archive:
            archive.writestr("fire-docs-main/рабочая/старый.txt", "старый документ")
        self.index = NormativeIndex(self.paths)
        self.index.rebuild(full_text=True)

    @staticmethod
    def new_record() -> dict:
        return {
            "relative_path": "рабочая/новый.txt",
            "folder": "рабочая",
            "suffix": ".txt",
            "text_status": "text_extracted",
            "content": "новый документ",
        }

    def test_extraction_does_not_hold_live_database_write_lock(self) -> None:
        extracting = threading.Event()
        release = threading.Event()

        def slow_records(*args, **kwargs):
            yield self.new_record()
            extracting.set()
            if not release.wait(10):
                raise RuntimeError("Test did not release extraction")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(self.index, "_iter_records", side_effect=slow_records):
                future = pool.submit(self.index.rebuild, full_text=True)
                try:
                    self.assertTrue(extracting.wait(10))
                    # Another process can still read the complete previous index.
                    self.assertEqual(self.index.status()["documents"], 1)
                    self.assertEqual(len(self.index.search("старый")), 1)
                    # Text extraction must not hold any write transaction on the live DB.
                    with closing(sqlite3.connect(self.paths.index, timeout=0.1)) as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        connection.rollback()
                finally:
                    release.set()
                    future.result(timeout=10)
        self.assertEqual(self.index.search("старый"), [])
        self.assertEqual(len(self.index.search("новый")), 1)

    def test_failed_extraction_preserves_previous_index_and_allows_retry(self) -> None:
        def broken_records(*args, **kwargs):
            yield self.new_record()
            raise RuntimeError("extraction interrupted")

        with patch.object(self.index, "_iter_records", side_effect=broken_records):
            with self.assertRaisesRegex(RuntimeError, "extraction interrupted"):
                self.index.rebuild(full_text=True)
        self.assertEqual(self.index.status()["documents"], 1)
        self.assertEqual(len(self.index.search("старый")), 1)
        self.assertEqual(self.index.search("новый"), [])
        self.assertTrue(self.index.rebuild(full_text=True)["ok"])

    def test_failed_publication_rolls_back_documents_and_full_text_index(self) -> None:
        with closing(sqlite3.connect(self.paths.index)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_import BEFORE INSERT ON documents "
                "BEGIN SELECT RAISE(ABORT, 'test publication failure'); END"
            )
        with patch.object(self.index, "_iter_records", return_value=iter([self.new_record()])):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "test publication failure"):
                self.index.rebuild(full_text=True)
        self.assertEqual(len(self.index.search("старый")), 1)
        self.assertEqual(self.index.search("новый"), [])
        with closing(sqlite3.connect(self.paths.index)) as connection, connection:
            connection.execute("DROP TRIGGER fail_import")
        self.assertTrue(self.index.rebuild(full_text=True)["ok"])

    def test_competing_rebuild_is_rejected_across_processes_then_can_retry(self) -> None:
        # Represents a separate GUI window or the MCP server, with a separate interpreter.
        code = """
import sys
from fire_audit.norms import NormativeIndex
from fire_audit.norm_lock import NormativeUpdateBusy
from fire_audit.paths import get_paths
try:
    NormativeIndex(get_paths(sys.argv[1])).rebuild(full_text=True)
except NormativeUpdateBusy:
    print('busy')
else:
    print('updated')
"""
        command = [sys.executable, "-c", code, str(self.paths.root)]
        kwargs = dict(capture_output=True, text=True, timeout=15, cwd=Path(__file__).resolve().parents[1])
        with normative_update_lock(self.paths.normative_cache / "update.lock"):
            blocked = subprocess.run(command, **kwargs)
            self.assertEqual(blocked.returncode, 0, blocked.stderr)
            self.assertEqual(blocked.stdout.strip(), "busy")
            self.assertEqual(len(self.index.search("старый")), 1)
        retried = subprocess.run(command, **kwargs)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(retried.stdout.strip(), "updated")

    def test_sync_and_rebuild_share_one_lock_across_both_phases(self) -> None:
        contender = NormativeIndex(self.paths)

        def sync(*args, **kwargs):
            with self.assertRaises(NormativeUpdateBusy):
                contender.rebuild()
            return {"ok": True}

        def records(*args, **kwargs):
            with self.assertRaises(NormativeUpdateBusy):
                contender.sync_repository()
            yield self.new_record()

        with patch.object(self.index, "_sync_repository", side_effect=sync):
            with patch.object(self.index, "_iter_records", side_effect=records):
                result = self.index.sync_repository(rebuild_index=True)
        self.assertTrue(result["index"]["ok"])
        self.assertEqual(len(self.index.search("новый")), 1)


if __name__ == "__main__":
    unittest.main()
