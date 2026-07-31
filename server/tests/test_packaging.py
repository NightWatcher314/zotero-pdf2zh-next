from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_lock_uses_public_pypi_only(self) -> None:
        lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text())
        registries = {
            package["source"]["registry"]
            for package in lock["package"]
            if "registry" in package["source"]
        }

        self.assertEqual(registries, {"https://pypi.org/simple"})

    def test_lock_omits_unused_pdf2zh_frontends(self) -> None:
        lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text())
        package_names = {package["name"] for package in lock["package"]}

        self.assertTrue(
            {
                "fastapi",
                "gradio",
                "gradio-i18n",
                "gradio-pdf",
                "legacy-cgi",
                "pydantic-settings",
                "sse-starlette",
                "uvicorn",
            }.isdisjoint(package_names)
        )


if __name__ == "__main__":
    unittest.main()
