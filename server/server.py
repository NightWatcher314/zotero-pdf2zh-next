from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from pdf2zh_next_service import explain_service_error
from pdf2zh_next_service import translate_pdf
from pdf2zh_next_service import validate_service_config
from task_manager import TaskManager

VERSION = "5.0.0"
LOGGER = logging.getLogger("zotero_pdf2zh_server")
TASK_MANAGER = TaskManager()


class RequestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedTranslationRequest:
    file_name: str
    service: str
    output_modes: list[str]
    request_payload: dict[str, Any]


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok", "version": VERSION}, 200

    @app.post("/translate")
    def translate():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Expected a JSON body"}), 400

        try:
            pdf_bytes, filename, output_mode = translate_pdf_request(data)
        except RequestValidationError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except RuntimeError as exc:
            return (
                jsonify({"status": "error", "message": explain_service_error(exc)}),
                502,
            )
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

        response = send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
        response.headers["X-PDF2ZH-Output-Mode"] = output_mode
        response.headers["X-PDF2ZH-Version"] = VERSION
        return response

    @app.post("/validate-config")
    def validate_config():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Expected a JSON body"}), 400

        try:
            result = validate_config_request(data)
        except RequestValidationError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except RuntimeError as exc:
            return (
                jsonify({"status": "error", "message": explain_service_error(exc)}),
                502,
            )
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

        return (
            jsonify(
                {
                    "status": "ok",
                    "service": result.service,
                    "model": result.model,
                }
            ),
            200,
        )

    @app.route("/tasks", methods=["GET", "POST"])
    def tasks():
        if request.method == "GET":
            return jsonify({"status": "ok", "tasks": TASK_MANAGER.list_tasks()}), 200

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Expected a JSON body"}), 400

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            temp_dir = tempfile.TemporaryDirectory(prefix="zotero-pdf2zh-next-task-")
            prepared = prepare_translation_request(data, Path(temp_dir.name))
            task = TASK_MANAGER.create_task(
                file_name=prepared.file_name,
                service=prepared.service,
                output_modes=prepared.output_modes,
                request_payload=prepared.request_payload,
                temp_dir=temp_dir,
            )
        except RequestValidationError as exc:
            if temp_dir is not None:
                temp_dir.cleanup()
            return jsonify({"status": "error", "message": str(exc)}), 400
        except Exception as exc:
            if temp_dir is not None:
                temp_dir.cleanup()
            return jsonify({"status": "error", "message": str(exc)}), 500

        return jsonify({"status": "ok", "task": task}), 202

    @app.get("/tasks/<task_id>")
    def task_detail(task_id: str):
        task = TASK_MANAGER.get_task(task_id)
        if task is None:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        return jsonify({"status": "ok", "task": task}), 200

    @app.delete("/tasks/<task_id>")
    def delete_task(task_id: str):
        try:
            task = TASK_MANAGER.delete_task(task_id)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
        if task is None:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        return jsonify({"status": "ok", "task": task}), 200

    @app.post("/tasks/<task_id>/cancel")
    def cancel_task(task_id: str):
        task = TASK_MANAGER.cancel_task(task_id)
        if task is None:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        return jsonify({"status": "ok", "task": task}), 200

    @app.post("/tasks/clear-failed")
    def clear_failed_tasks():
        deleted_count = TASK_MANAGER.clear_failed_tasks()
        return jsonify({"status": "ok", "deletedCount": deleted_count}), 200

    @app.get("/tasks/<task_id>/result")
    def task_result(task_id: str):
        requested_mode = request.args.get("mode")
        if requested_mode is not None:
            try:
                requested_mode = normalize_output_mode_value(requested_mode)
            except RequestValidationError as exc:
                return jsonify({"status": "error", "message": str(exc)}), 400

        result = TASK_MANAGER.get_result_file(task_id, requested_mode)
        if result is None:
            return jsonify({"status": "error", "message": "Task not found"}), 404

        task_record, result_file = result
        if task_record.status != "completed":
            return (
                jsonify({"status": "error", "message": "Task result is not ready"}),
                409,
            )
        if result_file is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": (
                            "Output mode is required when multiple result files exist"
                        ),
                    }
                ),
                400,
            )

        response = send_file(
            result_file.output_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=result_file.filename,
        )
        response.headers["X-PDF2ZH-Output-Mode"] = result_file.output_mode
        response.headers["X-PDF2ZH-Version"] = VERSION
        response.headers["X-PDF2ZH-Task-Id"] = task_record.task_id
        return response

    return app


def translate_pdf_request(data: dict[str, Any]) -> tuple[bytes, str, str]:
    with tempfile.TemporaryDirectory(prefix="zotero-pdf2zh-next-") as temp_dir:
        prepared = prepare_translation_request(data, Path(temp_dir))
        if len(prepared.output_modes) != 1:
            raise RequestValidationError(
                "/translate accepts exactly one output mode; use /tasks for multiple outputs"
            )

        job_id = os.urandom(4).hex()
        LOGGER.info(
            "[%s] accepted request: file=%s service=%s output_modes=%s",
            job_id,
            prepared.file_name,
            prepared.service,
            ",".join(prepared.output_modes),
        )
        result = asyncio.run(translate_pdf(prepared.request_payload, job_id))
        output_mode = prepared.output_modes[0]
        output_file = result.files[output_mode]
        return output_file.output_path.read_bytes(), output_file.filename, output_mode


def validate_config_request(data: dict[str, Any]):
    job_id = os.urandom(4).hex()
    service = normalize_service(data.get("service") or "siliconflowfree")
    request_payload = {
        "source_lang": normalize_language(data.get("sourceLang"), "en"),
        "target_lang": normalize_language(data.get("targetLang"), "zh-CN"),
        "service": service,
        "qps": parse_int(data.get("qps"), 1, minimum=1),
        "pool_size": parse_int(data.get("poolSize"), 0, minimum=0),
        "ocr": parse_bool(data.get("ocr"), False),
        "auto_ocr": parse_bool(data.get("autoOcr"), True),
        "no_watermark": parse_bool(data.get("noWatermark"), True),
        "font_family": normalize_font_family(data.get("fontFamily")),
        "llm_api": data.get("llm_api") or {},
    }
    LOGGER.info("[%s] checking config: service=%s", job_id, service)
    return validate_service_config(request_payload, job_id)


def prepare_translation_request(
    data: dict[str, Any],
    temp_path: Path,
) -> PreparedTranslationRequest:
    file_bytes = decode_pdf_content(data.get("fileContent"))
    file_name = sanitize_pdf_filename(data.get("fileName"))
    service = normalize_service(data.get("service") or "siliconflowfree")
    output_modes = normalize_output_modes(data)
    input_path = temp_path / file_name
    output_dir = temp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(file_bytes)

    request_payload = {
        "source_lang": normalize_language(data.get("sourceLang"), "en"),
        "target_lang": normalize_language(data.get("targetLang"), "zh-CN"),
        "output_modes": output_modes,
        "service": service,
        "qps": parse_int(data.get("qps"), 8, minimum=1),
        "pool_size": parse_int(data.get("poolSize"), 0, minimum=0),
        "skip_last_pages": parse_int(data.get("skipLastPages"), 0, minimum=0),
        "ocr": parse_bool(data.get("ocr"), False),
        "auto_ocr": parse_bool(data.get("autoOcr"), True),
        "no_watermark": parse_bool(data.get("noWatermark"), True),
        "font_family": normalize_font_family(data.get("fontFamily")),
        "llm_api": data.get("llm_api") or {},
        "input_path": str(input_path),
        "output_dir": str(output_dir),
    }
    return PreparedTranslationRequest(
        file_name=file_name,
        service=service,
        output_modes=output_modes,
        request_payload=request_payload,
    )


def decode_pdf_content(file_content: Any) -> bytes:
    if not isinstance(file_content, str) or not file_content.strip():
        raise RequestValidationError("fileContent is required")

    payload = file_content.strip()
    if payload.startswith("data:application/pdf;base64,"):
        payload = payload.split(",", 1)[1]

    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RequestValidationError("fileContent is not valid base64 PDF data") from exc


def sanitize_pdf_filename(file_name: Any) -> str:
    if not isinstance(file_name, str) or not file_name.strip():
        return "document.pdf"

    sanitized = Path(file_name.strip()).name
    if not sanitized.lower().endswith(".pdf"):
        sanitized += ".pdf"
    return sanitized


def normalize_output_mode(data: dict[str, Any]) -> str:
    return normalize_output_modes(data)[0]


def normalize_output_modes(data: dict[str, Any]) -> list[str]:
    output_modes = data.get("outputModes")
    if output_modes is None:
        return [normalize_single_output_mode(data)]

    if not isinstance(output_modes, list):
        raise RequestValidationError(
            "outputModes must be a list containing 'mono' and/or 'dual'"
        )

    normalized_modes: list[str] = []
    for value in output_modes:
        mode = normalize_output_mode_value(value)
        if mode not in normalized_modes:
            normalized_modes.append(mode)

    if not normalized_modes:
        raise RequestValidationError(
            "outputModes must contain at least one of 'mono' or 'dual'"
        )
    return normalized_modes


def normalize_single_output_mode(data: dict[str, Any]) -> str:
    output_mode = data.get("outputMode")
    if not output_mode:
        if parse_bool(data.get("mono"), False) and not parse_bool(data.get("dual"), True):
            output_mode = "mono"
        else:
            output_mode = "dual"

    return normalize_output_mode_value(output_mode)

 
def normalize_output_mode_value(output_mode: Any) -> str:
    if not isinstance(output_mode, str):
        raise RequestValidationError("outputMode must be 'mono' or 'dual'")

    normalized = output_mode.strip().lower()
    if normalized not in {"mono", "dual"}:
        raise RequestValidationError("outputMode must be 'mono' or 'dual'")
    return normalized


def normalize_service(service: Any) -> str:
    if not isinstance(service, str) or not service.strip():
        return "siliconflowfree"

    return service.strip().lower().replace("-", "").replace("_", "")


def normalize_language(value: Any, default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()


def normalize_font_family(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized in {"auto", "serif", "sans-serif", "script"}:
        return normalized
    return None


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def parse_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, minimum)


app = create_app()


def configure_logging() -> None:
    level_name = os.getenv("PDF2ZH_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


if __name__ == "__main__":
    configure_logging()
    host = os.getenv("PDF2ZH_HOST", "127.0.0.1")
    port = parse_int(os.getenv("PDF2ZH_PORT"), 8890, minimum=1)
    LOGGER.info("server starting on http://%s:%s", host, port)
    app.run(host=host, port=port)
