"""Tests for v2.22.0 conflict helpers in tools/webapi/_helpers.py.

NOTE: astrbot.api.web is unavailable in the test environment (no AstrBot runtime).
We mock it before any tools.webapi import to avoid ModuleNotFoundError.
"""

import sys
from unittest.mock import MagicMock

# Mock astrbot.api.web before any tools.webapi import triggers it
_web_mock = MagicMock()
_web_mock.JSONResponse = type("JSONResponse", (), {})
sys.modules.setdefault("astrbot.api.web", _web_mock)
sys.modules.setdefault("astrbot.api", MagicMock(web=_web_mock))

import pytest  # noqa: E402


class TestReasonCodeConflict:
    """Verify new ReasonCode constants exist and have correct string values."""

    def test_merge_conflict(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.MERGE_CONFLICT == "merge_conflict"

    def test_merge_already_up_to_date(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.MERGE_ALREADY_UP_TO_DATE == "merge_already_up_to_date"

    def test_unrelated_histories(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.UNRELATED_HISTORIES == "unrelated_histories"

    def test_cherry_pick_conflict(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.CHERRY_PICK_CONFLICT == "cherry_pick_conflict"

    def test_cherry_pick_empty(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.CHERRY_PICK_EMPTY == "cherry_pick_empty"

    def test_operation_in_progress(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.OPERATION_IN_PROGRESS == "operation_in_progress"

    def test_no_conflict_in_progress(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.NO_CONFLICT_IN_PROGRESS == "no_conflict_in_progress"

    def test_file_not_conflicted(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.FILE_NOT_CONFLICTED == "file_not_conflicted"

    def test_unresolved_conflicts_remain(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.UNRESOLVED_CONFLICTS_REMAIN == "unresolved_conflicts_remain"


class TestConflictHunk:
    """Verify ConflictHunk dataclass structure."""

    def test_fields(self):
        from tools.webapi._helpers import ConflictHunk

        hunk = ConflictHunk(
            index=0,
            start_line=12,
            end_line=18,
            ours="ours text\n",
            theirs="theirs text\n",
            base="base text\n",
            ours_label="HEAD",
            theirs_label="feature/x",
        )
        assert hunk.index == 0
        assert hunk.start_line == 12
        assert hunk.end_line == 18
        assert hunk.ours == "ours text\n"
        assert hunk.theirs == "theirs text\n"
        assert hunk.base == "base text\n"
        assert hunk.ours_label == "HEAD"
        assert hunk.theirs_label == "feature/x"

    def test_base_optional(self):
        from tools.webapi._helpers import ConflictHunk

        hunk = ConflictHunk(
            index=1,
            start_line=5,
            end_line=9,
            ours="a\n",
            theirs="b\n",
            base=None,
            ours_label="HEAD",
            theirs_label="main",
        )
        assert hunk.base is None
