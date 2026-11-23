"""Project runtime, split by responsibility. Each module imports what it uses explicitly.

The vendored `.astack/workflow.py` compatibility API lives only in `workflow/project_tool.py`.
"""

from __future__ import annotations

from . import (
    cli,
    common,
    dependency_check,
    output_contract,
    pipeline,
    project_state,
    publication,
    reports,
    reproduction,
    review,
    review_outputs,
    sources,
    staleness,
    validation,
)

RUNTIME_MODULES = (
    common,
    project_state,
    sources,
    validation,
    pipeline,
    dependency_check,
    reproduction,
    staleness,
    output_contract,
    publication,
    reports,
    review,
    review_outputs,
    cli,
)
