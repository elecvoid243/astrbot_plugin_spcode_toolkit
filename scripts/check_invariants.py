"""main.py 瘦身重构的跨 PR 不变量检查脚本。

每次 PR 完成后跑一次(可作为 pre-commit hook):
    python scripts/check_invariants.py

不变量:
    INV-1: main.py < 600 行(目标 ~290)
    INV-2: main.py 中无 FunctionTool 子类(@dataclass + class *Tool)
    INV-3: ALL_TOOL_CLASSES 包含 13 个
    INV-4: 5 个子包都已创建(agentsmd / codegraph / project / security / llm_inject)
    INV-5: 2 个辅助模块都已创建(_stats / _guidance_text)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = PROJECT_ROOT / "main.py"

# WHY: scripts/check_invariants.py 独立运行,需要在 sys.path 注入项目根
# 才能 import tools.function_tools。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def count_lines(path: Path) -> int:
    return sum(1 for _ in path.open(encoding="utf-8"))


def check_main_py_line_count() -> bool:
    """INV-1: main.py < 600 行"""
    n = count_lines(MAIN_PY)
    ok = n < 600
    status = "✓" if ok else "✗"
    print(f"{status} INV-1: main.py = {n} lines (target < 600)")
    return ok


def check_no_tool_class_in_main() -> bool:
    """INV-2: main.py 中无 FunctionTool 子类"""
    text = MAIN_PY.read_text(encoding="utf-8")
    # 匹配 "@dataclass" 后接 "class *Tool(FunctionTool)" 的模式
    pattern = re.compile(
        r"@dataclass\s*\nclass\s+\w*Tool\s*\(\s*FunctionTool\s*\)\s*:",
        re.MULTILINE,
    )
    matches = pattern.findall(text)
    ok = len(matches) == 0
    status = "✓" if ok else "✗"
    print(f"{status} INV-2: main.py has {len(matches)} FunctionTool subclasses (target 0)")
    if matches:
        print(f"    {matches[:3]}")
    return ok


def check_all_tool_classes_count() -> bool:
    """INV-3: ALL_TOOL_CLASSES == 15

    v2.12 (PR-split-modify): 原 13 个工具 = 独立 4 + todo 4 + inta_shell 5。
    拆分后 = 独立 4 + todo 6 + inta_shell 5 = 15。
    """
    try:
        from tools.function_tools import ALL_TOOL_CLASSES

        n = len(ALL_TOOL_CLASSES)
    except Exception as e:
        print(f"✗ INV-3: failed to import ALL_TOOL_CLASSES: {e}")
        return False
    ok = n == 15
    status = "✓" if ok else "✗"
    print(f"{status} INV-3: ALL_TOOL_CLASSES has {n} entries (target 15)")
    return ok


def check_subsystem_subpackages() -> bool:
    """INV-4: 5 个子包都已创建"""
    expected = ["agentsmd", "codegraph", "project", "security", "llm_inject"]
    all_ok = True
    for sub in expected:
        path = PROJECT_ROOT / "tools" / sub
        has_init = (path / "__init__.py").exists() if path.is_dir() else False
        ok = path.is_dir() and has_init
        status = "✓" if ok else "✗"
        print(f"{status} INV-4a: tools/{sub}/ exists: {ok}")
        if not ok:
            all_ok = False
    return all_ok


def check_helper_modules() -> bool:
    """INV-5: 2 个辅助模块都已创建"""
    expected = ["_stats.py", "_guidance_text.py"]
    all_ok = True
    for mod in expected:
        path = PROJECT_ROOT / "tools" / mod
        ok = path.is_file()
        status = "✓" if ok else "✗"
        print(f"{status} INV-5: tools/{mod} exists: {ok}")
        if not ok:
            all_ok = False
    return all_ok


def main() -> int:
    print("=" * 60)
    print("main.py 瘦身重构 - 跨 PR 不变量检查")
    print("=" * 60)
    results = [
        check_main_py_line_count(),
        check_no_tool_class_in_main(),
        check_all_tool_classes_count(),
        check_subsystem_subpackages(),
        check_helper_modules(),
    ]
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"✅ All {total} invariants passed!")
        return 0
    print(f"❌ {total - passed}/{total} invariants failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())