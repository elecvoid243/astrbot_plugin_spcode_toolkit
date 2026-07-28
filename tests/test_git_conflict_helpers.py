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


from unittest.mock import AsyncMock, patch  # noqa: E402


class TestDetectConflictOperation:
    """Tests for _detect_conflict_operation."""

    @pytest.mark.asyncio
    async def test_no_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result is None

    @pytest.mark.asyncio
    async def test_merge_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "merge"

    @pytest.mark.asyncio
    async def test_cherry_pick_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "CHERRY_PICK_HEAD").write_text("def456\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "cherry_pick"

    @pytest.mark.asyncio
    async def test_revert_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "REVERT_HEAD").write_text("789abc\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "revert"

    @pytest.mark.asyncio
    async def test_git_dir_relative(self, tmp_path):
        """git rev-parse --git-dir may return relative '.git'."""
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": ".git", "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "merge"

    @pytest.mark.asyncio
    async def test_git_dir_probe_fails(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": False, "stdout": "", "stderr": "fatal", "code": 128},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result is None


class TestListConflictedFiles:
    """Tests for _list_conflicted_files."""

    @pytest.mark.asyncio
    async def test_no_conflicts(self):
        from tools.webapi._helpers import _list_conflicted_files

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
        ):
            result = await _list_conflicted_files("git", "/repo")
        assert result == []

    @pytest.mark.asyncio
    async def test_two_conflicted_files(self):
        from tools.webapi._helpers import _list_conflicted_files

        porcelain = "UU src/auth.py\nAU src/config.py\n M src/ok.py\n"
        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": porcelain, "stderr": "", "code": 0},
        ):
            result = await _list_conflicted_files("git", "/repo")
        assert len(result) == 2
        assert result[0] == {"path": "src/auth.py", "status": "UU"}
        assert result[1] == {"path": "src/config.py", "status": "AU"}

    @pytest.mark.asyncio
    async def test_git_failure_returns_empty(self):
        from tools.webapi._helpers import _list_conflicted_files

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": False, "stdout": "", "stderr": "fatal", "code": 128},
        ):
            result = await _list_conflicted_files("git", "/repo")
        assert result == []


class TestReadOperationRef:
    """Tests for _read_operation_ref."""

    @pytest.mark.asyncio
    async def test_reads_merge_head(self, tmp_path):
        from tools.webapi._helpers import _read_operation_ref

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123def456\n")

        async def mock_run(args, **kwargs):
            if "log" in args:
                return {"ok": True, "stdout": "feat: add login", "stderr": "", "code": 0}
            return {"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0}

        with patch("tools.webapi._helpers._run_git_async", side_effect=mock_run):
            sha, subject = await _read_operation_ref("git", str(tmp_path), "merge")
        assert sha == "abc123def456"
        assert subject == "feat: add login"

    @pytest.mark.asyncio
    async def test_missing_sentinel_returns_empty(self, tmp_path):
        from tools.webapi._helpers import _read_operation_ref

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            sha, subject = await _read_operation_ref("git", str(tmp_path), "merge")
        assert sha == ""
        assert subject == ""

    @pytest.mark.asyncio
    async def test_unknown_operation_returns_empty(self, tmp_path):
        from tools.webapi._helpers import _read_operation_ref

        sha, subject = await _read_operation_ref("git", str(tmp_path), "rebase")
        assert sha == ""
        assert subject == ""


class TestParseConflictHunks:
    """Tests for _parse_conflict_hunks."""

    def test_standard_format_single_hunk(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "line1\n"
            "<<<<<<< HEAD\n"
            "ours content\n"
            "=======\n"
            "theirs content\n"
            ">>>>>>> feature/login\n"
            "line7\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert len(hunks) == 1
        h = hunks[0]
        assert h.index == 0
        assert h.start_line == 2
        assert h.end_line == 6
        assert h.ours == "ours content\n"
        assert h.theirs == "theirs content\n"
        assert h.base is None
        assert h.ours_label == "HEAD"
        assert h.theirs_label == "feature/login"

    def test_diff3_format(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "<<<<<<< HEAD\n"
            "ours\n"
            "||||||| merged common ancestors\n"
            "base\n"
            "=======\n"
            "theirs\n"
            ">>>>>>> branch-b\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert len(hunks) == 1
        h = hunks[0]
        assert h.ours == "ours\n"
        assert h.base == "base\n"
        assert h.theirs == "theirs\n"
        assert h.ours_label == "HEAD"
        assert h.theirs_label == "branch-b"

    def test_multiple_hunks(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "a\n"
            "<<<<<<< HEAD\n"
            "o1\n"
            "=======\n"
            "t1\n"
            ">>>>>>> b\n"
            "mid\n"
            "<<<<<<< HEAD\n"
            "o2\n"
            "=======\n"
            "t2\n"
            ">>>>>>> b\n"
            "end\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert len(hunks) == 2
        assert hunks[0].index == 0
        assert hunks[0].ours == "o1\n"
        assert hunks[1].index == 1
        assert hunks[1].ours == "o2\n"
        assert hunks[1].start_line == 8

    def test_no_conflicts(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        assert _parse_conflict_hunks("clean file\nno markers\n") == []

    def test_empty_content(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        assert _parse_conflict_hunks("") == []

    def test_binary_content_returns_empty(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        assert _parse_conflict_hunks("abc\x00def") == []

    def test_multiline_ours_theirs(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "<<<<<<< HEAD\n"
            "line1\n"
            "line2\n"
            "=======\n"
            "line3\n"
            "line4\n"
            "line5\n"
            ">>>>>>> other\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert hunks[0].ours == "line1\nline2\n"
        assert hunks[0].theirs == "line3\nline4\nline5\n"


class TestClassifyMergeStderr:
    """Tests for _classify_merge_stderr."""

    def test_conflict(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert (
            _classify_merge_stderr("CONFLICT (content): Merge conflict in f.py")
            == ReasonCode.MERGE_CONFLICT
        )

    def test_unrelated_histories(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert (
            _classify_merge_stderr("fatal: refusing to merge unrelated histories")
            == ReasonCode.UNRELATED_HISTORIES
        )

    def test_unknown_revision(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert (
            _classify_merge_stderr("merge: bogus - not something we can merge")
            == ReasonCode.REF_NOT_FOUND
        )

    def test_local_changes(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert (
            _classify_merge_stderr("error: Your local changes would be overwritten")
            == ReasonCode.WORKTREE_DIRTY
        )

    def test_already_up_to_date_in_stdout(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert (
            _classify_merge_stderr("", stdout="Already up to date.")
            == ReasonCode.MERGE_ALREADY_UP_TO_DATE
        )

    def test_generic_error(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("fatal: something weird") == ReasonCode.GIT_ERROR


class TestClassifyCherryPickStderr:
    """Tests for _classify_cherry_pick_stderr."""

    def test_conflict(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert (
            _classify_cherry_pick_stderr(
                "CONFLICT (content): Merge conflict in x.py"
            )
            == ReasonCode.CHERRY_PICK_CONFLICT
        )

    def test_empty(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert (
            _classify_cherry_pick_stderr("The previous cherry-pick is now empty")
            == ReasonCode.CHERRY_PICK_EMPTY
        )

    def test_bad_object(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert (
            _classify_cherry_pick_stderr("fatal: bad object abc123")
            == ReasonCode.COMMIT_NOT_FOUND
        )

    def test_dirty(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert (
            _classify_cherry_pick_stderr(
                "error: Your local changes would be overwritten"
            )
            == ReasonCode.WORKTREE_DIRTY
        )

    def test_generic(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert _classify_cherry_pick_stderr("fatal: unknown") == ReasonCode.GIT_ERROR
