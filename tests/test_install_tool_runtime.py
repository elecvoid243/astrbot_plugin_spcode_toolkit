"""Packaging checks that do not require a network download."""

import zipfile
from pathlib import Path

import pytest

from scripts.install_tool_runtime import build_runtime, extract_archive, package_runtime


def test_reject_archive_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escaped.txt", "unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        extract_archive(archive, tmp_path / "runtime")
    assert not (tmp_path / "escaped.txt").exists()


def test_offline_zip_keeps_plugin_relative_layout(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "python").mkdir(parents=True)
    (root / "python/python.exe").write_bytes(b"MZ")
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "dist/runtime.zip"
    package_runtime(root, archive)
    with zipfile.ZipFile(archive) as output:
        assert output.namelist() == [
            "runtime/manifest.json",
            "runtime/python/python.exe",
        ]


def test_builder_will_not_replace_an_unrecognized_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        build_runtime(tmp_path, tmp_path / "requirements.txt")


def test_archive_cannot_include_itself(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        package_runtime(tmp_path, tmp_path / "runtime.zip")
