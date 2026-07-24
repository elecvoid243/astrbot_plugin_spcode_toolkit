# file-write 保存格式保持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `POST /spcode/file-write`，使已有文本文件保存后保持原字符编码、UTF-8 BOM 和主导换行格式，同时保持新建文件为 UTF-8 无 BOM + LF。

**Architecture:** 在 `file_write.py` 内增加一个不可变文件格式数据结构以及编码/换行探测和编码辅助函数。handler 在覆盖已有文件前读取原始字节并探测格式，在内存中完成新内容编码后才写盘；请求协议、路径安全和 upsert 流程不变。

**Tech Stack:** Python 3.10+、AstrBot Web API、pytest、ruff、Git worktree

**Author:** elecvoid243
**时间戳:** 2026-07-24 16:44 CST

## Global Constraints

- 工作目录固定为 `F:\github\astrbot_plugin_spcode_toolkit\.worktrees\file-write-preserve-format`。
- Python 固定使用 `D:\anaconda3\envs\astrbot\python.exe`。
- 不修改 `/spcode/file-write` 请求体字段和路由。
- 已有文件保留当前读取链支持的编码：UTF-8、UTF-8 BOM、CP936、GBK、GB18030、Latin-1。
- 已有文件保留主导换行；数量相同时按 CRLF > LF > CR 选择。
- 新文件继续使用 UTF-8 无 BOM + LF。
- 请求内容大小仍按 UTF-8 字节计算且不超过 2 MB。
- 路径必须继续经过 `_git_endpoint_preflight` 和 `_validate_repo_relative_file`。
- 修改任何生产实现前，新增回归测试必须在旧实现上按预期失败。
- 只进行本地 commit，禁止 push 和 PR。
- 实现前完整基线为 `1446 passed, 6 skipped, 2 failed`；已知失败仅为：
  - `tests/test_git_branches.py::test_branches_etag_changes_after_upstream_track_change`（测试环境初始化分支不是 `main`）；
  - `tests/test_vivado_e2e.py::TestVivadoE2E::test_vivado_mcp_imports`（当前 `vivado_mcp` 包无 `server` 属性）。
- 本次完成标准是 file-write 相关测试全部通过，且完整套件不得出现上述两项之外的新失败。

---

## File Map

- Modify: `tests/test_file_write.py` — 新增保存格式回归测试并强化现有 LF/新建文件不变量。
- Modify: `tools/webapi/file_write.py` — 探测原编码/换行、按原格式编码并写回。
- Reference only: `tools/webapi/file_browser.py` — 复用 `_decode_text_bytes(raw) -> tuple[str, str]` 的解码顺序和 UTF-8 BOM 语义。
- Reference: `docs/superpowers/specs/2026-07-24-file-write-preserve-format-design.md`。

---

### Task 1: 用 TDD 实现已有文件格式保持

**Files:**
- Modify: `tests/test_file_write.py`
- Modify: `tools/webapi/file_write.py`

**Interfaces:**
- Consumes: `file_browser._decode_text_bytes(raw: bytes) -> tuple[str, str]`。
- Produces: `_TextFileFormat(encoding: str, newline: str)`。
- Produces: `_detect_newline(text: str) -> str`。
- Produces: `_detect_text_format(raw: bytes) -> _TextFileFormat`。
- Produces: `_encode_content(content: str, file_format: _TextFileFormat) -> bytes`。
- Preserves: `async def handle(plugin, *, umo=None, worktree=None, body=None) -> dict`。

- [ ] **Step 1: 添加会在旧实现上失败的端点回归测试**

在 `tests/test_file_write.py` 的 happy-path 区域追加以下测试：

```python
async def test_preserves_utf8_crlf(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "main.cpp"
    target.write_bytes(b"// old\r\nint main() { return 0; }\r\n")
    _load_project(plugin, "u:m", str(tmp_path))

    content = "// new\nint main() { return 1; }\n"
    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "main.cpp", "content": content},
    )

    assert result["data"]["saved"] is True
    assert target.read_bytes() == content.replace("\n", "\r\n").encode("utf-8")


async def test_preserves_utf8_bom(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "bom.cpp"
    target.write_bytes(b"\xef\xbb\xbf" + "// 旧内容\n".encode("utf-8"))
    _load_project(plugin, "u:m", str(tmp_path))

    content = "// 新内容\n"
    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "bom.cpp", "content": content},
    )

    assert result["data"]["saved"] is True
    assert target.read_bytes() == b"\xef\xbb\xbf" + content.encode("utf-8")


async def test_preserves_gbk_crlf(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "legacy.cpp"
    target.write_bytes("// 旧内容\r\n".encode("gbk"))
    _load_project(plugin, "u:m", str(tmp_path))

    content = "// 新内容\nint value = 1;\n"
    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "legacy.cpp", "content": content},
    )

    assert result["data"]["saved"] is True
    expected = content.replace("\n", "\r\n")
    assert target.read_bytes().decode("gbk") == expected
    with pytest.raises(UnicodeDecodeError):
        target.read_bytes().decode("utf-8")


async def test_mixed_line_endings_use_dominant_style(
    plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "mixed.txt"
    target.write_bytes(b"a\r\nb\r\nc\n")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "mixed.txt", "content": "x\ny\n"},
    )

    assert result["data"]["saved"] is True
    assert target.read_bytes() == b"x\r\ny\r\n"


async def test_tied_line_endings_prefer_crlf(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "tie.txt"
    target.write_bytes(b"a\r\nb\n")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "tie.txt", "content": "x\ny\n"},
    )

    assert result["data"]["saved"] is True
    assert target.read_bytes() == b"x\r\ny\r\n"


async def test_unencodable_content_does_not_overwrite_original(
    plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "latin1.txt"
    original = "Café\r\n".encode("latin-1")
    target.write_bytes(original)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "latin1.txt", "content": "你好\n"},
    )

    assert result["data"]["reason"] == "invalid_param"
    assert result["data"]["saved"] is False
    assert target.read_bytes() == original
```

同时强化两个已有测试：

```python
# test_overwrites_existing_code_file 末尾
assert (tmp_path / "src" / "main.py").read_bytes() == b"print('new')\n"

# test_creates_missing_file 末尾
assert (tmp_path / ".gitignore").read_bytes() == b".codegraph/\n"
```

- [ ] **Step 2: 运行新增回归测试并确认 RED**

Run:

```text
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/test_file_write.py -v -k "preserves_utf8_crlf or preserves_utf8_bom or preserves_gbk_crlf or mixed_line_endings or tied_line_endings or unencodable_content"
```

Expected: 6 个新增测试均 FAIL；失败原因分别是 CRLF/BOM/GBK 未保持、混合换行被写成 LF，以及 Latin-1 文件被错误覆盖。不得在看到预期失败前修改生产代码。

- [ ] **Step 3: 增加文件格式数据结构和纯辅助函数**

在 `tools/webapi/file_write.py` 中添加导入：

```python
from dataclasses import dataclass

from .file_browser import _decode_text_bytes
```

在常量下方添加：

```python
@dataclass(frozen=True)
class _TextFileFormat:
    """已有文本文件需要保持的字符编码和主导换行格式。"""

    encoding: str
    newline: str


_DEFAULT_TEXT_FILE_FORMAT = _TextFileFormat(encoding="utf-8", newline="\n")


def _detect_newline(text: str) -> str:
    """返回文本主导换行；数量相同时按 CRLF、LF、CR 的顺序选择。"""
    crlf_count = text.count("\r\n")
    without_crlf = text.replace("\r\n", "")
    candidates = (
        ("\r\n", crlf_count),
        ("\n", without_crlf.count("\n")),
        ("\r", without_crlf.count("\r")),
    )
    newline, count = max(candidates, key=lambda item: item[1])
    return newline if count else "\n"


def _detect_text_format(raw: bytes) -> _TextFileFormat:
    """按 file-browser 的解码链探测已有文件编码和主导换行。"""
    text, encoding = _decode_text_bytes(raw)
    return _TextFileFormat(encoding=encoding, newline=_detect_newline(text))


def _encode_content(content: str, file_format: _TextFileFormat) -> bytes:
    """把前端文本转换为目标文件原有的换行和字符编码。"""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if file_format.newline != "\n":
        normalized = normalized.replace("\n", file_format.newline)
    return normalized.encode(file_format.encoding)
```

更新模块 docstring，删除“非 UTF-8 文件保存后会被转为 UTF-8”的旧描述，改为说明已有文件保持编码/BOM/主导换行，新文件使用 UTF-8 无 BOM + LF。

- [ ] **Step 4: 修改 handler，在内存编码成功后再写盘**

将 `created = not target.exists()` 到 `logger.info(...)` 的现有写盘逻辑替换为：

```python
    created = not target.exists()
    file_format = _DEFAULT_TEXT_FILE_FORMAT
    if not created:
        try:
            file_format = _detect_text_format(target.read_bytes())
        except OSError as exc:
            logger.exception("[file-write] failed to read %s", target)
            return _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=_elapsed(t0),
                saved=False,
                created=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                path=path,
                stderr=str(exc),
            )

    try:
        output_bytes = _encode_content(content, file_format)
    except (UnicodeEncodeError, LookupError) as exc:
        logger.warning(
            "[file-write] content cannot be encoded as %s for %s: %s",
            file_format.encoding,
            target,
            exc,
        )
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            saved=False,
            created=created,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=path,
            stderr=f"content cannot be encoded as {file_format.encoding}: {exc}",
        )

    try:
        if created:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output_bytes)
    except OSError as exc:
        logger.exception("[file-write] failed to write %s", target)
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(t0),
            saved=False,
            created=created,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=path,
            stderr=str(exc),
        )

    logger.info(
        "[file-write] saved %s (%d bytes, encoding=%s, newline=%r)",
        target,
        len(output_bytes),
        file_format.encoding,
        file_format.newline,
    )
```

将成功响应中的：

```python
size=len(output_bytes)
```

改为：

```python
size=len(output_bytes)
```

- [ ] **Step 5: 运行新增回归测试并确认 GREEN**

Run:

```text
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/test_file_write.py -v -k "preserves_utf8_crlf or preserves_utf8_bom or preserves_gbk_crlf or mixed_line_endings or tied_line_endings or unencodable_content"
```

Expected: 6 passed。

- [ ] **Step 6: 运行 file-write 全部测试**

Run:

```text
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/test_file_write.py -v
```

Expected: 19 passed，原有 13 个测试无回归。

- [ ] **Step 7: 格式化并进行单文件 lint**

依次对以下文件使用内置 `code_format`，再使用内置 `code_check`：

```text
tools/webapi/file_write.py
tests/test_file_write.py
```

Expected: 两个文件均无 ruff error；格式化后重新执行 Step 6。

- [ ] **Step 8: 审阅差异并提交实现**

Run:

```text
git diff --check
git diff -- tools/webapi/file_write.py tests/test_file_write.py
git status --short
```

确认只包含目标端点及测试后执行：

```text
git add tools/webapi/file_write.py tests/test_file_write.py
git commit -m "fix(webapi): preserve file format in file-write"
```

---

### Task 2: 完整验证与代码审查

**Files:**
- Verify: `tools/webapi/file_write.py`
- Verify: `tests/test_file_write.py`
- Reference: `docs/superpowers/specs/2026-07-24-file-write-preserve-format-design.md`

**Interfaces:**
- Consumes: Task 1 的 `file-write` 行为和回归测试。
- Produces: 新鲜的测试、lint、diff 和审查证据。

- [ ] **Step 1: 运行相关测试**

Run:

```text
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/test_file_write.py tests/test_file_browser.py -v
```

Expected: 全部通过。

- [ ] **Step 2: 运行完整测试套件**

Run:

```text
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/ -q
```

Expected: file-write 新增测试全部计入通过项；除以下两个基线失败外没有新失败：

```text
tests/test_git_branches.py::test_branches_etag_changes_after_upstream_track_change
tests/test_vivado_e2e.py::TestVivadoE2E::test_vivado_mcp_imports
```

若失败集合发生变化，必须先定位本次修改是否造成回归，不得把新失败归为基线问题。

- [ ] **Step 3: 运行最终 lint 和差异检查**

使用内置 `code_check` 检查：

```text
tools/webapi/file_write.py
tests/test_file_write.py
```

并运行：

```text
git diff HEAD~1 --check
git status --short --branch
git log -3 --oneline
```

Expected: ruff 0 error；diff 无空白错误；工作区无未提交实现修改。

- [ ] **Step 4: 请求代码审查**

使用 `requesting-code-review` 流程，以设计提交前的 `0bff1fc` 为 base、当前 HEAD 为 head，检查：

- 是否真正保持 UTF-8 BOM、GBK/CP936 和换行；
- 编码失败时是否在写盘前返回，避免覆盖原文件；
- 是否保持请求协议、路径防御和 upsert；
- 是否存在循环导入、异常未捕获或测试缺口。

修复所有 Critical/Important 问题后，重新执行 Task 2 Steps 1-3。

- [ ] **Step 5: 汇总本地结果**

报告 worktree、分支、本地 commit、修改文件、RED/GREEN 证据、相关测试、完整测试和 lint 结果。禁止 push 或发起 PR。
