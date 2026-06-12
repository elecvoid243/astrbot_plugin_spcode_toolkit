"""_path_safety — 统一路径安全校验(v2.4 合并自 agentsmd)。

为 agentsmd 的 /agentsmd init|load 命令提供轻量级安全检查。
`/codegraph init|uninit|set` 沿用 _codegraph_mcp.resolve_project_path(更复杂:
含 `..` 检测、目录存在性检查、shell_meta 检查);不复用本模块。

提供:
- SYSTEM_DIR_BLACKLIST: 跨平台系统敏感目录黑名单
- is_path_safe(path, *, user_blacklist=...): 返回 (safe, reason)
"""

from __future__ import annotations

from pathlib import Path

# 跨平台系统敏感目录黑名单(大小写不敏感、前缀匹配)
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

# 规范化版本(全部小写 + 统一 / 分隔符),实际比较时使用
_NORMALIZED_BLACKLIST: tuple[str, ...] = tuple(
    s.replace("\\", "/").lower() for s in SYSTEM_DIR_BLACKLIST
)


def _matches_prefix(s: str, prefix: str) -> bool:
    """检查 s 是否以 prefix 开头(完整前缀 或 前缀+/ 分隔符)。

    修复朴素 startswith 的 bug: `/etc-backup` 不应被识别为 `/etc`。
    注意:规范化时已经统一用 / 分隔符,这里也用 /。
    """
    return s == prefix or s.startswith(prefix + "/")


def is_path_safe(
    path: str | Path,
    *,
    user_blacklist: list[str] | None = None,
) -> tuple[bool, str]:
    """校验路径安全性(系统黑名单 + 用户自定义黑名单)。

    Args:
        path: 待校验路径(可为尚不存在的路径)
        user_blacklist: 用户自定义黑名单(绝对路径前缀列表)

    Returns:
        (True, "") 路径安全
        (False, reason) 路径不安全,reason 是人类可读的中文原因
    """
    try:
        p = Path(path)
    except (OSError, ValueError) as e:
        return False, f"路径无法解析: {e}"

    # 统一用 / 比较(跨平台),避免 Windows 反斜杠导致匹配失败
    s = str(p).replace("\\", "/").lower()

    # 系统黑名单
    for bad in _NORMALIZED_BLACKLIST:
        if _matches_prefix(s, bad):
            return False, f"禁止操作系统敏感目录: {bad}"

    # 用户自定义黑名单
    for prefix in user_blacklist or []:
        try:
            user_p = str(Path(prefix).resolve()).replace("\\", "/").lower()
        except (OSError, ValueError):
            continue
        if _matches_prefix(s, user_p):
            return False, f"用户黑名单: {prefix}"

    return True, ""
