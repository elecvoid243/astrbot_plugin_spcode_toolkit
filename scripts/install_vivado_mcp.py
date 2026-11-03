"""Compatibility entry point for installing the standalone tool runtime."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from install_tool_runtime import main

if __name__ == "__main__":
    main()
