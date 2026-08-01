#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "server"
LICENSES_DIR = SERVER_DIR / "LICENSES"


@dataclass(frozen=True)
class WheelSnapshot:
    name: str
    version: str
    package: str
    url: str
    sha256: str
    license_member: str | None
    license_name: str
    excludes: tuple[str, ...] = ()


SNAPSHOTS = (
    WheelSnapshot(
        name="pdf2zh-next",
        version="2.8.2",
        package="pdf2zh_next",
        url=(
            "https://files.pythonhosted.org/packages/2a/f5/"
            "897dbd72b2875411540502ea74e460dc222d8085f1699b2c2972acf3f3cc/"
            "pdf2zh_next-2.8.2-py3-none-any.whl"
        ),
        sha256="5416f8e65828783df9a2323893380145d30846ed7c201539f847307b8689b770",
        license_member="pdf2zh_next-2.8.2.dist-info/licenses/LICENSE",
        license_name="pdf2zh-next-AGPL-3.0.txt",
        excludes=(
            "assets",
            "gui.py",
            "gui_translation.yaml",
            "http_api.py",
            "i18n.py",
            "main.py",
        ),
    ),
    WheelSnapshot(
        name="BabelDOC",
        version="0.5.24",
        package="babeldoc",
        url=(
            "https://files.pythonhosted.org/packages/64/36/"
            "fbb911469a2c744712ba44cbd0fc88cede1ab59b89865b9f4fa4a7b141d7/"
            "babeldoc-0.5.24-py3-none-any.whl"
        ),
        sha256="8810b9d8faecbe9b3f3e41f7af1f5d83cbee060ac16c9f17aa2a81abb149c6f2",
        license_member="babeldoc-0.5.24.dist-info/licenses/LICENSE",
        license_name="BabelDOC-AGPL-3.0.txt",
        excludes=("tools",),
    ),
    WheelSnapshot(
        name="rapidocr-onnxruntime",
        version="1.4.4",
        package="rapidocr_onnxruntime",
        url=(
            "https://files.pythonhosted.org/packages/ba/12/"
            "1e5497183bdbe782dbb91bad1d0d2297dba4d2831b2652657f7517bfc6df/"
            "rapidocr_onnxruntime-1.4.4-py3-none-any.whl"
        ),
        sha256="971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf",
        license_member=None,
        license_name="RapidOCR-Apache-2.0.txt",
    ),
)

RAPIDOCR_LICENSE_URL = (
    "https://raw.githubusercontent.com/RapidAI/RapidOCR/v1.4.4/LICENSE"
)
RAPIDOCR_LICENSE_SHA256 = (
    "3e0af25fdd06aa9586ae97adb00ea927ebe5a3805ac77d2d3a81ce5f55693333"
)


def download(url: str, expected_sha256: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"SHA256 mismatch for {url}: {actual_sha256} != {expected_sha256}"
        )
    return data


def should_extract(snapshot: WheelSnapshot, member_name: str) -> bool:
    prefix = f"{snapshot.package}/"
    if not member_name.startswith(prefix) or member_name.endswith("/"):
        return False
    relative = member_name.removeprefix(prefix)
    return not any(
        relative == excluded or relative.startswith(f"{excluded}/")
        for excluded in snapshot.excludes
    )


def extract_snapshot(snapshot: WheelSnapshot, wheel_path: Path) -> bytes | None:
    destination = SERVER_DIR / snapshot.package
    if destination.exists():
        shutil.rmtree(destination)

    license_bytes: bytes | None = None
    with zipfile.ZipFile(wheel_path) as archive:
        for member in archive.infolist():
            if should_extract(snapshot, member.filename):
                target = SERVER_DIR / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
        if snapshot.license_member:
            license_bytes = archive.read(snapshot.license_member)

    init_file = destination / "__init__.py"
    if not init_file.exists():
        raise RuntimeError(f"Missing package after extraction: {init_file}")
    return license_bytes


def main() -> None:
    LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    for old_license in LICENSES_DIR.iterdir():
        if old_license.is_file():
            old_license.unlink()

    with tempfile.TemporaryDirectory(prefix="zotero-pdf2zh-vendor-") as temp_dir:
        temp_root = Path(temp_dir)
        for snapshot in SNAPSHOTS:
            wheel_bytes = download(snapshot.url, snapshot.sha256)
            wheel_path = temp_root / Path(snapshot.url).name
            wheel_path.write_bytes(wheel_bytes)
            license_bytes = extract_snapshot(snapshot, wheel_path)
            if license_bytes is not None:
                (LICENSES_DIR / snapshot.license_name).write_bytes(license_bytes)

    rapidocr_license = download(RAPIDOCR_LICENSE_URL, RAPIDOCR_LICENSE_SHA256)
    (LICENSES_DIR / "RapidOCR-Apache-2.0.txt").write_bytes(rapidocr_license)
    shutil.copyfile(
        REPO_ROOT / "LICENSE",
        LICENSES_DIR / "zotero-pdf2zh-next-AGPL-3.0-or-later.txt",
    )

    for snapshot in SNAPSHOTS:
        print(f"vendored {snapshot.name}=={snapshot.version}")


if __name__ == "__main__":
    main()
