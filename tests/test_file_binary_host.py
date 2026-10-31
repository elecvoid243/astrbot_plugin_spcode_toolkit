"""Explicit host binary reads preserve project and Git path contracts."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from tools.webapi import _wrap, file_binary
from tools.webapi._helpers import MIME_BY_EXT

from tests.conftest import make_web_request_mock

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("suffix", list(MIME_BY_EXT))
async def test_host_bytes_without_project(monkeypatch, tmp_path, suffix):
    """Read current host bytes with the existing MIME whitelist."""
    from astrbot.api import web

    target = tmp_path / ("中文 space#%" + suffix)
    target.write_bytes(b"first disk version")
    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(
            query={
                "scope": "host",
                "path": str(target),
            }
        ),
    )
    plugin = SimpleNamespace()
    response = await _wrap(file_binary.handle, plugin)()
    assert response.status_code == 200
    assert response.body == target.read_bytes()
    assert response.headers["content-type"] == MIME_BY_EXT[suffix]
    assert response.headers["cache-control"] == "no-store"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    target.write_bytes(b"second disk version")
    response = await _wrap(file_binary.handle, plugin)()
    assert response.body == b"second disk version"


@pytest.mark.parametrize(
    "query,status,reason",
    [
        ({"path": "relative.pdf"}, 400, "absolute_path_required"),
        ({"path": "missing.pdf"}, 404, "path_not_found"),
        ({"path": "archive.zip"}, 415, "unsupported_media_type"),
        ({"path": "big.pdf"}, 413, "file_too_large"),
        ({"path": "doc.pdf", "ref": "HEAD"}, 400, "invalid_param"),
        ({"path": "doc.pdf", "worktree": "elsewhere"}, 400, "invalid_param"),
    ],
)
async def test_host_errors(monkeypatch, tmp_path, query, status, reason):
    """Reject ambiguous contracts and unsupported or oversized files."""
    from astrbot.api import web

    (tmp_path / "archive.zip").write_bytes(b"zip")
    (tmp_path / "doc.pdf").write_bytes(b"pdf")
    with (tmp_path / "big.pdf").open("wb") as stream:
        stream.truncate(file_binary.FILE_BINARY_MAX_BYTES + 1)
    if query["path"] != "relative.pdf":
        query = {**query, "path": str(tmp_path / query["path"])}
    monkeypatch.setattr(
        web, "request", make_web_request_mock(query={"scope": "host", **query})
    )
    response = await _wrap(file_binary.handle, SimpleNamespace())()
    assert response.status_code == status
    assert response["data"]["reason"] == reason


async def test_host_permission_failure(monkeypatch, tmp_path):
    """Report a filesystem permission denial without returning bytes."""
    from astrbot.api import web

    target = tmp_path / "doc.pdf"
    target.write_bytes(b"pdf")
    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(query={"scope": "host", "path": str(target)}),
    )

    def denied(*args, **kwargs):
        raise PermissionError("denied test boundary")

    monkeypatch.setattr(Path, "open", denied)
    response = await _wrap(file_binary.handle, SimpleNamespace())()
    assert response.status_code == 403
    assert response["data"]["reason"] == "permission_denied"
