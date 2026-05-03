"""Regression tests for force-mode control persistence."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"
SELECT_PATH = ROOT / "custom_components" / "power_sync" / "select.py"


def _find_class_method(
    tree: ast.AST,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                return child
    raise AssertionError(f"{class_name}.{method_name} not found")


def _find_function(
    tree: ast.AST,
    function_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return node
    raise AssertionError(f"{function_name} not found")


def _is_async_update_entry_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "async_update_entry"
    )


def _writes_skip_reload(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    for target in node.targets:
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "_skip_reload"
        ):
            return True
    return False


def test_force_duration_select_updates_options_without_reload():
    tree = ast.parse(SELECT_PATH.read_text())
    method = _find_class_method(tree, "PowerSyncDurationSelect", "async_select_option")

    skip_reload_lines = [
        node.lineno
        for node in ast.walk(method)
        if _writes_skip_reload(node)
    ]
    update_entry_lines = [
        node.lineno
        for node in ast.walk(method)
        if _is_async_update_entry_call(node)
    ]

    assert skip_reload_lines
    assert update_entry_lines
    assert min(skip_reload_lines) < min(update_entry_lines)


def test_force_mode_persistence_uses_setup_store_reference():
    source = INIT_PATH.read_text()
    tree = ast.parse(source)
    function = _find_function(tree, "persist_force_mode_state")
    function_source = ast.get_source_segment(source, function)

    assert function_source is not None
    assert 'hass.data[DOMAIN][entry.entry_id]["store"]' not in function_source
    assert "await store.async_load()" in function_source
