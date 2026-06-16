"""codegraph MCP 集成测试。"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._codegraph_mcp import (  # noqa: E402
    SHELL_META_RE,
    SYSTEM_DIR_BLACKLIST,
    _detect_from_install_dir,
    build_cli_launcher,
    candidate_npm_roots,
    detect_codegraph_launcher,
    ensure_stdio_allowlist,
    resolve_project_path,
)


# ── SHELL_META_RE ──────────────────────────────────


def test_shell_meta_re_matches_ampersand():
    assert SHELL_META_RE.search("foo&bar")


def test_shell_meta_re_matches_pipe():
    assert SHELL_META_RE.search("foo|bar")


def test_shell_meta_re_matches_semicolon():
    assert SHELL_META_RE.search("foo;bar")


def test_shell_meta_re_matches_redirection():
    assert SHELL_META_RE.search("foo>bar")


def test_shell_meta_re_matches_backtick():
    assert SHELL_META_RE.search("foo`bar")


def test_shell_meta_re_no_match_normal_path():
    assert not SHELL_META_RE.search("C:/Users/elecvoid/projects/my-app")


def test_shell_meta_re_no_match_chinese():
    assert not SHELL_META_RE.search("D:/项目/my-app")


# ── SYSTEM_DIR_BLACKLIST ───────────────────────────


def test_system_dir_blacklist_has_windows():
    assert any("Windows" in d for d in SYSTEM_DIR_BLACKLIST)


def test_system_dir_blacklist_has_etc():
    assert "/etc" in SYSTEM_DIR_BLACKLIST


# ── resolve_project_path ───────────────────────────


def test_resolve_project_path_absolute(tmp_path):
    p = tmp_path / "myproj"
    p.mkdir()
    result = resolve_project_path(str(p), init=True)
    assert result == p.resolve()


def test_resolve_project_path_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "myproj").mkdir()
    result = resolve_project_path("myproj", init=True)
    assert result == (tmp_path / "myproj").resolve()


def test_resolve_project_path_expanduser(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "myapp").mkdir()
    result = resolve_project_path("~/projects/myapp", init=True)
    assert result == (tmp_path / "projects" / "myapp").resolve()


def test_resolve_project_path_rejects_dotdot():
    r = resolve_project_path("../etc", init=True)
    assert isinstance(r, str) and ".." in r


def test_resolve_project_path_rejects_system_windows():
    r = resolve_project_path("C:/Windows/System32", init=True)
    assert isinstance(r, str) and ("黑名单" in r or "系统" in r)


def test_resolve_project_path_rejects_system_etc():
    r = resolve_project_path("/etc", init=True)
    assert isinstance(r, str)


def test_resolve_project_path_rejects_user_blacklist(tmp_path):
    target = tmp_path / "secret"
    target.mkdir()
    r = resolve_project_path(str(target), init=True, user_blacklist=[str(tmp_path)])
    assert isinstance(r, str) and "黑名单" in r


def test_resolve_project_path_init_nonexistent(tmp_path):
    r = resolve_project_path(str(tmp_path / "nonexistent"), init=True)
    assert isinstance(r, str) and "不存在" in r


def test_resolve_project_path_init_not_a_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    r = resolve_project_path(str(f), init=True)
    assert isinstance(r, str) and "不是目录" in r


def test_resolve_project_path_uninit_nonexistent(tmp_path):
    # uninit 时目录不存在不报错
    r = resolve_project_path(str(tmp_path / "nonexistent"), init=False)
    assert isinstance(r, Path)


def test_resolve_project_path_empty_input():
    r = resolve_project_path("", init=True)
    assert isinstance(r, str) and "用法" in r


def test_resolve_project_path_strips_quotes(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    r = resolve_project_path(f'"{p}"', init=True)
    assert r == p.resolve()


# ── resolve_project_path(require_code_files=...) ─────
# v2.9: /codegraph init 要求目标目录下至少有一个代码文件,
# 对齐 /agentsmd init|load 语义。


def test_resolve_project_path_require_code_files_with_py(tmp_path):
    """init + require_code_files=True + 目录含 .py → 通过。"""
    p = tmp_path / "realproj"
    p.mkdir()
    (p / "main.py").write_text("x = 1")
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert r == p.resolve()


def test_resolve_project_path_require_code_files_empty_dir(tmp_path):
    """init + require_code_files=True + 空目录 → 报错。"""
    p = tmp_path / "empty"
    p.mkdir()
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert isinstance(r, str) and "代码文件" in r


def test_resolve_project_path_require_code_files_only_docs(tmp_path):
    """init + require_code_files=True + 只有 markdown/json → 报错。"""
    p = tmp_path / "docsonly"
    p.mkdir()
    (p / "README.md").write_text("# doc")
    (p / "package.json").write_text("{}")
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert isinstance(r, str) and "代码文件" in r


def test_resolve_project_path_require_code_files_nested(tmp_path):
    """init + require_code_files=True + 代码在子目录 → 仍然通过(递归)。"""
    p = tmp_path / "nested"
    p.mkdir()
    src = p / "src" / "deep"
    src.mkdir(parents=True)
    (src / "module.py").write_text("x = 1")
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert r == p.resolve()


def test_resolve_project_path_require_code_files_skips_node_modules(tmp_path):
    """init + require_code_files=True + 仅 node_modules 里有 .py → 报错。"""
    p = tmp_path / "noise"
    p.mkdir()
    nm = p / "node_modules"
    nm.mkdir()
    (nm / "dep.py").write_text("# ignored")
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert isinstance(r, str) and "代码文件" in r


def test_resolve_project_path_require_code_files_default_false(tmp_path):
    """require_code_files 默认 False:空目录也能 init(向后兼容)。"""
    p = tmp_path / "empty_legacy"
    p.mkdir()
    r = resolve_project_path(str(p), init=True)  # 不传 require_code_files
    assert r == p.resolve()


def test_resolve_project_path_require_code_files_ignored_for_uninit(tmp_path):
    """uninit 时 require_code_files=True 也不报错(uninit 允许空目录)。"""
    ghost = tmp_path / "ghost"
    r = resolve_project_path(str(ghost), init=False, require_code_files=True)
    assert isinstance(r, Path)


def test_resolve_project_path_require_code_files_only_junk_in_subdir(tmp_path):
    """真实代码在子目录 + 垃圾目录里有 .py → 以真实代码为准,通过。"""
    p = tmp_path / "mixed"
    p.mkdir()
    nm = p / "node_modules"
    nm.mkdir()
    (nm / "dep.py").write_text("# ignored")
    (p / "real.py").write_text("x = 1")
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert r == p.resolve()


def test_resolve_project_path_require_code_files_multiple_extensions(tmp_path):
    """init + require_code_files + 目录含主流语言代码 → 通过。"""
    p = tmp_path / "polyglot"
    p.mkdir()
    for f in ["app.js", "main.go", "lib.rs", "Main.java"]:
        (p / f).write_text(f"// {f}")
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert r == p.resolve()


def test_resolve_project_path_require_code_files_message_lists_supported(tmp_path):
    """错误消息应列出支持的后缀,便于用户理解。"""
    p = tmp_path / "empty"
    p.mkdir()
    r = resolve_project_path(str(p), init=True, require_code_files=True)
    assert isinstance(r, str)
    # 至少出现几个标志后缀
    for ext in [".py", ".js", ".cpp", ".go"]:
        assert ext in r, f"错误消息应列出 {ext}"


# ── ensure_stdio_allowlist ─────────────────────────


def test_ensure_stdio_allowlist_empty_env(monkeypatch):
    monkeypatch.delenv("ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS", raising=False)
    ensure_stdio_allowlist()
    assert "codegraph" in os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"]
    assert "node" in os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"]


def test_ensure_stdio_allowlist_preserves_existing(monkeypatch):
    monkeypatch.setenv("ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS", "python,node")
    ensure_stdio_allowlist()
    items = set(
        x.strip() for x in os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"].split(",")
    )
    assert {"python", "node", "codegraph"}.issubset(items)


def test_ensure_stdio_allowlist_idempotent(monkeypatch):
    monkeypatch.delenv("ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS", raising=False)
    ensure_stdio_allowlist()
    before = os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"]
    ensure_stdio_allowlist()
    after = os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"]
    assert before == after  # 重复调用不重复添加


def test_ensure_stdio_allowlist_normalizes_case(monkeypatch):
    monkeypatch.setenv("ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS", "CodeGraph,NODE")
    ensure_stdio_allowlist()
    items = {
        x.strip().lower()
        for x in os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"].split(",")
    }
    assert "codegraph" in items
    assert "node" in items


# ── candidate_npm_roots ────────────────────────────


def test_candidate_npm_roots_includes_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda x: None)
    roots = candidate_npm_roots()
    assert str(tmp_path) in roots


def test_candidate_npm_roots_filters_nonexistent(monkeypatch, tmp_path):
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(shutil, "which", lambda x: None)
    roots = candidate_npm_roots()
    # 不存在的目录不应出现在结果中
    assert not any(str(tmp_path / "nonexistent") in r for r in roots)


def test_candidate_npm_roots_returns_list():
    roots = candidate_npm_roots()
    assert isinstance(roots, list)
    # 至少包含一些已存在的平台默认路径(取决于运行环境)
    for r in roots:
        assert Path(r).is_dir()


# ── detect_codegraph_launcher ──────────────────────


def test_detect_windows_bundled(monkeypatch, tmp_path):
    # 构造一个 mock 的 npm 全局安装目录
    pkg_dir = tmp_path / "node_modules" / "@colbymchenry" / "codegraph-win32-x64"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "node.exe").write_bytes(b"")
    (pkg_dir / "lib" / "dist" / "bin").mkdir(parents=True)
    (pkg_dir / "lib" / "dist" / "bin" / "codegraph.js").write_text("")

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        "tools._codegraph_mcp.candidate_npm_roots",
        lambda: [str(tmp_path)],
    )

    cfg = detect_codegraph_launcher()
    assert cfg is not None
    assert cfg["command"].endswith("node.exe")
    assert "--liftoff-only" in cfg["args"]
    assert any("codegraph.js" in a for a in cfg["args"])
    assert "serve" in cfg["args"] and "--mcp" in cfg["args"]


def test_detect_windows_no_bundled(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        "tools._codegraph_mcp.candidate_npm_roots",
        lambda: [str(tmp_path / "nonexistent")],
    )
    monkeypatch.setattr(shutil, "which", lambda x: None)
    cfg = detect_codegraph_launcher()
    assert cfg is None  # Windows 找不到 bundled → 优雅放弃


def test_detect_unix_which(monkeypatch, tmp_path):
    fake_bin = tmp_path / "codegraph"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        shutil, "which", lambda x: str(fake_bin) if x == "codegraph" else None
    )

    cfg = detect_codegraph_launcher()
    assert cfg is not None
    assert cfg["command"] == str(fake_bin)
    assert cfg["args"] == ["serve", "--mcp"]


def test_detect_not_found(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "tools._codegraph_mcp.candidate_npm_roots",
        lambda: [],
    )
    monkeypatch.setattr(shutil, "which", lambda x: None)
    cfg = detect_codegraph_launcher()
    assert cfg is None


def test_detect_windows_partial_install(monkeypatch, tmp_path):
    # 只有 node.exe 没有 codegraph.js → 视为未安装
    pkg_dir = tmp_path / "node_modules" / "@colbymchenry" / "codegraph-win32-x64"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "node.exe").write_bytes(b"")

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        "tools._codegraph_mcp.candidate_npm_roots",
        lambda: [str(tmp_path)],
    )
    cfg = detect_codegraph_launcher()
    assert cfg is None


# ── build_cli_launcher ─────────────────────────────


def test_build_cli_strips_serve_args():
    """v2.2: 默认同时 strip --liftoff-only(它只对 serve --mcp 有效)。"""
    cfg = {
        "type": "stdio",
        "command": "C:/path/node.exe",
        "args": ["--liftoff-only", "C:/path/codegraph.js", "serve", "--mcp"],
    }
    launcher = build_cli_launcher(cfg)
    assert launcher["command"] == "C:/path/node.exe"
    # 'serve', '--mcp' 和 '--liftoff-only' 都被去掉
    assert launcher["args"] == ["C:/path/codegraph.js"]


def test_build_cli_keeps_liftoff_when_explicitly_disabled():
    """显式 strip_liftoff=False 保留 --liftoff-only(给需要 V8 旁路的特殊场景)。"""
    cfg = {
        "type": "stdio",
        "command": "node.exe",
        "args": ["--liftoff-only", "codegraph.js", "serve", "--mcp"],
    }
    launcher = build_cli_launcher(cfg, strip_liftoff=False)
    assert launcher["args"] == ["--liftoff-only", "codegraph.js"]


def test_build_cli_no_serve_args():
    """macOS/Linux 的 cfg 没有 serve/--mcp(直接是 ['serve', '--mcp'] 之外)"""
    cfg = {
        "type": "stdio",
        "command": "/usr/local/bin/codegraph",
        "args": [],  # macOS/Linux 走 PATH 时没有 node 包装
    }
    launcher = build_cli_launcher(cfg)
    assert launcher["args"] == []


def test_build_cli_strips_liftoff_only_when_not_needed():
    """实现期若发现 --liftoff-only 对 init/uninit 无效,需要 strip。函数提供 strip 模式。"""
    cfg = {
        "type": "stdio",
        "command": "node.exe",
        "args": ["--liftoff-only", "codegraph.js", "serve", "--mcp"],
    }
    launcher = build_cli_launcher(cfg, strip_liftoff=True)
    assert "--liftoff-only" not in launcher["args"]
    assert launcher["args"] == ["codegraph.js"]


def test_build_cli_none_input():
    assert build_cli_launcher(None) is None


# ── _detect_from_install_dir (v2.1) ────────────────


def test_detect_from_install_dir_windows_bundled(monkeypatch, tmp_path):
    """用户提供合法 Windows 安装目录 → 返回 node.exe + codegraph.js 配置。"""
    pkg_dir = tmp_path / "codegraph-win32-x64"
    pkg_dir.mkdir()
    (pkg_dir / "node.exe").write_bytes(b"")
    (pkg_dir / "lib" / "dist" / "bin").mkdir(parents=True)
    (pkg_dir / "lib" / "dist" / "bin" / "codegraph.js").write_text("")
    monkeypatch.setattr("sys.platform", "win32")

    cfg = _detect_from_install_dir(str(pkg_dir))
    assert cfg is not None
    assert cfg["command"] == str(pkg_dir / "node.exe")
    assert cfg["command"].endswith("node.exe")
    assert "--liftoff-only" in cfg["args"]
    assert any("codegraph.js" in a for a in cfg["args"])
    assert "serve" in cfg["args"] and "--mcp" in cfg["args"]


def test_detect_from_install_dir_windows_root_layout(monkeypatch, tmp_path):
    """codegraph.js 放在 install_dir 根(非标准布局) → 也能 fallback 找到。"""
    pkg_dir = tmp_path / "codegraph-custom"
    pkg_dir.mkdir()
    (pkg_dir / "node.exe").write_bytes(b"")
    (pkg_dir / "codegraph.js").write_text("")
    monkeypatch.setattr("sys.platform", "win32")

    cfg = _detect_from_install_dir(str(pkg_dir))
    assert cfg is not None
    assert any("codegraph.js" in a for a in cfg["args"])


def test_detect_from_install_dir_dir_not_exists(monkeypatch):
    """目录不存在 → 返回 None(不抛异常)。"""
    monkeypatch.setattr("sys.platform", "win32")
    cfg = _detect_from_install_dir("Z:/this/path/does/not/exist")
    assert cfg is None


def test_detect_from_install_dir_missing_node_exe(monkeypatch, tmp_path):
    """目录存在但缺 node.exe → 返回 None。"""
    monkeypatch.setattr("sys.platform", "win32")
    pkg_dir = tmp_path / "no-node"
    pkg_dir.mkdir()
    (pkg_dir / "codegraph.js").write_text("")
    cfg = _detect_from_install_dir(str(pkg_dir))
    assert cfg is None


def test_detect_from_install_dir_missing_codegraph_js(monkeypatch, tmp_path):
    """目录存在 + node.exe 存在但缺 codegraph.js → 返回 None。"""
    monkeypatch.setattr("sys.platform", "win32")
    pkg_dir = tmp_path / "no-entry"
    pkg_dir.mkdir()
    (pkg_dir / "node.exe").write_bytes(b"")
    cfg = _detect_from_install_dir(str(pkg_dir))
    assert cfg is None


def test_detect_from_install_dir_unix_node(monkeypatch, tmp_path):
    """非 Windows 平台: 找 'node'(不是 'node.exe')。"""
    monkeypatch.setattr("sys.platform", "linux")
    pkg_dir = tmp_path / "codegraph-pkg"
    pkg_dir.mkdir()
    (pkg_dir / "node").write_text("#!/bin/sh\n")
    (pkg_dir / "lib" / "dist" / "bin").mkdir(parents=True)
    (pkg_dir / "lib" / "dist" / "bin" / "codegraph.js").write_text("")
    cfg = _detect_from_install_dir(str(pkg_dir))
    assert cfg is not None
    assert cfg["command"] == str(pkg_dir / "node")


# ── detect_codegraph_launcher(install_dir=...) ─────────


def test_detect_launcher_with_install_dir_skips_auto(monkeypatch, tmp_path):
    """传入 install_dir → 完全不调 auto-detect(即使环境有其它 codegraph 安装)。"""
    # 构造一个合法 install_dir
    pkg_dir = tmp_path / "user-configured"
    pkg_dir.mkdir()
    (pkg_dir / "node.exe").write_bytes(b"")
    (pkg_dir / "lib" / "dist" / "bin").mkdir(parents=True)
    (pkg_dir / "lib" / "dist" / "bin" / "codegraph.js").write_text("")
    monkeypatch.setattr("sys.platform", "win32")

    # 即使 candidate_npm_roots 返回空(没其它安装),install_dir 仍能用
    monkeypatch.setattr("tools._codegraph_mcp.candidate_npm_roots", lambda: [])
    cfg = detect_codegraph_launcher(install_dir=str(pkg_dir))
    assert cfg is not None
    assert cfg["command"].endswith("node.exe")


def test_detect_launcher_with_invalid_install_dir_returns_none(monkeypatch, tmp_path):
    """传入 install_dir 但目录无效 → 返回 None,不调 auto-detect。"""
    monkeypatch.setattr("sys.platform", "win32")
    # 即使 auto-detect 能找到,无效 install_dir 仍优先返回 None
    monkeypatch.setattr(
        "tools._codegraph_mcp.candidate_npm_roots",
        lambda: [str(tmp_path / "fake")],
    )
    cfg = detect_codegraph_launcher(install_dir="Z:/nonexistent")
    assert cfg is None


def test_detect_launcher_no_install_dir_falls_back_to_auto(monkeypatch, tmp_path):
    """不传 install_dir → 走 auto-detect(给 CLI 命令用)。"""
    pkg_dir = tmp_path / "node_modules" / "@colbymchenry" / "codegraph-win32-x64"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "node.exe").write_bytes(b"")
    (pkg_dir / "lib" / "dist" / "bin").mkdir(parents=True)
    (pkg_dir / "lib" / "dist" / "bin" / "codegraph.js").write_text("")
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        "tools._codegraph_mcp.candidate_npm_roots", lambda: [str(tmp_path)]
    )
    cfg = detect_codegraph_launcher()  # install_dir 默认 None
    assert cfg is not None
    assert cfg["command"].endswith("node.exe")
