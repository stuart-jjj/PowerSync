"""Regression tests for Home Assistant datetime utility scoping."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"


def _non_nested_nodes(function_node: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield nodes in a function body without descending into nested scopes."""
    stack = list(function_node.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            ):
                continue
            stack.append(child)


def test_power_sync_init_does_not_shadow_module_dt_util():
    tree = ast.parse(INIT_PATH.read_text())
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in _non_nested_nodes(node):
            if not isinstance(child, ast.ImportFrom):
                continue
            if child.module != "homeassistant.util":
                continue
            for alias in child.names:
                if alias.name == "dt" and alias.asname == "dt_util":
                    offenders.append(f"{node.name}:{child.lineno}")

    assert offenders == []
