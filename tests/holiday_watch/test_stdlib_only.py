import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "holiday_watch"


def test_the_package_imports_only_the_stdlib_and_itself():
    """An import bug in the app, or a missing third-party package, must never stop the watchdog."""
    bad = set()
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top not in ("holiday_watch", "__future__"):
                    bad.add(f"{path.name}: {name}")
    assert not bad
