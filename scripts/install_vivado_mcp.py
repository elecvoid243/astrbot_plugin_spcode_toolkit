"""Install the pinned Vivado MCP release without changing AstrBot dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

VIVADO_MCP_VERSION = "0.3.27"


def main() -> None:
    """Create or update the plugin's isolated MCP server environment.

    Raises:
        subprocess.CalledProcessError: Package installation fails.
    """
    root = Path(__file__).resolve().parents[1] / ".venv-vivado"
    python = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(root)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            f"vivado-mcp=={VIVADO_MCP_VERSION}",
        ],
        check=True,
        timeout=600,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


if __name__ == "__main__":
    main()
