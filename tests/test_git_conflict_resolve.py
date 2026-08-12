"""Unit tests for POST /spcode/git-conflict-resolve."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch


# Mock astrbot.api.web before any tools.webapi import triggers it
class _FakeJSONResponse:
    """Minimal stand-in for astrbot.api.web.JSONResponse in tests."""

    def __init__(self, content=None, status_code=200, headers=None):
        self._content = content
        self.status_code = status_code
        self.headers = headers or {}

    def __getitem__(self, key):
        return self._content[key]

    def get(self, key, default=None):
        return self._content.get(key, default)


_web_mock = MagicMock()
_web_mock.JSONResponse = _FakeJSONResponse
sys.modules.setdefault("astrbot.api.web", _web_mock)
sys.modules.setdefault("astrbot.api", MagicMock(web=_web_mock))

import pytest  # noqa: E402


class TestRebuildFileFromHunks:
    def test_all_hunks_resolved(self):
        from tools.webapi._helpers import ConflictHunk
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks

        content = "line1\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> dev\nline7\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(
                index=0,
                start_line=2,
                end_line=6,
                ours="ours\n",
                theirs="theirs\n",
                base=None,
                ours_label="HEAD",
                theirs_label="dev",
            )
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "theirs"})
        assert result == "line1\ntheirs\nline7\n"

    def test_partial_resolution_returns_none(self):
        from tools.webapi._helpers import ConflictHunk
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks

        content = "<<<<<<< HEAD\no1\n=======\nt1\n>>>>>>> b\nmid\n<<<<<<< HEAD\no2\n=======\nt2\n>>>>>>> b\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(
                index=0,
                start_line=1,
                end_line=5,
                ours="o1\n",
                theirs="t1\n",
                base=None,
                ours_label="HEAD",
                theirs_label="b",
            ),
            ConflictHunk(
                index=1,
                start_line=7,
                end_line=11,
                ours="o2\n",
                theirs="t2\n",
                base=None,
                ours_label="HEAD",
                theirs_label="b",
            ),
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "ours"})
        assert result is None

    def test_base_choice(self):
        from tools.webapi._helpers import ConflictHunk
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks

        content = "<<<<<<< HEAD\nours\n||||||| base\nbaseval\n=======\ntheirs\n>>>>>>> dev\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(
                index=0,
                start_line=1,
                end_line=7,
                ours="ours\n",
                theirs="theirs\n",
                base="baseval\n",
                ours_label="HEAD",
                theirs_label="dev",
            )
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "base"})
        assert result == "baseval\n"


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestResolveBodyValidation:
    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(_make_plugin(), body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_file_and_all_both_provided(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(), body={"file": "a.py", "all": True, "resolution": "ours"}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_neither_file_nor_all(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(_make_plugin(), body={"resolution": "ours"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_hunks_and_resolution_mutual_exclusion(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={
                "file": "a.py",
                "hunks": [{"index": 0, "choice": "ours"}],
                "resolution": "theirs",
            },
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_all_with_custom_forbidden(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={"all": True, "resolution": "custom", "content": "x"},
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_all_with_hunks_forbidden(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={"all": True, "hunks": [{"index": 0, "choice": "ours"}]},
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_custom_without_content(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(), body={"file": "a.py", "resolution": "custom"}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_content_too_large(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={
                "file": "a.py",
                "resolution": "custom",
                "content": "x" * (1024 * 1024 + 1),
            },
        )
        assert result["data"]["reason"] == "invalid_param"


class TestResolveExecution:
    @pytest.mark.asyncio
    async def test_no_conflict_in_progress(self):
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"file": "a.py", "resolution": "ours"})
        assert result["data"]["reason"] == "no_conflict_in_progress"

    @pytest.mark.asyncio
    async def test_file_not_conflicted(self):
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[{"path": "other.py", "status": "UU"}],
            ),
            patch(
                "tools.webapi.git_conflict_resolve._validate_repo_relative_file",
                return_value=(MagicMock(), None),
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            result = await handle(plugin, body={"file": "a.py", "resolution": "ours"})
        assert result["data"]["reason"] == "file_not_conflicted"

    @pytest.mark.asyncio
    async def test_batch_all_ours(self):
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_resolve._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            # First call returns conflicted, second call (after resolve) returns empty
            mock_list.side_effect = [
                [{"path": "a.py", "status": "UU"}, {"path": "b.py", "status": "AU"}],
                [],
            ]
            result = await handle(plugin, body={"all": True, "resolution": "ours"})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["resolved"] is True
        assert data["mode"] == "all"
        assert data["files_resolved"] == 2
        assert data["all_resolved"] is True


# ── v2.23.2 (2026-08-11) custom 模式保持原文件编码 ──
#
# 报告:git-conflict-resolve Mode 3 (custom) 写盘固定 UTF-8,GBK 冲突文件
# 解决后会被强转。修复:检测冲突文件原编码 + 主导换行,按原格式写回
# (复用 tools/_helpers._encode_content,与 file-write / docs POST 一致)。
#
# Author: elecvoid243, 2026-08-11


class TestCustomResolutionEncodingPreserved:
    @pytest.mark.asyncio
    async def test_custom_resolution_preserves_gbk_encoding(self, tmp_path):
        """custom 解决 GBK 冲突文件后仍保持 GBK(不被强转 UTF-8)。"""
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        target = tmp_path / "gbk.py"
        target.write_bytes("// 中文\nint x=1;\n".encode("gbk"))

        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._validate_repo_relative_file",
                return_value=(target, None),
            ),
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_resolve._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
        ):
            mock_pf.return_value = (
                None,
                {
                    "directory": str(tmp_path),
                    "umo": "u1",
                    "worktree": str(tmp_path),
                },
            )
            mock_detect.return_value = "merge"
            mock_list.return_value = [{"path": "gbk.py", "status": "UU"}]

            result = await handle(
                plugin,
                body={
                    "file": "gbk.py",
                    "resolution": "custom",
                    "content": "// 新中文\nint y=2;\n",
                },
            )
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["resolved"] is True
        assert data["mode"] == "custom"

        raw = target.read_bytes()
        # 保持 GBK 字节(容错 Windows 换行):等于按 GBK 编码的新内容
        assert raw.replace(b"\r\n", b"\n") == "// 新中文\nint y=2;\n".encode("gbk"), (
            f"应保持 GBK 编码;实得前 24 字节={raw[:24]!r}"
        )
        assert "新中文" in raw.decode("gbk")

    @pytest.mark.asyncio
    async def test_custom_resolution_preserves_utf8_bom(self, tmp_path):
        """custom 解决 UTF-8 BOM 冲突文件后仍保留 BOM。"""
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        target = tmp_path / "bom.py"
        target.write_bytes(b"\xef\xbb\xbfint x=1;\n")

        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._validate_repo_relative_file",
                return_value=(target, None),
            ),
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_resolve._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
        ):
            mock_pf.return_value = (
                None,
                {
                    "directory": str(tmp_path),
                    "umo": "u1",
                    "worktree": str(tmp_path),
                },
            )
            mock_detect.return_value = "merge"
            mock_list.return_value = [{"path": "bom.py", "status": "UU"}]

            result = await handle(
                plugin,
                body={
                    "file": "bom.py",
                    "resolution": "custom",
                    "content": "int y=2;\n",
                },
            )
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["resolved"] is True

        raw = target.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM 丢失"
        assert raw.replace(b"\r\n", b"\n") == b"\xef\xbb\xbfint y=2;\n"

    @pytest.mark.asyncio
    async def test_custom_content_not_encodable_returns_invalid_param(self, tmp_path):
        """custom 内容无法用原编码表示 → invalid_param,原文件不变。"""
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        target = tmp_path / "a.py"
        # GBK 源文件(含中文,使解码链检测为 GBK):韩文无法用 GBK 表示
        target.write_bytes("旧内容\n".encode("gbk"))

        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._validate_repo_relative_file",
                return_value=(target, None),
            ),
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
        ):
            mock_pf.return_value = (
                None,
                {
                    "directory": str(tmp_path),
                    "umo": "u1",
                    "worktree": str(tmp_path),
                },
            )
            mock_detect.return_value = "merge"
            mock_list.return_value = [{"path": "a.py", "status": "UU"}]

            result = await handle(
                plugin,
                body={
                    "file": "a.py",
                    "resolution": "custom",
                    "content": "한국어\n",  # GBK 无法表示
                },
            )
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["resolved"] is False
        assert data["reason"] == "invalid_param"
        # 原文件未被改写
        assert target.read_bytes() == "旧内容\n".encode("gbk")
