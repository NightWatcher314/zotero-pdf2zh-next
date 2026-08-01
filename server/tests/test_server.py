from __future__ import annotations

import base64
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import server as server_module


def build_pdf_payload() -> str:
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    return "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")


class ServerRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = server_module.create_app().test_client()

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertIn("version", response.json)
        self.assertIn("pythonVersion", response.json)
        self.assertIn("pdf2zhVersion", response.json)
        self.assertIn("babeldocVersion", response.json)
        self.assertIn("workspace", response.json)
        self.assertTrue(response.json["workspace"]["writable"])
        self.assertIn("freeBytes", response.json["workspace"])
        self.assertIn("tasks", response.json)
        self.assertIn("total", response.json["tasks"])

    def test_translate_returns_pdf_response(self) -> None:
        with patch.object(
            server_module,
            "translate_pdf_request",
            return_value=(b"%PDF-1.4\n", "paper.dual.pdf", "dual"),
        ):
            response = self.client.post(
                "/translate",
                json={
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputMode": "dual",
                    "service": "openai",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertEqual(response.headers["X-PDF2ZH-Output-Mode"], "dual")
        self.assertEqual(response.data, b"%PDF-1.4\n")

    def test_translate_rejects_missing_file_content(self) -> None:
        response = self.client.post(
            "/translate",
            json={"fileName": "paper.pdf", "outputMode": "mono"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["status"], "error")
        self.assertIn("diagnostics", response.json)

    def test_translate_rejects_invalid_output_mode(self) -> None:
        response = self.client.post(
            "/translate",
            json={
                "fileName": "paper.pdf",
                "fileContent": build_pdf_payload(),
                "outputMode": "compare",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("outputMode", response.json["message"])

    def test_create_task_returns_snapshot(self) -> None:
        with patch.object(
            server_module.TASK_MANAGER,
            "create_task",
            return_value={
                "taskId": "task-1",
                "fileName": "paper.pdf",
                "service": "openai",
                "outputModes": ["mono", "dual"],
                "status": "queued",
                "stage": None,
                "stageCurrent": 0,
                "stageTotal": 0,
                "stageProgress": 0,
                "overallProgress": 0,
                "error": None,
                "resultFiles": {},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "canCancel": True,
                "cancelRequested": False,
            },
        ):
            response = self.client.post(
                "/tasks",
                json={
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["mono", "dual"],
                    "service": "openai",
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["task"]["taskId"], "task-1")
        self.assertEqual(response.json["task"]["outputModes"], ["mono", "dual"])

    def test_get_task_status_returns_task(self) -> None:
        with patch.object(
            server_module.TASK_MANAGER,
            "get_task",
            return_value={
                "taskId": "task-1",
                "fileName": "paper.pdf",
                "service": "openai",
                "outputModes": ["dual"],
                "status": "running",
                "stage": "translate",
                "stageCurrent": 3,
                "stageTotal": 10,
                "stageProgress": 30,
                "overallProgress": 45,
                "error": None,
                "resultFiles": {},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:01:00Z",
                "canCancel": True,
                "cancelRequested": False,
            },
        ):
            response = self.client.get("/tasks/task-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["task"]["status"], "running")
        self.assertEqual(response.json["task"]["stage"], "translate")

    def test_delete_task_returns_deleted_task(self) -> None:
        with patch.object(
            server_module.TASK_MANAGER,
            "delete_task",
            return_value={
                "taskId": "task-1",
                "fileName": "paper.pdf",
                "service": "openai",
                "outputModes": ["dual"],
                "status": "failed",
                "stage": "failed",
                "stageCurrent": 0,
                "stageTotal": 0,
                "stageProgress": 0,
                "overallProgress": 0,
                "error": "boom",
                "resultFiles": {},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:01:00Z",
                "canCancel": False,
                "cancelRequested": False,
            },
        ):
            response = self.client.delete("/tasks/task-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["task"]["taskId"], "task-1")

    def test_clear_failed_tasks_returns_deleted_count(self) -> None:
        with patch.object(
            server_module.TASK_MANAGER,
            "clear_failed_tasks",
            return_value=2,
        ):
            response = self.client.post("/tasks/clear-failed")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["deletedCount"], 2)

    def test_retry_task_returns_requeued_task(self) -> None:
        with patch.object(
            server_module.TASK_MANAGER,
            "retry_task",
            return_value={
                "taskId": "task-1",
                "fileName": "paper.pdf",
                "service": "openai",
                "outputModes": ["dual"],
                "status": "queued",
                "stage": None,
                "stageCurrent": 0,
                "stageTotal": 0,
                "stageProgress": 0,
                "overallProgress": 0,
                "error": None,
                "resultFiles": {},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:01:00Z",
                "canCancel": True,
                "cancelRequested": False,
            },
        ):
            response = self.client.post("/tasks/task-1/retry")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["task"]["status"], "queued")

    def test_task_events_streams_task_events(self) -> None:
        event_queue: queue.Queue[dict[str, object]] = queue.Queue()
        event_queue.put({"type": "deleted", "taskId": "task-1"})

        with (
            patch.object(
                server_module.TASK_MANAGER,
                "subscribe",
                return_value=event_queue,
            ),
            patch.object(server_module.TASK_MANAGER, "unsubscribe") as unsubscribe,
        ):
            response = self.client.get("/tasks/events", buffered=False)
            first_chunk = next(response.response).decode("utf-8")
            response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.content_type)
        self.assertIn('"type": "deleted"', first_chunk)
        self.assertIn('"taskId": "task-1"', first_chunk)
        unsubscribe.assert_called_once_with(event_queue)

    def test_validate_config_returns_service_and_model(self) -> None:
        with patch.object(
            server_module,
            "validate_config_request",
            return_value=SimpleNamespace(
                service="openai",
                model="gpt-4.1",
                status="ok",
                diagnostics=[
                    {
                        "code": "config_constructed",
                        "severity": "info",
                        "message": "ok",
                    }
                ],
                live_test={"enabled": True, "ok": True, "message": "你好"},
            ),
        ):
            response = self.client.post(
                "/validate-config",
                json={
                    "service": "openai",
                    "sourceLang": "en",
                    "targetLang": "zh-CN",
                    "llm_api": {
                        "model": "gpt-4.1",
                        "apiKey": "sk-test",
                        "apiUrl": "https://api.openai.com/v1",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["service"], "openai")
        self.assertEqual(response.json["model"], "gpt-4.1")
        self.assertEqual(response.json["diagnostics"][0]["code"], "config_constructed")
        self.assertTrue(response.json["liveTest"]["ok"])

    def test_validate_config_passes_live_test_flag(self) -> None:
        with patch.object(server_module, "validate_service_config") as validate:
            validate.return_value = SimpleNamespace(
                service="openai",
                model="gpt-4.1",
                status="ok",
                diagnostics=[],
                live_test={"enabled": True, "ok": True},
            )

            response = self.client.post(
                "/validate-config",
                json={
                    "service": "openai",
                    "liveTest": True,
                    "translateTableText": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(validate.call_args.args[0]["live_test"])
        self.assertFalse(validate.call_args.args[0]["translate_table_text"])

    def test_validate_config_can_report_live_test_warning(self) -> None:
        with patch.object(server_module, "validate_service_config") as validate:
            validate.return_value = SimpleNamespace(
                service="openai",
                model="gpt-4.1",
                status="warning",
                diagnostics=[
                    {
                        "code": "llm_auth",
                        "severity": "error",
                        "message": "bad key",
                    }
                ],
                live_test={"enabled": True, "ok": False, "message": "401"},
            )

            response = self.client.post(
                "/validate-config",
                json={
                    "service": "openai",
                    "liveTest": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "warning")
        self.assertFalse(response.json["liveTest"]["ok"])

    def test_health_reports_degraded_workspace_without_raising(self) -> None:
        with (
            patch.object(server_module, "TRANSLATES_DIR", Path("/missing-parent/x")),
            patch.object(
                server_module.Path,
                "mkdir",
                side_effect=PermissionError("permission denied"),
            ),
            patch.object(server_module.shutil, "disk_usage") as disk_usage,
        ):
            disk_usage.return_value = SimpleNamespace(free=123)
            payload = server_module.build_health_payload()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["workspace"]["writable"])
        self.assertIn("permission denied", payload["workspace"]["error"])

    def test_prepare_translation_request_uses_workspace_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            prepared = server_module.prepare_translation_request(
                {
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["mono", "dual"],
                    "service": "openai",
                },
                workspace_dir,
            )

            self.assertEqual(prepared.file_name, "paper.pdf")
            self.assertEqual(prepared.output_modes, ["mono", "dual"])
            self.assertEqual(
                Path(prepared.request_payload["input_path"]),
                workspace_dir / "paper.pdf",
            )
            self.assertEqual(
                Path(prepared.request_payload["output_dir"]),
                workspace_dir / "output",
            )
            self.assertTrue((workspace_dir / "paper.pdf").exists())
            self.assertTrue((workspace_dir / "output").is_dir())
            self.assertFalse(prepared.request_payload["no_auto_extract_glossary"])
            self.assertTrue(prepared.request_payload["translate_table_text"])

    def test_prepare_translation_request_can_disable_term_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            prepared = server_module.prepare_translation_request(
                {
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["dual"],
                    "service": "openai",
                    "disableTermExtraction": True,
                },
                workspace_dir,
            )

            self.assertTrue(prepared.request_payload["no_auto_extract_glossary"])

    def test_prepare_translation_request_can_skip_text_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            prepared = server_module.prepare_translation_request(
                {
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["dual"],
                    "service": "openai",
                    "skipTextChecks": True,
                },
                workspace_dir,
            )

            self.assertTrue(prepared.request_payload["skip_text_checks"])

    def test_prepare_translation_request_can_skip_table_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            prepared = server_module.prepare_translation_request(
                {
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["dual"],
                    "service": "openai",
                    "translateTableText": False,
                },
                workspace_dir,
            )

            self.assertFalse(prepared.request_payload["translate_table_text"])

    def test_create_workspace_dir_uses_translates_folder(self) -> None:
        workspace_dir = server_module.create_workspace_dir("test-job")
        try:
            self.assertEqual(workspace_dir, server_module.TRANSLATES_DIR / "test-job")
            self.assertTrue(workspace_dir.is_dir())
        finally:
            server_module.remove_workspace_dir(workspace_dir)

    def test_task_result_returns_pdf_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "paper.dual.pdf"
            output_path.write_bytes(b"%PDF-1.4\n")

            with patch.object(
                server_module.TASK_MANAGER,
                "get_result_file",
                return_value=(
                    SimpleNamespace(status="completed", task_id="task-1"),
                    SimpleNamespace(
                        output_path=output_path,
                        filename="paper.dual.pdf",
                        output_mode="dual",
                    ),
                ),
            ):
                response = self.client.get("/tasks/task-1/result?mode=dual")

        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-PDF2ZH-Task-Id"], "task-1")
            self.assertEqual(response.headers["X-PDF2ZH-Output-Mode"], "dual")
            self.assertEqual(response.data, b"%PDF-1.4\n")
        finally:
            response.close()

    def test_task_result_rejects_invalid_output_mode(self) -> None:
        response = self.client.get("/tasks/task-1/result?mode=compare")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["status"], "error")
        self.assertIn("outputMode", response.json["message"])


if __name__ == "__main__":
    unittest.main()
