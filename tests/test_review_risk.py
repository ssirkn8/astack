"""Risk floors change scrutiny while durable readiness remains separately verified."""

from pathlib import Path

import pytest

from astack.project_policy import review_policy, review_risk


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/build.yml",
        "web/package.json",
        "src/auth.py",
        "src/credentials.py",
        "src/integrations/service_credentials.py",
        ".opencode/skills/human-decisions/SKILL.md",
        "upgrade.py",
        "src/astack/release.py",
        "release/intent.toml",
        "validation.py",
        "exports/private.json",
        ".astack/project.toml",
        "REVIEW.md",
    ],
)
def test_protected_surfaces_cannot_be_classified_low(path: str) -> None:
    result = review_risk([path], requested="low")
    assert result["risk"] == "high"
    assert result["reviewer_configuration"] == "single"
    assert result["promotion"] == "not_enabled"


def test_science_and_unknown_evidence_escalate_without_profile_assumptions() -> None:
    assert review_risk(["src/ui.js"])["risk"] == "medium"
    assert review_risk(["docs/usage.md"])["risk"] == "low"
    assert review_risk(["docs/usage.md"], scientific_changes=True)["risk"] == "high"
    assert review_risk(["src/ui.js"], unknown_factors=True)["risk"] == "high"
    assert review_risk([])["risk"] == "high"
    assert review_risk(["docs/usage.md"], requested="critical")["risk"] == "critical"


@pytest.mark.parametrize("profile", ["general", "data-science"])
def test_shared_policy_requires_actual_science_axis_in_both_profiles(
    tmp_path: Path, profile
) -> None:
    config = tmp_path / ".astack" / "project.toml"
    config.parent.mkdir()
    declared = (
        ""
        if profile == "general"
        else 'capabilities = ["scientific-review", "scientific-validation"]\n'
    )
    config.write_text(f'profile = "{profile}"\n{declared}')
    ordinary = review_policy(tmp_path, ["src/annotation.js"])
    science = review_policy(tmp_path, ["src/annotation.js"], scientific_changes=True)
    # Data Science declares scientific review, so every change gets the science axis.
    assert ordinary["risk"] == ("medium" if profile == "general" else "high")
    assert ordinary["scientific_applicability"] == (
        "interpretive" if profile == "general" else "required"
    )
    assert science["risk"] == "high"
    assert "scientific_credibility" in science["required_axes"]
    assert science["reviewer_configuration"] == "single"


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../owner", "a/../b", r"C:\owner\secrets", "", ".", "./", "a\x00b"],
)
def test_invalid_candidate_paths_fail_closed(path) -> None:
    with pytest.raises(ValueError, match="relative candidate"):
        review_risk([path])


def test_relative_dot_and_backslash_paths_normalize_before_matching(tmp_path: Path) -> None:
    config = tmp_path / ".astack" / "project.toml"
    config.parent.mkdir()
    config.write_text('profile = "general"\n[review]\nscientific_paths = ["custom/**"]\n')
    result = review_policy(
        tmp_path,
        ["./.github/workflows/ci.yml", r"analysis\model.py", "./custom/./check.py"],
    )
    assert result["risk_floor"] == "high"
    assert result["protected_paths"] == [".github/workflows/ci.yml"]
    assert result["scientific_paths"] == ["analysis/model.py"]
    assert result["matched_scientific_paths"] == ["custom/check.py"]
    assert "scientific_credibility" in result["required_axes"]
