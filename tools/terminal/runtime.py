"""Terminal manager module-level singleton (mirrors inta_shell/runtime.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .component import TerminalSessionManager

# Set by SPCodeToolkit.initialize(); None before initialization.
component: TerminalSessionManager | None = None
