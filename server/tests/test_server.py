from __future__ import annotations

import base64
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

    def test_validate_config_returns_service_and_model(self) -> None:
        with patch.object(
            server_module,
            "validate_config_request",
            return_value=SimpleNamespace(service="openai", model="gpt-4.1"),
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
