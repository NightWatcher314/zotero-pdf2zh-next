#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import re
import sys

import babeldoc
import numpy
import pdf2zh_next
from rapidocr_onnxruntime import RapidOCR

import server

FORBIDDEN_DISTRIBUTIONS = {
    "babeldoc",
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
}


def canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_installed_runtime.py <version>")
    expected_version = sys.argv[1]
    installed = {
        canonicalize_name(distribution.metadata["Name"])
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }
    unexpected = installed & FORBIDDEN_DISTRIBUTIONS
    if unexpected:
        raise RuntimeError(f"Forbidden distributions installed: {sorted(unexpected)}")

    if importlib.metadata.version("zotero-pdf2zh-next") != expected_version:
        raise RuntimeError("Installed zotero-pdf2zh-next version mismatch")
    if pdf2zh_next.__version__ != "2.8.2":
        raise RuntimeError(f"Unexpected pdf2zh_next snapshot: {pdf2zh_next.__version__}")
    if babeldoc.__version__ != "0.5.24":
        raise RuntimeError(f"Unexpected BabelDOC snapshot: {babeldoc.__version__}")

    health = server.build_health_payload()
    if health["pdf2zhVersion"] != "2.8.2":
        raise RuntimeError(f"Health reports wrong pdf2zh version: {health}")
    if health["babeldocVersion"] != "0.5.24":
        raise RuntimeError(f"Health reports wrong BabelDOC version: {health}")

    image = numpy.full((32, 32, 3), 255, dtype=numpy.uint8)
    RapidOCR()(image)
    print(
        f"validated installed runtime {expected_version}: "
        f"{len(installed)} distributions"
    )


if __name__ == "__main__":
    main()
