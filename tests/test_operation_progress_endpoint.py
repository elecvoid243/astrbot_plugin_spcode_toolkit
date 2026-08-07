"""tests/test_operation_progress_endpoint.py — GET /spcode/operation-progress。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from tools import operation_progress as prog
from tools.webapi.operation_progress import handle


@pytest.fixture(autouse=True)
def _clean() -> None:
    prog._records.clear()


def test_missing_umo_rejected():
    result = asyncio.run(handle(MagicMock(), umo=None))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


def test_idle_when_no_record():
    result = asyncio.run(handle(MagicMock(), umo="u1"))
    assert result["data"]["success"] is True
    assert result["data"]["status"] == "idle"


def test_running_record_returned():
    prog.begin("u1", "project_load")
    prog.append("u1", "⏳ [1/3] init")
    result = asyncio.run(handle(MagicMock(), umo="u1"))
    data = result["data"]
    assert data["status"] == "running"
    assert data["operation"] == "project_load"
    assert data["current_step"] == "⏳ [1/3] init"
    assert data["messages"] == ["⏳ [1/3] init"]
    assert data["finished_at"] is None
