from __future__ import annotations

import asyncio
import json
import logging
import queue
import shutil
import threading
from dataclasses import dataclass, field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable

from pdf2zh_next_service import TranslationOutputFile
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
    workspace_dir: Path
    status: TaskStatus = "queued"
    stage: str | None = None
    stage_current: int = 0
    stage_total: int = 0
    stage_progress: float = 0.0
    overall_progress: float = 0.0
    error: str | None = None
    result_files: dict[str, TranslationOutputFile] = field(default_factory=dict)
    attempt: int = 1
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
            "attempt": self.attempt,
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
    def __init__(self, persistence_path: Path | str | None = None) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._persistence_path = (
            Path(persistence_path) if persistence_path is not None else None
        )
        self._load_persistent_tasks()

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
        task_id: str,
        file_name: str,
        service: str,
        output_modes: list[str],
        request_payload: dict[str, Any],
        workspace_dir: Path,
    ) -> dict[str, Any]:
        record = TaskRecord(
            task_id=task_id,
            file_name=file_name,
            service=service,
            output_modes=output_modes,
            request_payload=request_payload,
            workspace_dir=workspace_dir,
        )
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            daemon=True,
            name=f"pdf2zh-task-{task_id}",
        )
        with self._lock:
            self._tasks[task_id] = record
        self._publish_event({"type": "task", "task": record.to_dict()})
        LOGGER.info(
            "[%s] task queued: file=%s service=%s output_modes=%s workspace=%s",
            task_id,
            file_name,
            service,
            ",".join(output_modes),
            workspace_dir,
        )
        thread.start()
        return record.to_dict()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        with self._lock:
            self._subscribers.add(event_queue)
            records = list(self._tasks.values())
        records.sort(key=lambda record: record.created_at, reverse=True)
        for record in records:
            self._enqueue_event(
                event_queue,
                {"type": "snapshot", "task": record.to_dict()},
            )
        return event_queue

    def unsubscribe(self, event_queue: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(event_queue)

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
            snapshot = record.to_dict()
            cancel_callback = record.cancel_callback

        LOGGER.info("[%s] cancellation requested", task_id)
        self._publish_event({"type": "task", "task": snapshot})
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

        shutil.rmtree(deleted.workspace_dir, ignore_errors=True)
        self._save_persistent_tasks()
        self._publish_event({"type": "deleted", "taskId": task_id})
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
            shutil.rmtree(record.workspace_dir, ignore_errors=True)

        self._save_persistent_tasks()
        if failed_task_ids:
            LOGGER.info("cleared failed tasks: %s", ",".join(failed_task_ids))
        for task_id in failed_task_ids:
            self._publish_event({"type": "deleted", "taskId": task_id})
        return len(failed_task_ids)

    def retry_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            if record.status in {"queued", "running", "cancelling"}:
                raise ValueError("Active task cannot be retried")
            if record.status != "failed":
                raise ValueError("Only failed tasks can be retried")

            input_path = Path(record.request_payload["input_path"])
            output_dir = Path(record.request_payload["output_dir"])
            if not input_path.exists():
                raise ValueError("Task input file is no longer available")

            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            record.status = "queued"
            record.stage = None
            record.stage_current = 0
            record.stage_total = 0
            record.stage_progress = 0.0
            record.overall_progress = 0.0
            record.error = None
            record.result_files = {}
            record.attempt += 1
            record.cancel_requested = False
            record.cancel_callback = None
            record.updated_at = utc_now_iso()
            snapshot = record.to_dict()

            thread = threading.Thread(
                target=self._run_task,
                args=(task_id,),
                daemon=True,
                name=f"pdf2zh-task-{task_id}-retry",
            )

        self._save_persistent_tasks()
        self._publish_event({"type": "task", "task": snapshot})
        LOGGER.info("[%s] task retry queued", task_id)
        thread.start()
        return snapshot

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
            if not result_file.output_path.exists():
                return record, None
            return record, result_file

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks[task_id]
            record.status = "cancelling" if record.cancel_requested else "running"
            record.updated_at = utc_now_iso()
            request_payload = dict(record.request_payload)
            snapshot = record.to_dict()

        self._publish_event({"type": "task", "task": snapshot})

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
            snapshot = record.to_dict()
        self._save_persistent_tasks()
        self._publish_event({"type": "task", "task": snapshot})
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
            snapshot = record.to_dict()

        self._publish_event({"type": "task", "task": snapshot})

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
            workspace_dir = record.workspace_dir
            snapshot = record.to_dict()

        if cancelled:
            shutil.rmtree(workspace_dir, ignore_errors=True)
        self._save_persistent_tasks()
        self._publish_event({"type": "task", "task": snapshot})
        if cancelled:
            LOGGER.info("[%s] task cancelled", task_id)
            return
        LOGGER.error("[%s] task failed: %s", task_id, error_message)

    def _publish_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for event_queue in subscribers:
            self._enqueue_event(event_queue, event)

    @staticmethod
    def _enqueue_event(
        event_queue: queue.Queue[dict[str, Any]],
        event: dict[str, Any],
    ) -> None:
        try:
            event_queue.put_nowait(event)
            return
        except queue.Full:
            pass

        try:
            event_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            event_queue.put_nowait(event)
        except queue.Full:
            pass

    def _load_persistent_tasks(self) -> None:
        if self._persistence_path is None or not self._persistence_path.exists():
            return

        try:
            payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("failed to load persisted tasks: %s", exc)
            return

        records = payload.get("tasks", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return

        for record_payload in records:
            if not isinstance(record_payload, dict):
                continue
            record = self._record_from_persistence(record_payload)
            if record is None or record.status in {"queued", "running", "cancelling"}:
                continue
            self._tasks[record.task_id] = record

    def _save_persistent_tasks(self) -> None:
        if self._persistence_path is None:
            return

        with self._lock:
            records = [
                self._record_to_persistence(record)
                for record in self._tasks.values()
                if record.status not in {"queued", "running", "cancelling"}
            ]

        payload = {"tasks": records}
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._persistence_path.with_suffix(
                self._persistence_path.suffix + ".tmp"
            )
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self._persistence_path)
        except OSError as exc:
            LOGGER.warning("failed to save persisted tasks: %s", exc)

    @staticmethod
    def _record_to_persistence(record: TaskRecord) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "file_name": record.file_name,
            "service": record.service,
            "output_modes": record.output_modes,
            "request_payload": record.request_payload,
            "workspace_dir": str(record.workspace_dir),
            "status": record.status,
            "stage": record.stage,
            "stage_current": record.stage_current,
            "stage_total": record.stage_total,
            "stage_progress": record.stage_progress,
            "overall_progress": record.overall_progress,
            "error": record.error,
            "attempt": record.attempt,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "cancel_requested": record.cancel_requested,
            "result_files": {
                output_mode: {
                    "output_mode": output_file.output_mode,
                    "output_path": str(output_file.output_path),
                    "filename": output_file.filename,
                }
                for output_mode, output_file in record.result_files.items()
            },
        }

    @staticmethod
    def _record_from_persistence(payload: dict[str, Any]) -> TaskRecord | None:
        try:
            result_files_payload = payload.get("result_files", {})
            result_files = {}
            if isinstance(result_files_payload, dict):
                for output_mode, output_payload in result_files_payload.items():
                    if not isinstance(output_payload, dict):
                        continue
                    output_path = output_payload.get("output_path")
                    filename = output_payload.get("filename")
                    if not output_path or not filename:
                        continue
                    result_files[str(output_mode)] = TranslationOutputFile(
                        output_mode=str(
                            output_payload.get("output_mode") or output_mode
                        ),
                        output_path=Path(output_path),
                        filename=str(filename),
                    )

            return TaskRecord(
                task_id=str(payload["task_id"]),
                file_name=str(payload["file_name"]),
                service=str(payload["service"]),
                output_modes=list(payload.get("output_modes", [])),
                request_payload=dict(payload.get("request_payload", {})),
                workspace_dir=Path(payload["workspace_dir"]),
                status=str(payload.get("status", "failed")),
                stage=payload.get("stage"),
                stage_current=TaskManager._coerce_int(payload.get("stage_current"), 0),
                stage_total=TaskManager._coerce_int(payload.get("stage_total"), 0),
                stage_progress=TaskManager._coerce_float(
                    payload.get("stage_progress"),
                    0.0,
                ),
                overall_progress=TaskManager._coerce_float(
                    payload.get("overall_progress"),
                    0.0,
                ),
                error=payload.get("error"),
                result_files=result_files,
                attempt=TaskManager._coerce_int(payload.get("attempt"), 1),
                created_at=str(payload.get("created_at") or utc_now_iso()),
                updated_at=str(payload.get("updated_at") or utc_now_iso()),
                cancel_requested=bool(payload.get("cancel_requested", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None

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
