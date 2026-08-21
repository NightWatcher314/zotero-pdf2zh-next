from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from typing import Callable

from pypdf import PdfReader

from pdf2zh_next.config.cli_env_model import CLIEnvSettingsModel
from pdf2zh_next.high_level import BabelDOCConfig
from pdf2zh_next.high_level import babeldoc_translate, create_babeldoc_config
from pdf2zh_next.translator import get_translator

LOGGER = logging.getLogger("zotero_pdf2zh_server.translate")
ProgressCallback = Callable[[dict[str, Any]], None]
ConfigReadyCallback = Callable[[BabelDOCConfig], None]
_TEXT_CHECK_TRANSLATION_LOCK = threading.Lock()
_TEXT_CHECK_PATCH_LOCK = threading.Lock()
_SKIP_TEXT_CHECKS_ENABLED = False
_TEXT_CHECK_PATCH_INSTALLED = False

SERVICE_FIELD_MAP = {
    "openai": {
        "model": "openai_model",
        "apiKey": "openai_api_key",
        "apiUrl": "openai_base_url",
    },
    "aliyundashscope": {
        "model": "aliyun_dashscope_model",
        "apiKey": "aliyun_dashscope_api_key",
        "apiUrl": "aliyun_dashscope_base_url",
    },
    "deepseek": {
        "model": "deepseek_model",
        "apiKey": "deepseek_api_key",
    },
    "ollama": {
        "model": "ollama_model",
        "apiUrl": "ollama_host",
    },
    "xinference": {
        "model": "xinference_model",
        "apiUrl": "xinference_host",
    },
    "azureopenai": {
        "model": "azure_openai_model",
        "apiKey": "azure_openai_api_key",
        "apiUrl": "azure_openai_base_url",
    },
    "modelscope": {
        "model": "modelscope_model",
        "apiKey": "modelscope_api_key",
    },
    "zhipu": {
        "model": "zhipu_model",
        "apiKey": "zhipu_api_key",
    },
    "siliconflow": {
        "model": "siliconflow_model",
        "apiKey": "siliconflow_api_key",
        "apiUrl": "siliconflow_base_url",
    },
    "gemini": {
        "model": "gemini_model",
        "apiKey": "gemini_api_key",
    },
    "azure": {
        "apiKey": "azure_api_key",
        "apiUrl": "azure_endpoint",
    },
    "anythingllm": {
        "apiKey": "anythingllm_apikey",
        "apiUrl": "anythingllm_url",
    },
    "dify": {
        "apiKey": "dify_apikey",
        "apiUrl": "dify_url",
    },
    "grok": {
        "model": "grok_model",
        "apiKey": "grok_api_key",
    },
    "groq": {
        "model": "groq_model",
        "apiKey": "groq_api_key",
    },
    "qwenmt": {
        "model": "qwenmt_model",
        "apiKey": "qwenmt_api_key",
        "apiUrl": "qwenmt_base_url",
    },
    "openaicompatible": {
        "model": "openai_compatible_model",
        "apiKey": "openai_compatible_api_key",
        "apiUrl": "openai_compatible_base_url",
    },
    "claudecode": {
        "model": "claude_code_model",
        "apiUrl": "claude_code_path",
    },
    "deepl": {
        "apiKey": "deepl_auth_key",
    },
}


def install_text_check_bypass() -> None:
    global _TEXT_CHECK_PATCH_INSTALLED
    with _TEXT_CHECK_PATCH_LOCK:
        if _TEXT_CHECK_PATCH_INSTALLED:
            return

        import babeldoc.format.pdf.high_level as babeldoc_high_level
        from babeldoc.format.pdf.document_il.midend import automatic_term_extractor
        from babeldoc.format.pdf.document_il.midend import il_translator_llm_only
        from babeldoc.format.pdf.document_il.midend import paragraph_finder
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
            ParagraphFinder,
        )
        from babeldoc.format.pdf.document_il.utils import paragraph_helper

        original_check_cid_char = babeldoc_high_level.check_cid_char
        original_check_cid_paragraph = ParagraphFinder.check_cid_paragraph
        original_is_cid_paragraph = paragraph_helper.is_cid_paragraph

        def text_checks_skipped() -> bool:
            return _SKIP_TEXT_CHECKS_ENABLED

        def check_cid_char(document):
            if text_checks_skipped():
                return False
            return original_check_cid_char(document)

        def check_cid_paragraph(self, document):
            if text_checks_skipped():
                return False
            return original_check_cid_paragraph(self, document)

        def is_cid_paragraph(paragraph):
            if text_checks_skipped():
                return False
            return original_is_cid_paragraph(paragraph)

        babeldoc_high_level.check_cid_char = check_cid_char
        ParagraphFinder.check_cid_paragraph = check_cid_paragraph
        paragraph_helper.is_cid_paragraph = is_cid_paragraph
        paragraph_finder.is_cid_paragraph = is_cid_paragraph
        automatic_term_extractor.is_cid_paragraph = is_cid_paragraph
        il_translator_llm_only.is_cid_paragraph = is_cid_paragraph
        _TEXT_CHECK_PATCH_INSTALLED = True


def set_text_checks_skipped(skip: bool) -> bool:
    global _SKIP_TEXT_CHECKS_ENABLED
    previous = _SKIP_TEXT_CHECKS_ENABLED
    _SKIP_TEXT_CHECKS_ENABLED = skip
    return previous


@dataclass(frozen=True)
class TranslationOutputFile:
    output_mode: str
    output_path: Path
    filename: str


@dataclass(frozen=True)
class TranslationResult:
    files: dict[str, TranslationOutputFile]


@dataclass(frozen=True)
class ValidationResult:
    service: str
    model: str | None
    diagnostics: list[dict[str, str]]
    live_test: dict[str, Any]
    status: str = "ok"


@dataclass(frozen=True)
class DiagnosticMessage:
    code: str
    severity: str
    message: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.suggestion:
            payload["suggestion"] = self.suggestion
        return payload


class ProgressLogger:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._last_stage: str | None = None
        self._last_logged_stage_bucket = -1
        self._last_logged_overall_bucket = -1

    def log(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")

        if event_type == "progress_start":
            stage = self._stage_name(event)
            self._last_stage = stage
            self._last_logged_stage_bucket = -1
            self._last_logged_overall_bucket = -1
            LOGGER.info(
                "[%s] stage started: %s (%s items)",
                self.job_id,
                stage,
                event.get("stage_total", "?"),
            )
            return

        if event_type == "progress_update":
            stage = self._stage_name(event)
            stage_progress = self._percentage(event.get("stage_progress"))
            overall_progress = self._percentage(event.get("overall_progress"))
            stage_bucket = int(stage_progress // 10)
            overall_bucket = int(overall_progress // 5)

            if (
                stage == self._last_stage
                and stage_bucket == self._last_logged_stage_bucket
                and overall_bucket == self._last_logged_overall_bucket
            ):
                return

            self._last_stage = stage
            self._last_logged_stage_bucket = stage_bucket
            self._last_logged_overall_bucket = overall_bucket
            LOGGER.info(
                "[%s] stage=%s stage_progress=%.1f%% overall=%.1f%% (%s/%s)",
                self.job_id,
                stage,
                stage_progress,
                overall_progress,
                event.get("stage_current", "?"),
                event.get("stage_total", "?"),
            )
            return

        if event_type == "progress_end":
            stage = self._stage_name(event)
            LOGGER.info(
                "[%s] stage finished: %s overall=%.1f%%",
                self.job_id,
                stage,
                self._percentage(event.get("overall_progress")),
            )
            return

        if event_type == "finish":
            LOGGER.info("[%s] translation finished", self.job_id)
            return

        if event_type == "error":
            LOGGER.error(
                "[%s] translation failed: %s",
                self.job_id,
                event.get("error") or "unknown error",
            )

    @staticmethod
    def _stage_name(event: dict[str, Any]) -> str:
        return str(event.get("stage") or "unknown")

    @staticmethod
    def _percentage(value: Any) -> float:
        try:
            return max(0.0, min(float(value), 100.0))
        except (TypeError, ValueError):
            return 0.0


def coerce_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in {"", "null", "none"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return stripped


def build_service_detail(service: str, llm_api: dict[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    field_map = SERVICE_FIELD_MAP.get(service, {})

    for key, field_name in field_map.items():
        value = coerce_value(llm_api.get(key))
        if value not in (None, ""):
            detail[field_name] = value

    extra_data = llm_api.get("extraData") or {}
    if isinstance(extra_data, dict):
        for key, value in extra_data.items():
            normalized_key = str(key).strip().replace("-", "_")
            normalized_value = coerce_value(value)
            if normalized_key and normalized_value not in (None, ""):
                detail[normalized_key] = normalized_value

    return detail


def build_settings_input(payload: dict[str, Any]) -> dict[str, Any]:
    input_path = Path(payload["input_path"])
    output_dir = Path(payload["output_dir"])
    output_modes = payload["output_modes"]
    service = payload["service"]

    settings_input: dict[str, Any] = {
        "translation": {
            "lang_in": payload["source_lang"],
            "lang_out": payload["target_lang"],
            "output": str(output_dir),
            "qps": max(int(payload.get("qps", 8) or 8), 1),
            "no_auto_extract_glossary": bool(
                payload.get("no_auto_extract_glossary")
            ),
        },
        "pdf": {
            "no_mono": "mono" not in output_modes,
            "no_dual": "dual" not in output_modes,
            "watermark_output_mode": (
                "no_watermark" if payload.get("no_watermark", True) else "watermarked"
            ),
            "ocr_workaround": bool(payload.get("ocr")),
            "auto_enable_ocr_workaround": bool(payload.get("auto_ocr")),
            "translate_table_text": bool(payload.get("translate_table_text", True)),
        },
        service: True,
    }

    if payload.get("pool_size"):
        settings_input["translation"]["pool_max_workers"] = int(payload["pool_size"])

    font_family = payload.get("font_family")
    if font_family and font_family != "auto":
        settings_input["translation"]["primary_font_family"] = font_family

    skip_last_pages = int(payload.get("skip_last_pages", 0) or 0)
    if skip_last_pages > 0:
        total_pages = len(PdfReader(input_path).pages)
        last_page = total_pages - skip_last_pages
        if last_page < 1:
            raise ValueError(
                f"skipLastPages={skip_last_pages} removes every page from {input_path.name}"
            )
        settings_input["pdf"]["pages"] = f"1-{last_page}"

    llm_api = payload.get("llm_api") or {}
    detail = build_service_detail(service, llm_api)
    if detail:
        settings_input[f"{service}_detail"] = detail

    return settings_input


def create_runtime_settings(payload: dict[str, Any]):
    settings_input = build_settings_input(payload)
    settings = CLIEnvSettingsModel.model_validate(settings_input).to_settings_model()
    settings.validate_settings()
    return settings


def explain_service_error(error: Exception | str) -> str:
    message = str(error).strip() if not isinstance(error, str) else error.strip()
    if not message:
        return "Unknown translation error"

    if "object has no attribute 'choices'" in message or 'object has no attribute "choices"' in message:
        return (
            "LLM 接口返回的不是标准 OpenAI Chat Completions 响应。"
            "请检查 apiUrl 或路径，确认它指向兼容的 /chat/completions 接口。"
        )

    return message


def diagnose_service_error(error: Exception | str) -> list[dict[str, str]]:
    message = explain_service_error(error)
    lowered = message.lower()

    diagnostics: list[DiagnosticMessage] = []
    if "too many cid paragraphs" in lowered or "cid" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="pdf_text_layer_cid",
                severity="warning",
                message="PDF text layer appears to contain CID-heavy encoded text.",
                suggestion=(
                    "Try enabling OCR workaround first. If the PDF renders normally "
                    "but still fails, enable skip text safety checks for this file."
                ),
            )
        )
    if "chat/completions" in lowered or "choices" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="llm_response_shape",
                severity="error",
                message="The LLM endpoint did not return an OpenAI-compatible chat response.",
                suggestion=(
                    "Check that the API URL points to a compatible /chat/completions "
                    "endpoint or use the provider-specific service type."
                ),
            )
        )
    if "401" in lowered or "unauthorized" in lowered or "api key" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="llm_auth",
                severity="error",
                message="The provider rejected the configured credentials.",
                suggestion="Check the API key, provider account status, and selected service.",
            )
        )
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="llm_rate_limit",
                severity="warning",
                message="The provider appears to be rate limiting requests.",
                suggestion="Lower QPS or pool size, then retry the task.",
            )
        )
    if "timeout" in lowered or "timed out" in lowered or "connection" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="network_connectivity",
                severity="warning",
                message="The server had trouble reaching the configured endpoint.",
                suggestion="Check the API URL, local proxy/VPN, and provider availability.",
            )
        )
    if "did not produce" in lowered or "translated pdf not found" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="output_missing",
                severity="error",
                message="pdf2zh_next finished without the requested output file.",
                suggestion="Retry with one output mode first, then check server logs if it repeats.",
            )
        )
    if "removes every page" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="invalid_page_range",
                severity="error",
                message="The skip-last-pages setting would remove the whole PDF.",
                suggestion="Reduce skipLastPages or set it to 0.",
            )
        )

    if not diagnostics:
        diagnostics.append(
            DiagnosticMessage(
                code="unknown_error",
                severity="error",
                message="The server could not classify this error automatically.",
                suggestion="Copy diagnostics and server logs when reporting the issue.",
            )
        )

    return [diagnostic.to_dict() for diagnostic in diagnostics]


def resolve_output_path(output_path: str | Path, output_dir: Path) -> Path:
    path = Path(output_path)
    if path.is_absolute():
        return path
    return output_dir / path


def collect_output_files(
    translate_result: Any,
    output_dir: Path,
    requested_output_modes: list[str],
    input_name: str,
) -> dict[str, TranslationOutputFile]:
    candidates = {
        "mono": getattr(translate_result, "mono_pdf_path", None),
        "dual": getattr(translate_result, "dual_pdf_path", None),
    }
    files: dict[str, TranslationOutputFile] = {}

    for output_mode in requested_output_modes:
        selected_path = candidates.get(output_mode)
        if not selected_path:
            raise RuntimeError(
                f"pdf2zh_next did not produce a {output_mode} PDF for {input_name}"
            )

        resolved_path = resolve_output_path(selected_path, output_dir)
        if not resolved_path.exists():
            raise FileNotFoundError(f"Translated PDF not found: {resolved_path}")

        persistent_path = output_dir / resolved_path.name
        if resolved_path != persistent_path:
            shutil.move(resolved_path, persistent_path)
            resolved_path = persistent_path

        files[output_mode] = TranslationOutputFile(
            output_mode=output_mode,
            output_path=resolved_path,
            filename=resolved_path.name,
        )

    return files


async def translate_pdf_with_callbacks(
    payload: dict[str, Any],
    job_id: str,
    progress_callback: ProgressCallback | None = None,
    on_config_ready: ConfigReadyCallback | None = None,
) -> TranslationResult:
    input_path = Path(payload["input_path"])
    output_dir = Path(payload["output_dir"])
    output_modes = payload["output_modes"]
    skip_text_checks = bool(payload.get("skip_text_checks"))

    if skip_text_checks:
        install_text_check_bypass()
        LOGGER.warning("[%s] skipping BabelDOC CID text extraction checks", job_id)

    await asyncio.to_thread(_TEXT_CHECK_TRANSLATION_LOCK.acquire)
    previous_skip_text_checks = set_text_checks_skipped(skip_text_checks)
    try:
        settings = create_runtime_settings(payload)
        translation_config = create_babeldoc_config(settings, input_path)
        if on_config_ready is not None:
            on_config_ready(translation_config)

        progress_logger = ProgressLogger(job_id)
        LOGGER.info(
            "[%s] translation started: file=%s service=%s output_modes=%s",
            job_id,
            input_path.name,
            payload["service"],
            ",".join(output_modes),
        )

        async for event in babeldoc_translate(translation_config):
            progress_logger.log(event)
            if progress_callback is not None:
                progress_callback(event)

            if event["type"] == "error":
                raise RuntimeError(
                    explain_service_error(
                        event.get("error") or "pdf2zh_next translation failed"
                    )
                )
            if event["type"] != "finish":
                continue

            result = event["translate_result"]
            files = collect_output_files(
                result,
                output_dir,
                output_modes,
                input_path.name,
            )
            LOGGER.info(
                "[%s] output ready: %s",
                job_id,
                ", ".join(file.filename for file in files.values()),
            )
            return TranslationResult(files=files)
    finally:
        set_text_checks_skipped(previous_skip_text_checks)
        _TEXT_CHECK_TRANSLATION_LOCK.release()

    raise RuntimeError("pdf2zh_next finished without producing a result")


def run_live_translator_test(translator: Any, timeout_seconds: int = 20) -> dict[str, Any]:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        translator.translate,
        "Hello",
        True,
    )
    try:
        translated = future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return {
            "enabled": True,
            "ok": False,
            "message": f"Provider test timed out after {timeout_seconds} seconds.",
        }
    except Exception as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        return {
            "enabled": True,
            "ok": False,
            "message": explain_service_error(exc),
        }
    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)

    return {
        "enabled": True,
        "ok": True,
        "message": str(translated)[:200] if translated is not None else "",
    }


def validate_service_config(payload: dict[str, Any], job_id: str) -> ValidationResult:
    with TemporaryDirectory(prefix="zotero-pdf2zh-next-check-") as temp_dir:
        output_dir = Path(temp_dir) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        validation_payload = {
            "source_lang": payload.get("source_lang", "en"),
            "target_lang": payload.get("target_lang", "zh-CN"),
            "output_modes": payload.get("output_modes") or ["dual"],
            "service": payload["service"],
            "qps": payload.get("qps", 1),
            "pool_size": payload.get("pool_size", 0),
            "skip_last_pages": 0,
            "ocr": payload.get("ocr", False),
            "auto_ocr": payload.get("auto_ocr", True),
            "translate_table_text": payload.get("translate_table_text", True),
            "no_watermark": payload.get("no_watermark", True),
            "no_auto_extract_glossary": payload.get(
                "no_auto_extract_glossary", False
            ),
            "font_family": payload.get("font_family"),
            "llm_api": payload.get("llm_api") or {},
            "input_path": str(Path(temp_dir) / "validation.pdf"),
            "output_dir": str(output_dir),
        }
        live_test_enabled = bool(payload.get("live_test", False))

        LOGGER.info(
            "[%s] validating llm config: service=%s",
            job_id,
            validation_payload["service"],
        )
        try:
            settings = create_runtime_settings(validation_payload)
            translator = get_translator(settings)
        except Exception as exc:
            raise RuntimeError(explain_service_error(exc)) from exc

        model = getattr(translator, "model", None)
        diagnostics = [
            DiagnosticMessage(
                code="config_constructed",
                severity="info",
                message="Provider configuration can be loaded by pdf2zh_next.",
            ).to_dict()
        ]
        live_test = {"enabled": live_test_enabled}
        if live_test_enabled:
            LOGGER.info(
                "[%s] running live provider test: service=%s",
                job_id,
                validation_payload["service"],
            )
            live_test = run_live_translator_test(translator)
            if live_test.get("ok"):
                diagnostics.append(
                    DiagnosticMessage(
                        code="live_test_ok",
                        severity="info",
                        message="Provider accepted a short translation request.",
                    ).to_dict()
                )
            else:
                diagnostics.extend(diagnose_service_error(live_test.get("message", "")))

        status = "warning" if live_test_enabled and not live_test.get("ok") else "ok"
        LOGGER.info(
            "[%s] llm config %s: service=%s model=%s live_test=%s",
            job_id,
            status,
            validation_payload["service"],
            model or "-",
            live_test.get("ok") if live_test_enabled else "skipped",
        )
        return ValidationResult(
            service=validation_payload["service"],
            model=model,
            diagnostics=diagnostics,
            live_test=live_test,
            status=status,
        )
