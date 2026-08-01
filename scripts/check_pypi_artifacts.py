#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

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
REQUIRED_RUNTIME_FILES = {
    "pdf2zh_next/__init__.py",
    "babeldoc/__init__.py",
    "babeldoc/pdfminer/cmap/UniGB-UCS2-H.pickle.gz",
    "rapidocr_onnxruntime/__init__.py",
    "rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx",
    "rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx",
}
REQUIRED_LICENSE_FILES = {
    "BabelDOC-AGPL-3.0.txt",
    "pdf2zh-next-AGPL-3.0.txt",
    "RapidOCR-Apache-2.0.txt",
    "zotero-pdf2zh-next-AGPL-3.0-or-later.txt",
}
FORBIDDEN_RUNTIME_FILES = {
    "pdf2zh_next/gui.py",
    "pdf2zh_next/gui_translation.yaml",
    "pdf2zh_next/http_api.py",
    "pdf2zh_next/i18n.py",
}
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    if not match:
        raise RuntimeError(f"Cannot parse requirement: {requirement}")
    return canonicalize_name(match.group(0))


def check_wheel(wheel: Path, version: str) -> None:
    if wheel.stat().st_size > MAX_ARTIFACT_BYTES:
        raise RuntimeError(f"Wheel is unexpectedly large: {wheel.stat().st_size} bytes")

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise RuntimeError(f"Expected one dist-info METADATA, found {metadata_files}")
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))

    if metadata["Version"] != version:
        raise RuntimeError(f"Wheel version {metadata['Version']} != {version}")
    if metadata["License-Expression"] != "AGPL-3.0-or-later":
        raise RuntimeError("Wheel is missing AGPL-3.0-or-later license metadata")

    requirements = {
        requirement_name(value) for value in metadata.get_all("Requires-Dist", [])
    }
    unexpected = requirements & FORBIDDEN_DISTRIBUTIONS
    if unexpected:
        raise RuntimeError(f"Wheel declares forbidden dependencies: {sorted(unexpected)}")

    missing_runtime = REQUIRED_RUNTIME_FILES - names
    if missing_runtime:
        raise RuntimeError(f"Wheel is missing runtime files: {sorted(missing_runtime)}")
    unexpected_runtime = FORBIDDEN_RUNTIME_FILES & names
    if unexpected_runtime:
        raise RuntimeError(
            f"Wheel contains upstream frontend files: {sorted(unexpected_runtime)}"
        )

    for license_name in REQUIRED_LICENSE_FILES:
        if not any(name.endswith(f"/licenses/LICENSES/{license_name}") for name in names):
            raise RuntimeError(f"Wheel is missing license: {license_name}")
    if not any(name.endswith("/licenses/THIRD_PARTY_NOTICES.md") for name in names):
        raise RuntimeError("Wheel is missing THIRD_PARTY_NOTICES.md")


def check_sdist(sdist: Path, version: str) -> None:
    if sdist.stat().st_size > MAX_ARTIFACT_BYTES:
        raise RuntimeError(f"sdist is unexpectedly large: {sdist.stat().st_size} bytes")

    prefix = f"zotero_pdf2zh_next-{version}/"
    with tarfile.open(sdist, "r:gz") as archive:
        names = set(archive.getnames())

    required = {
        f"{prefix}{name}" for name in REQUIRED_RUNTIME_FILES
    } | {
        f"{prefix}LICENSES/{name}" for name in REQUIRED_LICENSE_FILES
    } | {
        f"{prefix}THIRD_PARTY_NOTICES.md",
        f"{prefix}pyproject.toml",
    }
    missing = required - names
    if missing:
        raise RuntimeError(f"sdist is missing files: {sorted(missing)}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_pypi_artifacts.py <dist-dir> <version>")
    dist_dir = Path(sys.argv[1])
    version = sys.argv[2]
    wheel = dist_dir / f"zotero_pdf2zh_next-{version}-py3-none-any.whl"
    sdist = dist_dir / f"zotero_pdf2zh_next-{version}.tar.gz"
    check_wheel(wheel, version)
    check_sdist(sdist, version)
    print(f"validated PyPI artifacts for {version}")


if __name__ == "__main__":
    main()
