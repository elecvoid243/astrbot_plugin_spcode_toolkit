"""Exercise native safety boundaries without invoking destructive native calls."""

import ctypes
import errno
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.webapi import host_file_manage as manage

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("route", ["host", "file_remove", "docs_crud"])
@pytest.mark.parametrize(
    "signal",
    [
        "missing_backend",
        "unsafe_drive",
        "old_system",
        "force_nuke",
        "too_large",
        "no_recycle",
        "aborted",
        "failed_result",
        "no_recycled_item",
        "failed_item",
        "no_post",
        "success",
        "success_info",
    ],
)
async def test_windows_native_trash_signals(tmp_path, monkeypatch, route, signal):
    pythoncom = pytest.importorskip("pythoncom")
    import win32file
    from win32com.client import Dispatch
    from win32com.shell import shell
    from tests.conftest import _make_plugin

    source = tmp_path / "F05 marked native boundary.md"
    source.write_bytes(b"preserve exact bytes")
    native_calls = []
    operations = []

    class Operation:
        def SetOperationFlags(self, flags):
            assert flags & 0x00080000  # Mandatory RECYCLEONDELETE, not ALLOWUNDO.
            assert not flags & 0x40
            self.flags = flags

        def DeleteItem(self, item, sink):
            self.sink = pythoncom.UnwrapObject(sink)
            self.item = item
            native_calls.append("queued")

        def PerformOperations(self):
            if signal in {"force_nuke", "too_large"}:
                return 0x80270036 if signal == "force_nuke" else 0x80270037
            flags = 0 if signal == "no_recycle" else 0x80
            if signal == "no_recycle":
                # Use a valid IDispatch gateway to check the raised COM HRESULT;
                # IFileOperationProgressSink itself has no Python client wrapper.
                gateway = Dispatch(
                    pythoncom.WrapObject(self.sink, pythoncom.IID_IDispatch)
                )
                try:
                    gateway.PreDeleteItem(flags, None)
                except pythoncom.com_error as exc:
                    assert exc.hresult == -2147467260 or (
                        exc.excepinfo and exc.excepinfo[-1] == -2147467260
                    )
                    native_calls.append("cancelled_hresult")
                    raise
            else:
                self.sink.PreDeleteItem(flags, self.item)
            native_calls.append("past_predelete")
            if signal in {"success", "success_info"}:
                destination = tmp_path / "simulated-native-recycle.md"
                source.rename(destination)
                recycled = shell.SHCreateItemFromParsingName(
                    str(destination), None, shell.IID_IShellItem
                )
            else:
                recycled = (
                    self.item
                    if signal in {"aborted", "failed_result", "failed_item"}
                    else None
                )
            if signal != "no_post":
                self.sink.PostDeleteItem(
                    flags,
                    self.item,
                    -2147467259
                    if signal == "failed_item"
                    else 0x00270008
                    if signal == "success_info"
                    else 0,
                    recycled,
                )
            if signal in {"aborted", "failed_result", "failed_item"}:
                exists = Path.exists
                monkeypatch.setattr(
                    Path,
                    "exists",
                    lambda path: False if path == source else exists(path),
                )
            return 1 if signal == "failed_result" else 0

        def GetAnyOperationsAborted(self):
            return signal == "aborted"

    def create(*args):
        native_calls.append("created")
        op = Operation()
        operations.append(op)
        return op

    def forbidden(*args, **kwargs):
        pytest.fail("legacy/permanent-delete fallback invoked")

    monkeypatch.setattr(pythoncom, "CoCreateInstance", create)
    monkeypatch.setattr(
        win32file, "GetDriveType", lambda path: 2 if signal == "unsafe_drive" else 3
    )
    import send2trash

    monkeypatch.setattr(send2trash, "send2trash", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    if signal == "old_system":
        monkeypatch.setattr(sys, "getwindowsversion", lambda: (6, 1))
    if signal == "missing_backend":
        monkeypatch.setitem(sys.modules, "pythoncom", None)
    if route == "host":
        result = await manage.handle(
            None, body={"path": str(source), "operation": "trash"}
        )
    else:
        module = import_module("tools.webapi." + route)

        async def preflight(*args, **kwargs):
            return None, {"directory": str(tmp_path), "umo": "u:m"}

        monkeypatch.setattr(module, "_git_endpoint_preflight", preflight)
        handler = module.handle if route == "file_remove" else module.handle_delete_docs
        result = await handler(_make_plugin(), umo="u:m", body={"path": source.name})
    if signal in {"success", "success_info"}:
        assert result["data"]["deleted"]
        assert not source.exists()
        assert (
            tmp_path / "simulated-native-recycle.md"
        ).read_bytes() == b"preserve exact bytes"
    else:
        assert not result["data"].get("deleted")
        assert result["data"]["reason"]
        assert source.read_bytes() == b"preserve exact bytes"
    if signal in {"missing_backend", "unsafe_drive", "old_system"}:
        assert not native_calls
    if signal == "no_recycle":
        assert native_calls == ["created", "queued", "cancelled_hresult"]
        assert operations[0].sink.predelete and operations[0].sink.refused


@pytest.mark.parametrize("platform", ["linux", "darwin"])
@pytest.mark.parametrize("error", [errno.EEXIST, errno.ENOSYS, errno.ENOTSUP])
async def test_exclusive_posix_rename_boundary(tmp_path, monkeypatch, platform, error):
    source = tmp_path / "F05 source.txt"
    source.write_bytes(b"source bytes")
    destination = tmp_path / "arrives.txt"
    calls = []

    class ExclusiveRename:
        def __call__(self, *args):
            calls.append(args)
            assert args[-1] == (1 if platform == "linux" else 4)
            if error == errno.EEXIST:
                # Another process creates the target after the caller's exists check.
                destination.write_bytes(b"other writer bytes")
            ctypes.set_errno(error)
            return -1

    def forbidden(*args, **kwargs):
        pytest.fail("replace-capable rename fallback invoked")

    monkeypatch.setattr(manage.sys, "platform", platform)
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda *args, **kwargs: SimpleNamespace(
            renameat2=ExclusiveRename(), renamex_np=ExclusiveRename()
        ),
    )
    monkeypatch.setattr(Path, "rename", forbidden)
    result = (
        await manage.handle(
            None,
            body={
                "path": str(source),
                "operation": "rename",
                "new_name": destination.name,
            },
        )
    )["data"]
    assert len(calls) == 1
    assert result["reason"] == (
        "file_exists" if error == errno.EEXIST else "operation_failed"
    )
    assert not result["renamed"]
    assert source.read_bytes() == b"source bytes"
    if error == errno.EEXIST:
        assert destination.read_bytes() == b"other writer bytes"
    else:
        assert not destination.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native rename semantics")
async def test_windows_destination_appears_after_precheck(tmp_path, monkeypatch):
    source = tmp_path / "F05 source.txt"
    source.write_bytes(b"source bytes")
    destination = tmp_path / "arrives.txt"
    rename = Path.rename

    def concurrent_destination(path, target):
        destination.write_bytes(b"other writer bytes")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", concurrent_destination)
    result = (
        await manage.handle(
            None,
            body={
                "path": str(source),
                "operation": "rename",
                "new_name": destination.name,
            },
        )
    )["data"]
    assert result["reason"] == "file_exists"
    assert source.read_bytes() == b"source bytes"
    assert destination.read_bytes() == b"other writer bytes"


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd"])
async def test_missing_exclusive_rename_backend_fails_before_mutation(
    tmp_path, monkeypatch, platform
):
    source = tmp_path / "F05 preserved.txt"
    source.write_bytes(b"source bytes")

    def forbidden(*args, **kwargs):
        pytest.fail("replace-capable rename fallback invoked")

    monkeypatch.setattr(manage.sys, "platform", platform)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(Path, "rename", forbidden)
    result = (
        await manage.handle(
            None,
            body={"path": str(source), "operation": "rename", "new_name": "new.txt"},
        )
    )["data"]
    assert result["reason"] == "operation_failed"
    assert source.read_bytes() == b"source bytes"
    assert not (tmp_path / "new.txt").exists()
