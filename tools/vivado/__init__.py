"""vivado 子包 (PR-1 2026-07-23)。

模块拆分:
    state      - module-level dataclass + manager
    launcher   - (Task 3) 组合 detect + build_env
    bootstrap  - (Task 3) build_mcp_cfg / bootstrap_mcp / shutdown_mcp
    manager    - (Task 4) 4 个 cmd handler
    inject     - (Task 10) system_prompt 注入
"""
