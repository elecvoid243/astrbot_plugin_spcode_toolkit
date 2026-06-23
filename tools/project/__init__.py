"""/project 命令组(load/unload/status 流水线,PR-7 2026-06-23)。

包含:
    state.py      模块级 _loaded_projects dict
    pipeline.py   ProjectLoadAbort + project_load_step(子步骤包装)
    manager.py    ProjectManager: load/unload/status 命令 handler
"""
from __future__ import annotations

from . import state
from .manager import ProjectManager
from .pipeline import ProjectLoadAbort, project_load_step

__all__ = [
    "ProjectLoadAbort",
    "ProjectManager",
    "project_load_step",
    "state",
]
