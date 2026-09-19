from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf2zh_next_service import configure_request_retries, create_runtime_settings
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
from server import parse_retry_options, prepare_translation_request, validate_config_request, RequestValidationError


class RequestRetryTests(unittest.TestCase):
    def make_translator(self, statuses):
        settings = create_runtime_settings({
            "input_path": "/tmp/paper.pdf", "output_dir": "/tmp/output",
            "output_modes": ["dual"], "source_lang": "en", "target_lang": "zh-CN",
            "service": "openai", "llm_api": {"apiKey": "test", "model": "test"},
        })
        translator = OpenAITranslator(settings, None)
        translator.client.close()
        self.calls = []
        def handle(request):
            self.calls.append(request)
            status = statuses[min(len(self.calls) - 1, len(statuses) - 1)]
            if status == "timeout":
                raise httpx.ReadTimeout("test timeout", request=request)
            body = ({"choices": [{"message": {"role": "assistant", "content": "译文"}}]}
                    if status == 200 else {"error": {"message": "test error"}})
            return httpx.Response(status, json=body)
        translator.client = openai.OpenAI(
            api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handle))
        )
        self.addCleanup(translator.client.close)
        return translator

    def test_transient_errors_retry_exactly_without_sdk_multiplication(self):
        for status in (408, 409, 429, 500, 503, "timeout"):
            with self.subTest(status=status):
                translator = self.make_translator([status])
                configure_request_retries(translator, {"retry_count": 2, "retry_interval": 0})
                with self.assertRaises(openai.APIError):
                    translator.do_llm_translate("text")
                self.assertEqual(len(self.calls), 3)

    def test_success_after_retry_for_both_entrypoints(self):
        for method in ("do_translate", "do_llm_translate"):
            translator = self.make_translator([500, 200])
            configure_request_retries(translator, {"retry_count": 2, "retry_interval": 7})
            sleep_calls = []
            getattr(translator, method).retry.sleep = sleep_calls.append
            self.assertEqual(getattr(translator, method)("text"), "译文")
            self.assertEqual(len(self.calls), 2)
            self.assertEqual(sleep_calls, [7])

    def test_zero_disables_both_layers(self):
        translator = self.make_translator([429])
        configure_request_retries(translator, {"retry_count": 0})
        with self.assertRaises(openai.RateLimitError):
            translator.do_llm_translate("text")
        self.assertEqual(len(self.calls), 1)

    def test_permanent_errors_are_not_retried(self):
        for status in (400, 401, 403, 404):
            translator = self.make_translator([status])
            configure_request_retries(translator, {"retry_count": 3, "retry_interval": 0})
            with self.assertRaises(openai.APIStatusError):
                translator.do_llm_translate("text")
            self.assertEqual(len(self.calls), 1)

    def test_defaults_leave_engine_unchanged(self):
        translator = self.make_translator([200])
        method = translator.do_llm_translate
        configure_request_retries(translator, {})
        self.assertEqual(translator.do_llm_translate, method)
        self.assertEqual(translator.client.max_retries, 2)

    def test_cancel_interrupts_retry_wait(self):
        translator = self.make_translator([429])
        def cancelled():
            raise RuntimeError("cancelled")
        configure_request_retries(translator, {"retry_count": 3, "retry_interval": 300}, cancelled)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            translator.do_llm_translate("text")
        self.assertEqual(len(self.calls), 1)

    def test_unsupported_engine_rejects_custom_policy(self):
        configure_request_retries(object(), {})
        with self.assertRaisesRegex(ValueError, "OpenAI SDK"):
            configure_request_retries(object(), {"retry_count": 2})

    def test_request_validation(self):
        self.assertEqual(parse_retry_options({}), {"retry_count": -1, "retry_interval": 2})
        self.assertEqual(parse_retry_options({"retryCount": "0", "retryInterval": "5"}),
                         {"retry_count": 0, "retry_interval": 5})
        for values in ({"retryCount": -2}, {"retryCount": 101}, {"retryInterval": -1},
                       {"retryInterval": 301}, {"retryCount": 1.5}, {"retryCount": "bad"}):
            with self.subTest(values=values), self.assertRaises(RequestValidationError):
                parse_retry_options(values)

    def test_task_payload_preserves_retry_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            result = prepare_translation_request({
                "fileName": "paper.pdf",
                "fileContent": base64.b64encode(b"%PDF-1.4\n").decode(),
                "service": "openai", "retryCount": "4", "retryInterval": "6",
            }, Path(directory))
        self.assertEqual(result.request_payload["retry_count"], 4)
        self.assertEqual(result.request_payload["retry_interval"], 6)

    def test_config_check_receives_retry_settings(self):
        with patch("server.validate_service_config") as validate:
            validate_config_request({"service": "openai", "retryCount": "0", "retryInterval": "8"})
        payload = validate.call_args.args[0]
        self.assertEqual(payload["retry_count"], 0)
        self.assertEqual(payload["retry_interval"], 8)
