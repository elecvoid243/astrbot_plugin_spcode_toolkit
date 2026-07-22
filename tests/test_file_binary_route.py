# Author: elecvoid243, 2026-07-22
"""Verify GET /spcode/file-binary is registered in tools.webapi.ROUTES."""

from __future__ import annotations

from tools.webapi import ROUTES, file_binary


def test_file_binary_route_registered() -> None:
    """The /spcode/file-binary GET route must appear in ROUTES."""
    matches = [r for r in ROUTES if r[0] == "/spcode/file-binary"]
    assert len(matches) == 1
    route, methods, handler, _desc = matches[0]
    assert methods == ["GET"]
    assert handler is file_binary.handle


def test_file_binary_routes_called_in_order() -> None:
    """ROUTES preserves a stable order — file-binary should sit right
    after file-browser (logical grouping)."""
    paths = [r[0] for r in ROUTES]
    assert "/spcode/file-browser" in paths
    assert "/spcode/file-binary" in paths
    fb_idx = paths.index("/spcode/file-browser")
    binary_idx = paths.index("/spcode/file-binary")
    assert binary_idx == fb_idx + 1
