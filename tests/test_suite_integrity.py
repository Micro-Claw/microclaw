"""Guards against tests whose result depends on the host text encoding."""

import ast
from pathlib import Path


def test_test_text_io_always_names_its_encoding():
    missing = []
    for path in Path(__file__).parent.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"read_text", "write_text"}:
                continue
            if not any(keyword.arg == "encoding" for keyword in node.keywords):
                missing.append(f"{path.name}:{node.lineno} {node.func.attr}()")

    assert not missing, "Text I/O must specify encoding='utf-8':\n" + "\n".join(missing)
