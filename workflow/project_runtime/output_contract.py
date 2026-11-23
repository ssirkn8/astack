from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    Any,
    Finding,
    Path,
    _finding,
    output_contract_summary,
)


def _audit_output_contract_summary(root: Path, scope: str | None = None) -> dict[str, Any]:
    """Scope only by an explicit analysis key. Without one, every declared output is audited."""
    return dict(output_contract_summary(root, scope))


def _check_output_contract(root: Path, findings: list[Finding], scope: str | None = None) -> None:
    """Report every blocker in the declared scientific output registry as a finding.

    An undeclared project has no blockers unless an analysis was named, which then fails.
    """
    summary = _audit_output_contract_summary(root, scope)
    for blocker in summary["blockers"]:
        findings.append(
            _finding(
                "P1",
                "scientific-output",
                "scientific-output-registry",
                str(blocker),
                summary.get("path") or ".astack/project.toml",
            )
        )
