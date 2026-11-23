"""Compatibility entry point for the vendored project runtime source modules."""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

# Avoid writing `__pycache__` files before `workflow.project_runtime` is imported.
_sys.dont_write_bytecode = True

if __package__:
    from . import project_runtime as _runtime
    from .project_runtime.cli import main as _runtime_main
else:
    _repo_root = _Path(__file__).resolve().parent.parent
    _src_root = _repo_root / "src"
    if _src_root.is_dir() and str(_src_root) not in _sys.path:
        _sys.path.insert(0, str(_src_root))
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from workflow import project_runtime as _runtime
    from workflow.project_runtime.cli import main as _runtime_main

_RUNTIME_MODULES = _runtime.RUNTIME_MODULES


def _exports() -> dict[str, object]:
    """Every runtime name, for code and tests written against the single-file runtime."""
    names: dict[str, object] = {}
    for module in _RUNTIME_MODULES:
        for name, value in vars(module).items():
            if name.startswith("__") or isinstance(value, _ModuleType):
                continue
            if name in names and names[name] is not value:
                raise ImportError(f"Runtime modules disagree on {name!r}")
            names[name] = value
    return names


_EXPORTS = _exports()
globals().update(_EXPORTS)
__all__ = sorted(_EXPORTS)


def main() -> None:
    _runtime_main()


class _ProjectToolFacade(_ModuleType):
    """Forward monkeypatches through the compatibility facade to runtime modules."""

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for module in _RUNTIME_MODULES:
            if name in module.__dict__:
                module.__dict__[name] = value

    def __delattr__(self, name: str) -> None:
        super().__delattr__(name)
        for module in _RUNTIME_MODULES:
            module.__dict__.pop(name, None)


_sys.modules[__name__].__class__ = _ProjectToolFacade

if __name__ == "__main__":
    main()
