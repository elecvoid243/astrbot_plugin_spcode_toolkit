"""Manual host saves use real bytes and never change Agent project state."""

import hashlib
import os
from pathlib import Path

import pytest

from tools.webapi import host_file_write
from tools.webapi.file_browser import _build_file_response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,content,expected",
    [
        (b"old\r\n", "new\n", b"new\r\n"),
        (b"\xef\xbb\xbfold\n", "new\n", b"\xef\xbb\xbfnew\n"),
        ("旧\r\n".encode("gbk"), "新\n", "新\r\n".encode("gbk")),
        (b"old\r", "new\n", b"new\r"),
    ],
)
async def test_save_preserves_format_without_project(
    tmp_path: Path, raw: bytes, content: str, expected: bytes
) -> None:
    target = tmp_path / "outside.txt"
    target.write_bytes(raw)
    revision = _build_file_response(target)["revision"]
    result = await host_file_write.handle(
        None, body={"path": str(target), "content": content, "base_revision": revision}
    )
    assert result["data"]["saved"] is True
    assert target.read_bytes() == expected
    assert result["data"]["revision"] == hashlib.sha256(expected).hexdigest()
    assert not list(tmp_path.glob(".spcode-save-*"))


@pytest.mark.asyncio
async def test_conflict_and_confirmation_are_revision_checked(tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_bytes(b"one")
    body = {
        "path": str(target),
        "content": "draft",
        "base_revision": hashlib.sha256(b"one").hexdigest(),
    }
    target.write_bytes(b"two")
    conflict = (await host_file_write.handle(None, body=body))["data"]
    assert conflict["reason"] == "conflict"
    assert target.read_bytes() == b"two"
    body["base_revision"] = conflict["revision"]
    target.write_bytes(b"three")
    conflict = (await host_file_write.handle(None, body=body))["data"]
    assert conflict["reason"] == "conflict"
    assert target.read_bytes() == b"three"
    body["base_revision"] = conflict["revision"]
    assert (await host_file_write.handle(None, body=body))["data"]["saved"]
    assert target.read_bytes() == b"draft"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["encoding", "write", "missing", "permission", "second_check"]
)
async def test_failed_save_keeps_original_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    target = tmp_path / "outside.txt"
    raw = "旧\r\n".encode("gbk")
    target.write_bytes(raw)
    body = {
        "path": str(target),
        "content": "新",
        "base_revision": hashlib.sha256(raw).hexdigest(),
    }
    if failure == "encoding":
        body["content"] = "😀"
    elif failure == "write":
        monkeypatch.setattr(
            host_file_write.os,
            "fsync",
            lambda _: (_ for _ in ()).throw(OSError("disk full")),
        )
    elif failure == "missing":
        target.unlink()
    elif failure == "permission":
        if os.name != "nt":
            pytest.skip("Real Windows read-only attribute exercise")
        target.chmod(0o444)
    else:
        fsync = host_file_write.os.fsync

        def external_write(fd: int) -> None:
            fsync(fd)
            target.write_bytes(b"external during staging")

        monkeypatch.setattr(host_file_write.os, "fsync", external_write)
    try:
        result = (await host_file_write.handle(None, body=body))["data"]
        assert result["saved"] is False
        assert (
            result["reason"]
            == {
                "encoding": "encoding_error",
                "write": "write_failed",
                "missing": "path_not_found",
                "permission": "permission_denied",
                "second_check": "conflict",
            }[failure]
        )
        if failure != "missing":
            assert target.read_bytes() == (
                b"external during staging" if failure == "second_check" else raw
            )
        assert not list(tmp_path.glob(".spcode-save-*"))
    finally:
        if target.exists():
            target.chmod(0o666)


@pytest.mark.asyncio
async def test_relative_paths_and_project_parameters_rejected(tmp_path: Path) -> None:
    for body in [
        {},
        {"path": "relative", "content": "x", "base_revision": "a" * 64},
        {
            "path": str(tmp_path),
            "content": "x",
            "base_revision": "a" * 64,
            "worktree": str(tmp_path),
        },
    ]:
        assert not (await host_file_write.handle(None, body=body))["data"]["saved"]


@pytest.mark.asyncio
async def test_session_format_survives_ascii_without_newlines(tmp_path: Path) -> None:
    target = tmp_path / "legacy.txt"
    target.write_bytes("旧内容\r\n".encode("gbk"))
    snapshot = _build_file_response(target)
    body = {
        "path": str(target),
        "content": "ASCII",
        "base_revision": snapshot["revision"],
        "encoding": snapshot["encoding"],
        "newline": snapshot["newline"],
    }
    result = (await host_file_write.handle(None, body=body))["data"]
    assert result["saved"]
    body.update(content="旧内容\n", base_revision=result["revision"])
    assert (await host_file_write.handle(None, body=body))["data"]["saved"]
    assert target.read_bytes() == "旧内容\r\n".encode("gbk")


@pytest.mark.asyncio
async def test_format_hint_cannot_reinterpret_disk_text(tmp_path: Path) -> None:
    target = tmp_path / "utf8.txt"
    target.write_bytes("中文\n".encode("utf-8"))
    result = await host_file_write.handle(
        None,
        body={
            "path": str(target),
            "content": "你好\n",
            "base_revision": _build_file_response(target)["revision"],
            "encoding": "latin-1",
            "newline": "\r\n",
        },
    )
    assert result["data"]["saved"]
    assert target.read_bytes() == "你好\n".encode("utf-8")
    assert result["data"]["encoding"] == "utf-8"
    assert result["data"]["newline"] == "\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("disk_bom", [False, True])
async def test_confirmed_overwrite_uses_current_disk_bom(
    tmp_path: Path, disk_bom: bool
) -> None:
    target = tmp_path / "bom.txt"
    original = b"old\n" if disk_bom else b"\xef\xbb\xbfold\n"
    target.write_bytes(original)
    snapshot = _build_file_response(target)
    body = {
        "path": str(target),
        "content": "draft\n",
        "base_revision": snapshot["revision"],
        "encoding": snapshot["encoding"],
    }
    target.write_bytes((b"\xef\xbb\xbf" if disk_bom else b"") + b"external\n")
    conflict = (await host_file_write.handle(None, body=body))["data"]
    assert conflict["reason"] == "conflict"
    body["base_revision"] = conflict["revision"]
    saved = (await host_file_write.handle(None, body=body))["data"]
    assert saved["saved"]
    assert target.read_bytes() == (b"\xef\xbb\xbf" if disk_bom else b"") + b"draft\n"
