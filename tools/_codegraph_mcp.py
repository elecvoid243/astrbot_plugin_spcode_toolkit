"""
_codegraph_mcp — codegraph 官方 MCP server 集成的纯函数工具集。

- SHELL_META_RE / SYSTEM_DIR_BLACKLIST: 与 mcp_client.py:50 保持完全一致
- ensure_stdio_allowlist: 启动 MCP server 前追加白名单环境变量
- detect_codegraph_launcher: 平台特定启动器检测 (Win bundled, Unix PATH)
- build_cli_launcher: 给 /codegraph init|uninit 命令构造进程参数
- candidate_npm_roots: 列举 npm 全局根目录候选
- resolve_project_path: 校验 + 解析用户输入的项目目录

详细设计见 docs/superpowers/specs/2026-06-09-codegraph-mcp-integration.md
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from astrbot.api import logger

# 与 mcp_client.py:50 的 _SHELL_META_RE 完全一致
SHELL_META_RE = re.compile(r"[\r\n\x00;&|<>`$]")

# 硬编码系统目录黑名单 (Win + POSIX baseline)
SYSTEM_DIR_BLACKLIST: tuple[str, ...] = (
    # Windows
    "C:\\Windows",
    "C:\\Windows\\System32",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    "C:\\$Recycle.Bin",
    # POSIX
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/var",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
    "/System",
    "/Library",
    "/Applications",
)


def resolve_project_path(
    raw: str,
    *,
    init: bool,
    user_blacklist: list[str] | None = None,
) -> Path | str:
    """校验 + 解析用户输入的项目目录路径。

    Args:
        raw: 用户原始输入(可能含引号、~、相对路径)
        init: True=init 操作(目录必须存在); False=uninit(目录不存在也允许)
        user_blacklist: 用户自定义黑名单(绝对路径前缀),与系统黑名单叠加

    Returns:
        Path: 解析后的绝对路径
        str: 错误消息(可作为 proposal 直接返回给用户)
    """
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return "用法: `/codegraph init <directory>` 或 `/codegraph uninit <directory>`"
    # 跨平台 ~ 展开:Windows 上 os.path.expanduser 用 USERPROFILE 而非 HOME,
    # 但单测用 monkeypatch.setenv("HOME", ...) 验证展开;故自己实现一份
    if s.startswith("~"):
        home_env = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
        if home_env:
            s = home_env + s[1:]
    if ".." in Path(s).parts:
        return "❌ 路径不允许包含 `..`(防止目录穿越)"
    p = Path(s).resolve()
    p_str = str(p)
    # 硬编码系统目录黑名单
    for bad in SYSTEM_DIR_BLACKLIST:
        if p_str.lower().startswith(bad.lower()):
            return f"❌ 路径在系统目录黑名单内: {bad}"
    # 用户自定义黑名单
    for prefix in user_blacklist or []:
        try:
            if p_str.startswith(str(Path(prefix).resolve())):
                return f"❌ 路径在用户黑名单内: {prefix}"
        except Exception:
            pass
    if init:
        if not p.exists():
            return f"❌ 目录不存在: {p}"
        if not p.is_dir():
            return f"❌ 路径不是目录: {p}"
    return p


_STDIO_ALLOWLIST_ENV = "ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"
_REQUIRED_FOR_CODEGRAPH = frozenset({"codegraph", "node"})


def ensure_stdio_allowlist() -> None:
    """把 codegraph 追加到 stdio 白名单。必须在 enable_mcp_server 之前调用。

    mcp_client.py:140 的 `_get_stdio_command_allowlist()` 在 validate_mcp_stdio_config
    内被延迟调用,所以只要在 enable 之前注入即可。
    """
    current = os.environ.get(_STDIO_ALLOWLIST_ENV, "")
    items = {x.strip().lower() for x in current.split(",") if x.strip()}
    needed = _REQUIRED_FOR_CODEGRAPH - items
    if needed:
        items |= needed
        os.environ[_STDIO_ALLOWLIST_ENV] = ",".join(sorted(items))


def candidate_npm_roots() -> list[str]:
    """返回可能的 npm 全局根目录列表(按优先级,过滤不存在项)。"""
    roots: list[str] = []
    # 1. NPM_CONFIG_PREFIX 环境变量
    env = os.environ.get("NPM_CONFIG_PREFIX", "").strip()
    if env:
        roots.append(env)
    # 2. 调 `npm config get prefix`
    npm_exe = shutil.which("npm")
    if npm_exe:
        try:
            r = subprocess.run(
                [npm_exe, "--no-fund", "config", "get", "prefix"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                roots.append(r.stdout.strip())
        except Exception:
            pass
    # 3. 常见路径 fallback
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            roots.append(str(Path(appdata) / "npm"))
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            roots.append(str(Path(localappdata) / "pnpm"))
        scoop = Path.home() / "scoop" / "apps" / "nodejs" / "current"
        if scoop.is_dir():
            roots.append(str(scoop))
    elif sys.platform == "darwin":
        roots.extend(["/usr/local", "/opt/homebrew"])
        home = Path.home()
        for sub in [".npm-global", ".nvm/versions/node"]:
            p = home / sub
            if p.is_dir():
                roots.append(str(p))
    else:  # linux
        roots.extend(["/usr", "/usr/local"])
        home = Path.home()
        for sub in [".npm-global", ".nvm/versions/node"]:
            p = home / sub
            if p.is_dir():
                roots.append(str(p))
    return [r for r in roots if r and Path(r).is_dir()]


def _detect_from_install_dir(install_dir: str) -> dict | None:
    """从用户显式提供的 codegraph 安装目录构造启动器。

    期望目录结构(以 Windows npm 全局安装为例):
        {install_dir}/
            node.exe
            lib/dist/bin/codegraph.js

    验证失败返回 None,具体原因通过 logger.warning 给出。

    Args:
        install_dir: 包含 node.exe 的目录绝对路径

    Returns:
        {"type": "stdio", "command": str, "args": list[str]} 或 None(验证失败)
    """
    p = Path(install_dir)
    if not p.is_dir():
        logger.warning(f"codegraph_install_dir 不存在或不是目录: {install_dir!r}")
        return None

    is_windows = sys.platform == "win32"
    node_exe_name = "node.exe" if is_windows else "node"
    node_exe = p / node_exe_name
    if not node_exe.exists():
        logger.warning(
            f"codegraph_install_dir 缺少 {node_exe_name}: {p}"
            "(请确认是 codegraph 包的根目录,内含 bundled node)"
        )
        return None

    # 优先 lib/dist/bin/codegraph.js(npm 全局布局);fallback 找同级 codegraph.js
    entry = p / "lib" / "dist" / "bin" / "codegraph.js"
    if not entry.exists():
        entry = p / "codegraph.js"
    if not entry.exists():
        logger.warning(
            f"codegraph_install_dir 缺少 codegraph.js(尝试过 lib/dist/bin/ 和根): {p}"
        )
        return None

    logger.info(f"使用 codegraph_install_dir 显式路径: {p}")
    return {
        "type": "stdio",
        "command": str(node_exe),
        "args": [
            "--liftoff-only",  # 防 Node 22+ tree-sitter WASM Zone OOM
            str(entry),
            "serve",
            "--mcp",
        ],
    }


def detect_codegraph_launcher(install_dir: str | None = None) -> dict | None:
    """跨平台检测 codegraph 启动器。

    Args:
        install_dir: 用户显式配置的安装目录(优先级最高);为 None 时走自动检测

    Returns:
        {"type": "stdio", "command": str, "args": list[str]} 或 None(未安装)

    行为:
    - 若 install_dir 显式传入:仅在该目录内查找,失败返回 None
    - 若 install_dir 为 None:走自动检测
        - Windows:扫 npm 全局根目录找 @colbymchenry/codegraph-win32-x64 bundled node.exe
        - macOS/Linux:shutil.which("codegraph")
    """
    if install_dir:
        return _detect_from_install_dir(install_dir)

    if sys.platform == "win32":
        for npm_root in candidate_npm_roots():
            pkg_dir = (
                Path(npm_root)
                / "node_modules"
                / "@colbymchenry"
                / "codegraph-win32-x64"
            )
            node_exe = pkg_dir / "node.exe"
            entry = pkg_dir / "lib" / "dist" / "bin" / "codegraph.js"
            if node_exe.exists() and entry.exists():
                return {
                    "type": "stdio",
                    "command": str(node_exe),
                    "args": [
                        "--liftoff-only",  # 防 Node 22+ tree-sitter WASM Zone OOM
                        str(entry),
                        "serve",
                        "--mcp",
                    ],
                }
        return None
    # macOS / Linux
    cg = shutil.which("codegraph")
    if cg:
        return {
            "type": "stdio",
            "command": cg,
            "args": ["serve", "--mcp"],
        }
    return None


def build_cli_launcher(
    mcp_cfg: dict | None,
    *,
    strip_liftoff: bool = True,
) -> dict | None:
    """把 detect_codegraph_launcher 的输出转换为 CLI (init/uninit) 启动器。

    主要差异:CLI 调用不需要 'serve --mcp' 子命令,我们替换为 init/uninit 实际子命令。
    'serve' 之后的所有 args 都被砍掉(它们是 MCP server 专属)。

    Args:
        mcp_cfg: detect_codegraph_launcher() 的返回值
        strip_liftoff: 默认 True——移除 --liftoff-only flag(它只对 serve --mcp 有效,
            是 V8 turboshaft 旁路 flag,留给 init/uninit 子进程会污染其参数解析)
    """
    if not mcp_cfg:
        return None
    args = list(mcp_cfg["args"])
    # 砍掉 'serve' 之后的所有 args
    if "serve" in args:
        idx = args.index("serve")
        args = args[:idx]
    if strip_liftoff:
        args = [a for a in args if a != "--liftoff-only"]
    return {
        "type": "stdio",
        "command": mcp_cfg["command"],
        "args": args,
    }
