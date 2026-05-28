from __future__ import annotations

import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from pdf2zh_next_service import _SKIP_TEXT_CHECKS
from pdf2zh_next_service import build_settings_input
from pdf2zh_next_service import install_text_check_bypass


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
        token = _SKIP_TEXT_CHECKS.set(True)
        try:
            self.assertFalse(babeldoc_high_level.check_cid_char(object()))
            self.assertFalse(ParagraphFinder.check_cid_paragraph(object(), object()))
            self.assertFalse(il_translator_llm_only.is_cid_paragraph(object()))
        finally:
            _SKIP_TEXT_CHECKS.reset(token)


if __name__ == "__main__":
    unittest.main()
