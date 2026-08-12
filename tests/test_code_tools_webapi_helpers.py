"""Tests for shared code-check/code-format Web API helpers."""

from tools.webapi._helpers import ReasonCode


def test_validate_code_path_rejects_control_chars():
    from tools.webapi._code_tools import _validate_code_path

    assert _validate_code_path("src/a.py") is None
    assert _validate_code_path("") == ReasonCode.INVALID_PARAM
    assert _validate_code_path("a\nb.py") == ReasonCode.INVALID_PARAM
    assert _validate_code_path("x" * 513) == ReasonCode.INVALID_PARAM
    assert _validate_code_path(None) == ReasonCode.INVALID_BODY


def test_tool_details_omit_ok():
    from tools.webapi._code_tools import _tool_details

    assert _tool_details({"ok": False, "error": "x", "proposal": "p"}) == {
        "error": "x",
        "proposal": "p",
    }


def test_tool_failure_maps_common_backend_errors():
    from tools.webapi._code_tools import _tool_failure

    assert _tool_failure({"error": "文件不存在: a.py"}, ReasonCode.CHECK_FAILED) == (
        ReasonCode.FILE_NOT_FOUND,
        "文件不存在: a.py",
    )
    assert (
        _tool_failure({"error": "不支持的扩展名: .txt"}, ReasonCode.CHECK_FAILED)[0]
        == ReasonCode.UNSUPPORTED_MEDIA_TYPE
    )
    assert (
        _tool_failure({"error": "ruff 未安装"}, ReasonCode.CHECK_FAILED)[0]
        == ReasonCode.TOOL_UNAVAILABLE
    )
    assert (
        _tool_failure({"error": "unknown"}, ReasonCode.CHECK_FAILED)[0]
        == ReasonCode.CHECK_FAILED
    )
