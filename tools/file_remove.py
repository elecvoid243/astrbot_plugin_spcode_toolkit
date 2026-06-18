"""
file_remove — 文件/目录删除工具。
自带路径沙箱和批量确认，防止误删。
"""

from __future__ import annotations

import os
import send2trash
from pathlib import Path

from ._helpers import proposal_reply

# 系统目录黑名单（删除这些目录会让 OS 崩溃）
# 注意：/Users 不在列表中——开发者家目录常用，应放行。
_FORBIDDEN_PREFIXES = [
    # Windows
    "C:/Windows",
    "C:/windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "C:/Users/All Users",
    # macOS
    "/System",
    "/Library",
    "/private",
    "/Applications",
    # Linux/Unix
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/proc",
    "/root",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
]


def _is_protected(p: Path) -> str | None:
    """路径位于受保护的系统目录时返回拦截原因，否则 None。"""
    raw = str(p).replace("\\", "/")
    for forbidden in _FORBIDDEN_PREFIXES:
        if raw.lower() == forbidden.lower() or raw.lower().startswith(
            forbidden.lower() + "/"
        ):
            return forbidden
    return None


def _is_user_blacklisted(p: Path, custom_blacklist: list[str] | None) -> str | None:
    """路径命中用户自定义黑名单时返回原始前缀（带 'user:' 标记），否则 None。

    与内置黑名单的区别：
      - 内置 → 返回原始前缀（如 "C:/Windows"）
      - 用户 → 返回 f"user:{前缀}"（如 "user:/home/me/secret"）

    匹配规则与内置一致：resolve 后的绝对路径做归一化 + 前缀匹配。
    None / 空列表都视为"未配置"。
    """
    if not custom_blacklist:
        return None
    raw_target = str(p).replace("\\", "/").lower()
    for entry in custom_blacklist:
        if not entry:
            continue
        # 用户黑名单项也归一化，便于与 resolve 后的目标比较
        entry_norm = entry.replace("\\", "/").lower().rstrip("/")
        if not entry_norm:
            continue
        if raw_target == entry_norm or raw_target.startswith(entry_norm + "/"):
            return f"user:{entry}"  # 保留用户原始输入（未归一化）以利审计
    return None


def _human_size(n: int) -> str:
    """字节数 → 人类可读大小。

    使用浮点除法以保留精度（如 1500B → 1.5KB 而非 1KB）。
    """
    f = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if f < 1024:
            return f"{f:.1f}{unit}".replace(".0", "")
        f /= 1024
    return f"{f:.1f}PB"


def remove(
    path: str,
    confirm: bool = False,
    max_items: int = 50,
    custom_blacklist: list[str] | None = None,
) -> dict:
    r"""删除文件或目录，自带沙箱和批量确认。

    检查顺序（从外到内）：
      1. 路径穿越（".." 段）
      2. UNC / 扩展长度路径（\\server\share、\\?\...）
      3. 系统目录黑名单（在 exists 检查之前，避免 LLM 试错发现受保护路径）
      4. 用户自定义黑名单（从插件配置注入，None/[] 表示未配置）
      5. 路径存在性
      6. 单文件 → 送入回收站
      7. 目录 → confirm → 批量阈值 → 执行

    协议：返回的 proposal 字段意味着需要 LLM 在下一轮调用中以
    `confirm=true` 重试——见 README「proposal → next_call」契约。
    """
    raw = str(Path(path))

    # 1. 路径穿越拦截
    if ".." in raw.replace("\\", "/").split("/"):
        return {"ok": False, "error": "路径包含 .. 穿越，已被拒绝"}

    # 2. UNC / 扩展长度路径拦截
    if raw.startswith("\\\\") or raw.startswith("//"):
        return {
            "ok": False,
            "error": "UNC 路径（\\\\server\\share）已被拒绝",
        }
    if raw.startswith("\\\\?\\") or raw.startswith("//?/"):
        return {
            "ok": False,
            "error": "Windows 扩展长度路径（\\\\?\\...）已被拒绝",
        }

    p = Path(path).resolve()

    # 3. 系统目录黑名单（先于 exists 检查，避免信息泄露）
    blocked = _is_protected(p)
    if blocked:
        return {
            "ok": False,
            "error": f"禁止操作系统目录: {path}",
            "proposal": "路径位于受保护的系统目录中，删除操作已被拦截。",
            "evidence": {"path": path, "blocked_by": blocked},
        }

    # 4. 用户自定义黑名单（先于 exists 检查，与内置一致）
    user_blocked = _is_user_blacklisted(p, custom_blacklist)
    if user_blocked:
        return {
            "ok": False,
            "error": f"禁止删除用户配置保护的路径: {path}",
            "proposal": "路径位于用户自定义黑名单中，删除操作已被拦截。",
            "evidence": {"path": path, "blocked_by": user_blocked},
        }

    # 5. 存在性
    if not p.exists():
        return {"ok": False, "error": f"路径不存在: {path}"}

    # ── 5. 单文件删除 ──
    if p.is_file():
        try:
            size = p.stat().st_size
            send2trash.send2trash(str(p))
            return {"ok": True, "deleted": 1, "freed": _human_size(size)}
        except FileNotFoundError:
            return {"ok": False, "error": f"路径不存在: {path}"}
        except PermissionError:
            return {"ok": False, "error": f"无权限移入回收站: {path}"}
        except OSError as e:
            return {"ok": False, "error": f"回收站不可用: {e}。请确认系统已安装 trash-cli (Linux) 或回收站服务可用。"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 6. 目录删除 ──
    if p.is_dir():
        if not confirm:
            return proposal_reply(
                False,
                f"确认删除目录？目录路径: {path}。请设置 confirm=true。",
                error="目录删除需二次确认",
                options=["confirm_delete", "cancel"],
            )

        # 单次遍历：统计文件数和总大小
        file_count = 0
        total_size = 0
        deleted_paths: list[str] = []
        for f in p.rglob("*"):
            if f.is_file():
                file_count += 1
                try:
                    total_size += f.stat().st_size
                except OSError:
                    pass

        if file_count > max_items:
            return proposal_reply(
                False,
                f"目录含 {file_count} 个文件，超过上限 {max_items}。确认删除？",
                error=f"目录含 {file_count} 个文件，超过批量限制 ({max_items})",
                evidence={"file_count": file_count, "directory": str(p)},
                options=["confirm_batch_delete", "cancel"],
            )

        errors: list[dict] = []
        try:
            for root, dirs, files in os.walk(p, topdown=False, followlinks=False):
                for name in files:
                    fp = os.path.join(root, name)
                    try:
                        os.remove(fp)
                        deleted_paths.append(fp)
                    except OSError as e:
                        errors.append({"path": fp, "reason": str(e)})
                for name in dirs:
                    dp = os.path.join(root, name)
                    try:
                        os.rmdir(dp)
                    except OSError as e:
                        errors.append({"path": dp, "reason": str(e)})
            try:
                os.rmdir(p)
            except OSError as e:
                errors.append({"path": str(p), "reason": str(e)})

            return {
                "ok": True,
                "deleted": file_count - len(errors),
                "freed": _human_size(total_size),
                "deleted_paths": deleted_paths[:10],
                "errors": errors[:10],
            }
        except OSError as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"不是文件也不是目录: {path}"}
