from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from pdf2zh_next_service import build_settings_input
from pdf2zh_next_service import collect_output_files
from pdf2zh_next_service import create_runtime_settings
from pdf2zh_next_service import diagnose_service_error
from pdf2zh_next_service import install_text_check_bypass
from pdf2zh_next_service import run_live_translator_test
from pdf2zh_next_service import set_text_checks_skipped


class PDF2zhNextServiceTests(unittest.TestCase):
    def test_collect_output_files_moves_absolute_result_into_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated_path = root / "translated.pdf"
            generated_path.write_bytes(b"pdf")
            output_dir = root / "workspace" / "output"
            output_dir.mkdir(parents=True)

            files = collect_output_files(
                SimpleNamespace(
                    mono_pdf_path=None,
                    dual_pdf_path=generated_path,
                ),
                output_dir,
                ["dual"],
                "paper.pdf",
            )

            persisted_path = output_dir / generated_path.name
            self.assertEqual(files["dual"].output_path, persisted_path)
            self.assertEqual(persisted_path.read_bytes(), b"pdf")
            self.assertFalse(generated_path.exists())

    def test_build_settings_input_nests_translation_settings(self) -> None:
        settings_input = build_settings_input(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
                "no_auto_extract_glossary": True,
            }
        )

        self.assertTrue(settings_input["translation"]["no_auto_extract_glossary"])

    def test_build_settings_input_nests_pdf_settings(self) -> None:
        settings_input = build_settings_input(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
            }
        )

        self.assertTrue(settings_input["pdf"]["translate_table_text"])

    def test_build_settings_input_can_skip_table_text(self) -> None:
        settings_input = build_settings_input(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
                "translate_table_text": False,
            }
        )

        self.assertFalse(settings_input["pdf"]["translate_table_text"])

    def test_create_runtime_settings_preserves_nested_translation_and_pdf_options(
        self,
    ) -> None:
        settings = create_runtime_settings(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
                "qps": 8,
                "pool_size": 3,
                "font_family": "sans-serif",
                "ocr": False,
                "auto_ocr": True,
                "translate_table_text": False,
                "no_watermark": True,
                "no_auto_extract_glossary": True,
                "llm_api": {
                    "model": "gpt-4o-mini",
                    "apiKey": "test-key",
                    "apiUrl": "https://example.com/v1",
                },
            }
        )

        self.assertEqual(settings.translation.output, "/tmp/output")
        self.assertEqual(settings.translation.lang_in, "en")
        self.assertEqual(settings.translation.lang_out, "zh-CN")
        self.assertEqual(settings.translation.qps, 8)
        self.assertEqual(settings.translation.pool_max_workers, 3)
        self.assertEqual(settings.translation.primary_font_family, "sans-serif")
        self.assertTrue(settings.translation.no_auto_extract_glossary)
        self.assertTrue(settings.pdf.no_mono)
        self.assertFalse(settings.pdf.no_dual)
        self.assertEqual(settings.pdf.watermark_output_mode, "no_watermark")
        self.assertFalse(settings.pdf.ocr_workaround)
        self.assertTrue(settings.pdf.auto_enable_ocr_workaround)
        self.assertFalse(settings.pdf.translate_table_text)

        manual_settings = create_runtime_settings(
            {
                "input_path": "/tmp/paper.pdf",
                "output_dir": "/tmp/output",
                "output_modes": ["dual"],
                "source_lang": "en",
                "target_lang": "zh-CN",
                "service": "openai",
                "ocr": True,
                "auto_ocr": False,
                "llm_api": {"apiKey": "test-key"},
            }
        )
        self.assertTrue(manual_settings.pdf.ocr_workaround)
        self.assertFalse(manual_settings.pdf.auto_enable_ocr_workaround)

    def test_text_check_bypass_skips_cid_checks_in_context(self) -> None:
        import babeldoc.format.pdf.high_level as babeldoc_high_level
        from babeldoc.format.pdf.document_il.midend import il_translator_llm_only
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
            ParagraphFinder,
        )

        install_text_check_bypass()
        previous = set_text_checks_skipped(True)
        try:
            self.assertFalse(babeldoc_high_level.check_cid_char(object()))
            self.assertFalse(ParagraphFinder.check_cid_paragraph(object(), object()))
            self.assertFalse(il_translator_llm_only.is_cid_paragraph(object()))
        finally:
            set_text_checks_skipped(previous)

    def test_text_check_bypass_reaches_executor_threads(self) -> None:
        import babeldoc.format.pdf.high_level as babeldoc_high_level

        install_text_check_bypass()
        previous = set_text_checks_skipped(True)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertFalse(
                    executor.submit(
                        babeldoc_high_level.check_cid_char,
                        object(),
                    ).result()
                )
        finally:
            set_text_checks_skipped(previous)

    def test_diagnose_service_error_classifies_openai_response_shape(self) -> None:
        diagnostics = diagnose_service_error("object has no attribute 'choices'")

        self.assertEqual(diagnostics[0]["code"], "llm_response_shape")
        self.assertEqual(diagnostics[0]["severity"], "error")

    def test_live_translator_test_returns_success(self) -> None:
        class Translator:
            def translate(self, text, ignore_cache=False, rate_limit_params=None):
                return f"{text} translated"

        result = run_live_translator_test(Translator(), timeout_seconds=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Hello translated")

    def test_live_translator_test_returns_diagnostic_message_on_error(self) -> None:
        class Translator:
            def translate(self, text, ignore_cache=False, rate_limit_params=None):
                raise RuntimeError("401 Unauthorized")

        result = run_live_translator_test(Translator(), timeout_seconds=1)

        self.assertFalse(result["ok"])
        self.assertIn("401", result["message"])


if __name__ == "__main__":
    unittest.main()
