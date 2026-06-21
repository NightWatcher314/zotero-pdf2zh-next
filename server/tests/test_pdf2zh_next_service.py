from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from pdf2zh_next_service import build_settings_input
from pdf2zh_next_service import diagnose_service_error
from pdf2zh_next_service import install_text_check_bypass
from pdf2zh_next_service import run_live_translator_test
from pdf2zh_next_service import set_text_checks_skipped


class PDF2zhNextServiceTests(unittest.TestCase):
    def test_build_settings_input_disables_auto_extract_glossary(self) -> None:
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

        self.assertTrue(settings_input["no_auto_extract_glossary"])

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
