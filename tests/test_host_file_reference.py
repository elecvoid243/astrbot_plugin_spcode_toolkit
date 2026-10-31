"""Verify the authenticated extension's explicit host-read contract."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools.webapi import _wrap, file_browser


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
    assert (await file_browser.handle(SimpleNamespace()))["data"]["type"] == "directory"
    query["path"] = str(tmp_path / "missing")
    assert (await file_browser.handle(SimpleNamespace()))["data"][
        "reason"
    ] == "path_not_found"


@pytest.mark.asyncio
async def test_host_directory_returns_resolved_entries_and_explicit_limits(
    tmp_path, monkeypatch
):
    """Host directory reads use the existing bounded listing without a project.

    Args:
        tmp_path: Isolated real filesystem directory.
        monkeypatch: Fixture for the request and listing limit.
    """
    import astrbot.api.web as web

    folder = tmp_path / "中文 folder"
    folder.mkdir()
    (folder / "child").mkdir()
    (folder / "one.txt").write_text("one", encoding="utf-8")
    (folder / "two.txt").write_text("two", encoding="utf-8")
    monkeypatch.setattr(
        web,
        "request",
        SimpleNamespace(query={"scope": "host", "path": str(folder / "..")}),
    )
    response = await file_browser.handle(SimpleNamespace())
    assert response["data"]["path"] == str(tmp_path.resolve())
    assert response.headers["Cache-Control"] == "no-store"
    monkeypatch.setattr(file_browser, "FILE_BROWSER_MAX_ENTRIES", 2)
    web.request.query["path"] = str(folder)
    listing = (await file_browser.handle(SimpleNamespace()))["data"]
    assert listing["truncated"] is True
    assert listing["max_entries"] == 2
    assert len(listing["entries"]) == 2
    assert all(Path(entry["path"]).is_absolute() for entry in listing["entries"])

    def denied(*args, **kwargs):
        """Simulate an operating system denial when listing the directory."""
        raise PermissionError(13, "Access denied")

    monkeypatch.setattr(Path, "iterdir", denied)
    assert (await file_browser.handle(SimpleNamespace()))["data"][
        "reason"
    ] == "permission_denied"


@pytest.mark.asyncio
async def test_directory_listing_ignores_special_entries(tmp_path, monkeypatch):
    """Keep a directory browsable when it contains a socket or device.

    Args:
        tmp_path: Isolated real filesystem directory.
        monkeypatch: Fixture for the platform-specific entry classification.
    """
    (tmp_path / "normal.txt").write_text("text", encoding="utf-8")
    special = tmp_path / "socket"
    special.touch()
    original = file_browser._make_entry
    monkeypatch.setattr(
        file_browser,
        "_make_entry",
        lambda path: {"type": "special"} if path == special else original(path),
    )
    listing = file_browser._build_directory_response(tmp_path)
    assert [entry["name"] for entry in listing["entries"]] == ["normal.txt"]


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
