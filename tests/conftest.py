from __future__ import annotations

import pytest


@pytest.fixture
def publication_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from project_tool_test_support import project_tool

    monkeypatch.setattr(project_tool, "_verify_audit_identity", lambda root, commit: None)
