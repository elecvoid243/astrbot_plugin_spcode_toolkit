"""Host management binds explicit paths and never falls back to permanent deletion."""

from pathlib import Path

import pytest

from tools.webapi import host_file_manage as manage

pytestmark = pytest.mark.asyncio


async def test_outside_project_rename_and_collision(tmp_path: Path):
    source = tmp_path / "F05 unique source.txt"
    source.write_bytes(b"content")
    result = (
        await manage.handle(
            None,
            body={"path": str(source), "operation": "rename", "new_name": "new.txt"},
        )
    )["data"]
    assert result["renamed"] and result["new_path"] == str(tmp_path / "new.txt")
    assert not source.exists()
    source.write_bytes(b"other")
    result = (
        await manage.handle(
            None,
            body={"path": str(source), "operation": "rename", "new_name": "new.txt"},
        )
    )["data"]
    assert result["reason"] == "file_exists"
    assert source.read_bytes() == b"other"
    assert (tmp_path / "new.txt").read_bytes() == b"content"


@pytest.mark.parametrize(
    "path,operation,name",
    [
        ("relative", "trash", None),
        ("relative", "rename", "x"),
        ("ABS", "rename", "../escape"),
        ("ABS", "rename", ""),
        ("ABS", "copy", "x"),
    ],
)
async def test_rejects_invalid_requests(tmp_path, path, operation, name):
    source = tmp_path / "F05 unique.txt"
    source.write_text("keep")
    result = (
        await manage.handle(
            None,
            body={
                "path": str(source) if path == "ABS" else path,
                "operation": operation,
                "new_name": name,
            },
        )
    )["data"]
    assert not result["renamed"] and not result["deleted"]
    assert result["reason"]
    assert source.read_text() == "keep"


@pytest.mark.parametrize(
    "failure",
    [PermissionError("denied"), OSError("trash unavailable"), ImportError("missing")],
)
async def test_trash_failure_never_unlinks(tmp_path, monkeypatch, failure):
    source = tmp_path / "F05 unique preserve.txt"
    source.write_bytes(b"keep")

    def fail(path):
        raise failure

    def forbidden(*args, **kwargs):
        pytest.fail("permanent-delete fallback")

    monkeypatch.setattr(manage, "trash_file", fail)
    monkeypatch.setattr(Path, "unlink", forbidden)
    result = (
        await manage.handle(None, body={"path": str(source), "operation": "trash"})
    )["data"]
    assert not result["deleted"] and result["reason"]
    assert source.read_bytes() == b"keep"


async def test_directory_missing_and_context_rejected(tmp_path):
    for body in [
        {"path": str(tmp_path)},
        {"path": str(tmp_path / "missing")},
        {"path": str(tmp_path), "worktree": str(tmp_path)},
    ]:
        result = (await manage.handle(None, body={"operation": "trash", **body}))[
            "data"
        ]
        assert not result["deleted"] and result["reason"]


async def test_trash_uses_existing_dependency(tmp_path, monkeypatch):
    import send2trash

    source = tmp_path / "F05 unique trash.txt"
    source.write_text("restore")
    calls = []

    def fake(path):
        calls.append(path)
        Path(path).rename(tmp_path / "simulated-trash.txt")

    monkeypatch.setattr(manage.sys, "platform", "linux")
    monkeypatch.setattr(send2trash, "send2trash", fake)
    result = (
        await manage.handle(None, body={"path": str(source), "operation": "trash"})
    )["data"]
    assert result["deleted"] and result["trashed"]
    assert calls == [str(source)]
    assert (tmp_path / "simulated-trash.txt").read_text() == "restore"


@pytest.mark.parametrize("module_name", ["file_remove", "docs_crud"])
async def test_legacy_trash_failure_preserves_file(tmp_path, monkeypatch, module_name):
    from importlib import import_module
    from tests.conftest import _make_plugin

    module = import_module("tools.webapi." + module_name)
    source = tmp_path / "F05 unique legacy.md"
    source.write_bytes(b"keep")

    async def preflight(*args, **kwargs):
        return None, {"directory": str(tmp_path), "umo": "u:m"}

    def fail(path):
        raise OSError("system trash unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("permanent-delete fallback")

    monkeypatch.setattr(module, "_git_endpoint_preflight", preflight)
    monkeypatch.setattr(module, "trash_file", fail)
    monkeypatch.setattr(Path, "unlink", forbidden)
    handler = (
        module.handle if module_name == "file_remove" else module.handle_delete_docs
    )
    result = await handler(_make_plugin(), umo="u:m", body={"path": source.name})
    assert result["data"]["reason"] == "trash_failed"
    assert source.read_bytes() == b"keep"


async def test_rename_permission_failure_preserves_target(tmp_path, monkeypatch):
    source = tmp_path / "F05 unique source.txt"
    source.write_bytes(b"keep")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(manage.sys, "platform", "win32")
    monkeypatch.setattr(Path, "rename", denied)
    result = (
        await manage.handle(
            None,
            body={"path": str(source), "operation": "rename", "new_name": "new.txt"},
        )
    )["data"]
    assert result["reason"] == "permission_denied"
    assert source.read_bytes() == b"keep"
    assert not (tmp_path / "new.txt").exists()


@pytest.mark.parametrize("module_name", ["file_rename", "file_remove", "docs_crud"])
async def test_old_project_contract_still_rejects_outside_targets(
    tmp_path, monkeypatch, module_name
):
    from importlib import import_module
    from tests.conftest import _make_plugin

    module = import_module("tools.webapi." + module_name)
    repository = tmp_path / "repository"
    repository.mkdir()
    source = tmp_path / "F05 unique legacy.md"
    source.write_bytes(b"keep")

    async def preflight(*args, **kwargs):
        return None, {"directory": str(repository), "umo": "u:m"}

    monkeypatch.setattr(module, "_git_endpoint_preflight", preflight)
    handler = module.handle if module_name != "docs_crud" else module.handle_delete_docs
    result = await handler(
        _make_plugin(), umo="u:m", body={"path": str(source), "new_name": "other.md"}
    )
    assert result["data"]["reason"] == "path_unsafe"
    assert source.read_bytes() == b"keep"
