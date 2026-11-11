"""Terminal manager module-level singleton (plugin-wide singleton convention)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .component import TerminalSessionManager

# Set by SPCodeToolkit.initialize(); None before initialization.
component: TerminalSessionManager | None = None
