from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from typing import Callable

from pdf2zh_next_service import TranslationOutputFile
from pdf2zh_next_service import TranslationResult
from pdf2zh_next_service import explain_service_error
from pdf2zh_next_service import translate_pdf_with_callbacks

TaskStatus = str
LOGGER = logging.getLogger("zotero_pdf2zh_server.tasks")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class TaskRecord:
    task_id: str
    file_name: str
    service: str
    output_modes: list[str]
    request_payload: dict[str, Any]
    temp_dir: TemporaryDirectory[str]
    status: TaskStatus = "queued"
    stage: str | None = None
    stage_current: int = 0
    stage_total: int = 0
    stage_progress: float = 0.0
    overall_progress: float = 0.0
    error: str | None = None
    result_files: dict[str, TranslationOutputFile] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    cancel_requested: bool = False
    cancel_callback: Callable[[], None] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "fileName": self.file_name,
            "service": self.service,
            "outputModes": self.output_modes,
            "status": self.status,
            "stage": self.stage,
            "stageCurrent": self.stage_current,
            "stageTotal": self.stage_total,
            "stageProgress": round(self.stage_progress, 1),
            "overallProgress": round(self.overall_progress, 1),
            "error": self.error,
            "resultFiles": {
                output_mode: output_file.filename
                for output_mode, output_file in self.result_files.items()
            },
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "canCancel": self.status in {"queued", "running", "cancelling"},
            "cancelRequested": self.cancel_requested,
        }


class TaskManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._tasks.values())
        records.sort(key=lambda record: record.created_at, reverse=True)
        return [record.to_dict() for record in records]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            return record.to_dict()

    def create_task(
        self,
        *,
        file_name: str,
        service: str,
        output_modes: list[str],
        request_payload: dict[str, Any],
        temp_dir: TemporaryDirectory[str],
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        record = TaskRecord(
            task_id=task_id,
            file_name=file_name,
            service=service,
            output_modes=output_modes,
            request_payload=request_payload,
            temp_dir=temp_dir,
        )
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            daemon=True,
            name=f"pdf2zh-task-{task_id}",
        )
        with self._lock:
            self._tasks[task_id] = record
        LOGGER.info(
            "[%s] task queued: file=%s service=%s output_modes=%s",
            task_id,
            file_name,
            service,
            ",".join(output_modes),
        )
        thread.start()
        return record.to_dict()

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status not in {"queued", "running", "cancelling"}:
                return record.to_dict()
            record.cancel_requested = True
            record.status = "cancelling"
            record.updated_at = utc_now_iso()
            cancel_callback = record.cancel_callback

        LOGGER.info("[%s] cancellation requested", task_id)
        if cancel_callback is not None:
            cancel_callback()

        with self._lock:
            return self._tasks[task_id].to_dict()

    def delete_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status in {"queued", "running", "cancelling"}:
                raise ValueError("Active task cannot be deleted")
            deleted = self._tasks.pop(task_id)

        deleted.temp_dir.cleanup()
        LOGGER.info("[%s] task deleted", task_id)
        return deleted.to_dict()

    def clear_failed_tasks(self) -> int:
        with self._lock:
            failed_task_ids = [
                task_id
                for task_id, record in self._tasks.items()
                if record.status == "failed"
            ]
            deleted_records = [self._tasks.pop(task_id) for task_id in failed_task_ids]

        for record in deleted_records:
            record.temp_dir.cleanup()

        if failed_task_ids:
            LOGGER.info("cleared failed tasks: %s", ",".join(failed_task_ids))
        return len(failed_task_ids)

    def get_result_file(
        self,
        task_id: str,
        output_mode: str | None = None,
    ) -> tuple[TaskRecord, TranslationOutputFile | None] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status != "completed" or not record.result_files:
                return record, None

            selected_output_mode = output_mode
            if selected_output_mode is None:
                if len(record.result_files) != 1:
                    return record, None
                selected_output_mode = next(iter(record.result_files))

            result_file = record.result_files.get(selected_output_mode)
            if result_file is None:
                return record, None
            return record, result_file

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "cancelling" if record.cancel_requested else "running"
            record.updated_at = utc_now_iso()
            request_payload = dict(record.request_payload)

        try:
            result = asyncio.run(
                translate_pdf_with_callbacks(
                    request_payload,
                    task_id,
                    progress_callback=lambda event: self._handle_progress_event(
                        task_id, event
                    ),
                    on_config_ready=lambda config: self._register_cancel_callback(
                        task_id,
                        config.cancel_translation,
                    ),
                )
            )
        except Exception as exc:
            self._handle_task_error(task_id, exc)
            return

        with self._lock:
            record = self._tasks[task_id]
            record.status = "completed"
            record.result_files = dict(result.files)
            record.stage = "completed"
            record.stage_progress = 100.0
            record.overall_progress = 100.0
            record.updated_at = utc_now_iso()
        LOGGER.info(
            "[%s] task completed: %s",
            task_id,
            ", ".join(file.filename for file in result.files.values()),
        )

    def _register_cancel_callback(
        self,
        task_id: str,
        cancel_callback: Callable[[], None],
    ) -> None:
        should_cancel_immediately = False
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            record.cancel_callback = cancel_callback
            record.updated_at = utc_now_iso()
            should_cancel_immediately = record.cancel_requested

        if should_cancel_immediately:
            cancel_callback()

    def _handle_progress_event(self, task_id: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            if record.status == "queued":
                record.status = "running"

            if event_type in {"progress_start", "progress_update", "progress_end"}:
                record.stage = str(event.get("stage") or record.stage or "unknown")
                record.stage_current = self._coerce_int(
                    event.get("stage_current"),
                    record.stage_current,
                )
                record.stage_total = self._coerce_int(
                    event.get("stage_total"),
                    record.stage_total,
                )
                record.stage_progress = self._coerce_float(
                    event.get("stage_progress"),
                    record.stage_progress,
                )
                record.overall_progress = self._coerce_float(
                    event.get("overall_progress"),
                    record.overall_progress,
                )

            if event_type == "error":
                record.error = str(event.get("error") or "translation failed")

            record.updated_at = utc_now_iso()

    def _handle_task_error(self, task_id: str, exc: Exception) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return

            error_message = explain_service_error(str(exc) or exc.__class__.__name__)
            cancelled = record.cancel_requested or "CancelledError" in error_message
            record.status = "cancelled" if cancelled else "failed"
            record.stage = "cancelled" if cancelled else "failed"
            record.error = None if cancelled else error_message
            record.updated_at = utc_now_iso()
            temp_dir = record.temp_dir

        temp_dir.cleanup()
        if cancelled:
            LOGGER.info("[%s] task cancelled", task_id)
            return
        LOGGER.error("[%s] task failed: %s", task_id, error_message)

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
