from __future__ import annotations

import unittest
from pathlib import Path

import tomllib

SERVER_DIR = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_project_metadata_omits_external_core_and_frontend_packages(self) -> None:
        project = tomllib.loads((SERVER_DIR / "pyproject.toml").read_text())["project"]
        dependency_names = {
            dependency.split("[", 1)[0]
            .split("<", 1)[0]
            .split(">", 1)[0]
            .split("=", 1)[0]
            .split("!", 1)[0]
            .lower()
            for dependency in project["dependencies"]
        }

        self.assertTrue(
            {
                "babeldoc",
                "fastapi",
                "gradio",
                "opencv-python",
                "pandas",
                "pdf2zh-next",
                "rapidocr-onnxruntime",
                "ruff",
                "uvicorn",
            }.isdisjoint(dependency_names)
        )

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
                "opencv-python",
                "pandas",
                "pdf2zh-next",
                "pydantic-settings",
                "rapidocr-onnxruntime",
                "ruff",
                "sse-starlette",
                "uvicorn",
            }.isdisjoint(package_names)
        )

    def test_vendored_runtime_versions_are_fixed(self) -> None:
        import babeldoc
        import pdf2zh_next

        self.assertEqual(pdf2zh_next.__version__, "2.8.2")
        self.assertEqual(babeldoc.__version__, "0.5.24")
        self.assertFalse((SERVER_DIR / "pdf2zh_next/gui.py").exists())
        self.assertTrue(
            (
                SERVER_DIR
                / "rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
