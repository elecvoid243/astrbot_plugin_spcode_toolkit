# Author: elecvoid243, 2026-07-22
# Spec: docs/superpowers/specs/2026-07-22-binary-preview-design.md §4 / §8.1
"""Tests for GET /spcode/file-binary — workspace + git ref paths."""

from __future__ import annotations

import json
import subprocess
import time as _time
from pathlib import Path
from typing import Any

import pytest
from tools.project import state as _proj_state
from tools.webapi import file_binary
from tools.webapi._helpers import (
    FILE_BINARY_MAX_BYTES,
    MIME_BY_EXT,
    ReasonCode,
)

pytestmark = pytest.mark.asyncio


def _err_data(resp: Any) -> dict[str, Any]:
    """Parse JSON envelope from an error Response.

    For ``_JSONResponseCompat`` we read ``_content`` directly (Starlette
    does not call ``render()`` until the response reaches the wire).
    Fallback parses ``body`` for raw ``Response`` instances.
    """
    content = getattr(resp, "_content", None)
    if isinstance(content, dict):
        return content.get("data", {})
    return json.loads(resp.body).get("data", {})


def _run_git(cwd: Path, args: list[str]) -> None:
    """Sync wrapper to silence ruff ASYNC221 in async test bodies."""
    subprocess.run(["git", *args], cwd=cwd, check=True)


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A minimal git worktree-style directory with sample binary + text files."""
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)

    pdf_bytes = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"
    (tmp_path / "doc.pdf").write_bytes(pdf_bytes)
    (tmp_path / "note.md").write_text("# hello", encoding="utf-8")
    # Unsupported extension
    (tmp_path / "archive.zip").write_bytes(b"PK\x03\x04")
    # Oversized file
    (tmp_path / "big.pdf").write_bytes(b"x" * (FILE_BINARY_MAX_BYTES + 1))
    # A symlink to the .pdf (skip on Windows without symlink privilege)
    try:
        (tmp_path / "link.pdf").symlink_to(tmp_path / "doc.pdf")
    except (OSError, NotImplementedError):
        pass
    # Commit baseline so git ref paths work
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _make_plugin(worktree_path: Path) -> tuple[Any, str]:
    from tests.conftest import _make_plugin

    plugin = _make_plugin()
    umo = "test:file-binary:1"
    _proj_state.put(umo, {"directory": str(worktree_path), "loaded_at": _time.time()})
    return plugin, umo


def _call(monkeypatch: pytest.MonkeyPatch, plugin: Any, **query: str) -> Any:
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    monkeypatch.setattr(web, "request", make_web_request_mock(query=query))
    return file_binary.handle(plugin)


# ─── Workspace path ─────────────────────────────────────────────────────


async def test_workspace_pdf_returns_bytes_with_etag(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="doc.pdf", umo=umo)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == MIME_BY_EXT[".pdf"]
    assert resp.headers["ETag"].startswith('W/"')
    assert b"%PDF-1.4" in resp.body


async def test_workspace_md_returns_markdown_content_type(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="note.md", umo=umo)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == MIME_BY_EXT[".md"]


async def test_workspace_zip_rejected_with_415(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="archive.zip", umo=umo)
    assert resp.status_code == 415
    assert _err_data(resp)["reason"] == ReasonCode.UNSUPPORTED_MEDIA_TYPE


async def test_workspace_oversized_returns_413(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="big.pdf", umo=umo)
    assert resp.status_code == 413
    assert _err_data(resp)["reason"] == ReasonCode.FILE_TOO_LARGE


async def test_workspace_symlink_rejected_with_415(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    if not (worktree / "link.pdf").exists():
        pytest.skip("symlink not supported on this platform")
    resp = await _call(monkeypatch, plugin, path="link.pdf", umo=umo)
    assert resp.status_code == 415
    assert _err_data(resp)["reason"] == ReasonCode.SPECIAL_FILE


# ─── Git ref path ───────────────────────────────────────────────────────


async def test_ref_path_returns_blob_bytes(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="doc.pdf", umo=umo, ref="HEAD")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == MIME_BY_EXT[".pdf"]
    assert b"%PDF-1.4" in resp.body


async def test_ref_not_found_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(
        monkeypatch,
        plugin,
        path="doc.pdf",
        umo=umo,
        ref="nonexistent-sha-abc",
    )
    assert resp.status_code == 404
    assert _err_data(resp)["reason"] == ReasonCode.REF_NOT_FOUND


async def test_ref_path_missing_file_returns_422(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(
        monkeypatch,
        plugin,
        path="nonexistent.pdf",
        umo=umo,
        ref="HEAD",
    )
    assert resp.status_code == 422
    assert _err_data(resp)["reason"] == ReasonCode.FILE_MISSING_AT_REF


# ─── ETag / 304 ────────────────────────────────────────────────────────


async def test_etag_consistency_format(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    r1 = await _call(monkeypatch, plugin, path="doc.pdf", umo=umo)
    etag = r1.headers["ETag"]
    assert etag.startswith('W/"')
    # Format: W/"<mtime_ns>-<size>"
    inner = etag[3:-1]
    parts = inner.split("-")
    assert len(parts) == 2
    assert parts[0].isdigit()
    assert parts[1].isdigit()


async def test_etag_304_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    r1 = await _call(monkeypatch, plugin, path="doc.pdf", umo=umo)
    etag = r1.headers["ETag"]

    # Second request with If-None-Match should 304
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(
            query={"path": "doc.pdf", "umo": umo}, headers={"If-None-Match": etag}
        ),
    )
    r2 = await file_binary.handle(plugin)
    assert r2.status_code == 304


# ─── Chinese filename ──────────────────────────────────────────────────


async def test_chinese_filename_content_disposition(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    pdf_bytes = b"%PDF-1.4\nfake\n%%EOF"
    (worktree / "中文文件.pdf").write_bytes(pdf_bytes)
    # Commit the new file
    _run_git(worktree, ["add", "-A"])
    _run_git(worktree, ["commit", "-q", "-m", "add chinese"])

    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="中文文件.pdf", umo=umo)
    assert resp.status_code == 200
    cd = resp.headers["Content-Disposition"]
    assert cd.startswith("inline; ")
    # RFC 5987 encoded filename
    assert "filename*=UTF-8''" in cd


# ─── Preflight failure paths ───────────────────────────────────────────


async def test_missing_umo_falls_back_to_latest_project(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, _umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="doc.pdf")
    # When umo is missing, preflight falls back to the most-recently
    # loaded project — so doc.pdf resolves and we get 200 with bytes.
    assert resp.status_code == 200
    assert b"%PDF-1.4" in resp.body


async def test_no_projects_loaded_returns_no_project_loaded(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, _umo = _make_plugin(worktree)
    _proj_state.pop("test:file-binary:1")  # ensure state is empty
    try:
        resp = await _call(monkeypatch, plugin, path="doc.pdf", umo="nobody")
        # Preflight uses webapi envelope convention: HTTP 200 with reason
        # field set to NO_PROJECT_LOADED.
        assert resp.status_code == 200
        assert _err_data(resp)["reason"] == ReasonCode.NO_PROJECT_LOADED
    finally:
        _proj_state.put(
            "test:file-binary:1",
            {"directory": str(worktree), "loaded_at": _time.time()},
        )


async def test_unsafe_path_returns_path_unsafe(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="../../../etc/passwd", umo=umo)
    assert resp.status_code == 400
    assert _err_data(resp)["reason"] == ReasonCode.PATH_UNSAFE


async def test_empty_path_returns_path_unsafe(
    monkeypatch: pytest.MonkeyPatch,
    worktree: Path,
) -> None:
    plugin, umo = _make_plugin(worktree)
    resp = await _call(monkeypatch, plugin, path="", umo=umo)
    assert resp.status_code == 400
    assert _err_data(resp)["reason"] == ReasonCode.PATH_UNSAFE
