"""Runtime modules share names only through explicit imports, never a merged namespace."""

import ast
from pathlib import Path

from project_tool_test_support import project_tool

from workflow import project_runtime

RUNTIME = Path(__file__).resolve().parents[1] / "workflow/project_runtime"


def _bound_names(module_name: str) -> set[str]:
    """Names a module defines or imports at top level, as written in its source."""
    tree = ast.parse((RUNTIME / f"{module_name}.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Try):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    names.update((a.asname or a.name).split(".")[0] for a in inner.names)
                elif isinstance(inner, ast.Assign):
                    names.update(t.id for t in inner.targets if isinstance(t, ast.Name))
    return names


def test_importing_the_package_adds_no_names_to_any_module() -> None:
    for module in project_runtime.RUNTIME_MODULES:
        name = module.__name__.rsplit(".", 1)[1]
        runtime_names = {n for n in vars(module) if not n.startswith("__")}
        assert runtime_names <= _bound_names(name), (name, runtime_names - _bound_names(name))


def test_facade_still_exposes_and_patches_cross_module_names(monkeypatch) -> None:
    from workflow.project_runtime import project_state, reproduction

    assert project_tool.audit is project_state.audit
    assert project_tool._ensure_dvc_cache is reproduction._ensure_dvc_cache

    def replacement(root):
        return root

    monkeypatch.setattr(project_tool, "_ensure_dvc_cache", replacement)
    # Both the defining module and the importing module see the patch.
    assert reproduction._ensure_dvc_cache is replacement
    assert project_state._ensure_dvc_cache is replacement
