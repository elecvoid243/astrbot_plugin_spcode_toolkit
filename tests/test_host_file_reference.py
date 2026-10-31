"""Verify the authenticated extension's explicit host-read contract."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.webapi import file_browser
from tools.webapi import _wrap


@pytest.mark.asyncio
async def test_host_read_requires_absolute_and_reads_current_bytes(
    tmp_path, monkeypatch
):
    """Host reads work without a project and never return a cached snapshot."""
    import astrbot.api.web as web

    path = tmp_path / "中文 notes#%.py"
    path.write_bytes(b"first\r\nsecond\r\n")
    query = {"path": str(path), "scope": "host"}
    monkeypatch.setattr(web, "request", SimpleNamespace(query=query))
    response = await file_browser.handle(SimpleNamespace())
    assert response["data"]["path"] == str(path.resolve())
    assert response["data"]["content"] == "first\nsecond\n"
    assert response["data"]["revision"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert response.headers["Cache-Control"] == "no-store"
    path.write_text("updated", encoding="utf-8")
    assert (await file_browser.handle(SimpleNamespace()))["data"][
        "content"
    ] == "updated"
    query["path"] = "relative.py"
    assert (await file_browser.handle(SimpleNamespace()))["data"][
        "reason"
    ] == "absolute_path_required"
    query["path"] = str(tmp_path)
    assert (await file_browser.handle(SimpleNamespace()))["data"][
        "reason"
    ] == "not_a_file"
    query["path"] = str(tmp_path / "missing")
    assert (await file_browser.handle(SimpleNamespace()))["data"][
        "reason"
    ] == "path_not_found"


@pytest.mark.asyncio
async def test_host_read_reports_permissions_and_bounds_a_growing_file(
    tmp_path, monkeypatch
):
    """Denied reads and growth after stat keep their actual error states."""
    path = tmp_path / "reference.py"
    path.write_bytes(b"content")
    old_stat = path.stat()
    monkeypatch.setattr(file_browser, "FILE_BROWSER_MAX_BYTES", 8)
    path.write_bytes(b"a" * 9)
    assert (
        file_browser._build_file_response(path, old_stat)["reason"] == "file_too_large"
    )
    path.write_bytes(b"allowed")

    def denied(*args, **kwargs):
        """Simulate the operating system refusing the actual open."""
        raise PermissionError(13, "Access denied")

    monkeypatch.setattr(Path, "open", denied)
    assert file_browser._build_file_response(path)["reason"] == "permission_denied"


@pytest.mark.asyncio
async def test_disabled_plugin_callback_never_reads_files():
    """Callbacks left in the host registry reject use after plugin termination."""
    response = await _wrap(file_browser.handle, SimpleNamespace(_webapi_active=False))()
    assert response == {
        "status": "error",
        "message": "spcode is disabled",
        "data": None,
    }
