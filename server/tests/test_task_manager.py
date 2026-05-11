from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from pdf2zh_next_service import TranslationOutputFile
from task_manager import TaskManager
from task_manager import TaskRecord


class TaskManagerTests(unittest.TestCase):
    def test_retry_increments_attempt_and_keeps_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir) / "workspace"
            output_dir = workspace_dir / "output"
            input_path = workspace_dir / "paper.pdf"
            output_dir.mkdir(parents=True)
            input_path.write_bytes(b"%PDF-1.4\n")
            stale_output = output_dir / "stale.pdf"
            stale_output.write_bytes(b"stale")

            manager = TaskManager()
            manager._tasks["task-1"] = TaskRecord(
                task_id="task-1",
                file_name="paper.pdf",
                service="openai",
                output_modes=["dual"],
                request_payload={
                    "input_path": str(input_path),
                    "output_dir": str(output_dir),
                },
                workspace_dir=workspace_dir,
                status="failed",
                stage="failed",
                error="boom",
                attempt=1,
            )

            with patch(
                "task_manager.threading.Thread",
                return_value=SimpleNamespace(start=lambda: None),
            ):
                snapshot = manager.retry_task("task-1")

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["attempt"], 2)
            self.assertEqual(snapshot["status"], "queued")
            self.assertTrue(workspace_dir.exists())
            self.assertTrue(output_dir.exists())
            self.assertFalse(stale_output.exists())
            self.assertEqual(manager._tasks["task-1"].attempt, 2)

    def test_persistence_restores_failed_and_completed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            persistence_path = temp_path / "tasks.json"
            completed_workspace = temp_path / "completed"
            completed_output = completed_workspace / "paper.dual.pdf"
            completed_output.parent.mkdir()
            completed_output.write_bytes(b"%PDF-1.4\n")

            manager = TaskManager(persistence_path=persistence_path)
            manager._tasks["failed-1"] = TaskRecord(
                task_id="failed-1",
                file_name="failed.pdf",
                service="openai",
                output_modes=["mono"],
                request_payload={},
                workspace_dir=temp_path / "failed",
                status="failed",
                stage="failed",
                error="boom",
                attempt=3,
            )
            manager._tasks["completed-1"] = TaskRecord(
                task_id="completed-1",
                file_name="paper.pdf",
                service="openai",
                output_modes=["dual"],
                request_payload={},
                workspace_dir=completed_workspace,
                status="completed",
                stage="completed",
                overall_progress=100.0,
                result_files={
                    "dual": TranslationOutputFile(
                        output_mode="dual",
                        output_path=completed_output,
                        filename=completed_output.name,
                    )
                },
            )
            manager._tasks["running-1"] = TaskRecord(
                task_id="running-1",
                file_name="running.pdf",
                service="openai",
                output_modes=["dual"],
                request_payload={},
                workspace_dir=temp_path / "running",
                status="running",
            )

            manager._save_persistent_tasks()

            restored = TaskManager(persistence_path=persistence_path)
            self.assertIsNotNone(restored.get_task("failed-1"))
            self.assertEqual(restored.get_task("failed-1")["attempt"], 3)
            self.assertIsNone(restored.get_task("running-1"))

            result = restored.get_result_file("completed-1", "dual")
            self.assertIsNotNone(result)
            _, result_file = result
            self.assertIsNotNone(result_file)
            self.assertEqual(result_file.output_path, completed_output)
            self.assertEqual(
                restored.get_task("completed-1")["resultFiles"],
                {"dual": "paper.dual.pdf"},
            )

    def test_subscriber_queue_keeps_latest_event_without_unbounded_growth(self) -> None:
        manager = TaskManager()
        event_queue = manager.subscribe()

        for index in range(200):
            manager._publish_event({"type": "deleted", "taskId": f"task-{index}"})

        self.assertLessEqual(event_queue.qsize(), event_queue.maxsize)

        last_event = None
        while not event_queue.empty():
            last_event = event_queue.get_nowait()

        self.assertEqual(last_event, {"type": "deleted", "taskId": "task-199"})

    def test_task_snapshot_includes_attempt(self) -> None:
        record = TaskRecord(
            task_id="task-1",
            file_name="paper.pdf",
            service="openai",
            output_modes=["dual"],
            request_payload={},
            workspace_dir=Path("/tmp/workspace"),
        )

        self.assertEqual(record.to_dict()["attempt"], 1)

    def test_missing_result_file_is_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "missing.pdf"
            manager = TaskManager()
            manager._tasks["task-1"] = TaskRecord(
                task_id="task-1",
                file_name="paper.pdf",
                service="openai",
                output_modes=["dual"],
                request_payload={},
                workspace_dir=Path(temp_dir),
                status="completed",
                result_files={
                    "dual": TranslationOutputFile(
                        output_mode="dual",
                        output_path=output_path,
                        filename=output_path.name,
                    )
                },
            )

            record, result_file = manager.get_result_file("task-1", "dual")

        self.assertEqual(record.task_id, "task-1")
        self.assertIsNone(result_file)


if __name__ == "__main__":
    unittest.main()
