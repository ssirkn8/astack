import json
from pathlib import Path

import pytest

from astack.diagnostics import append_record, load_summary, make_record, summarize


def test_record_is_explicitly_allowlisted_and_summarizes_existing_evidence() -> None:
    record = make_record(
        validation={
            "status": "failed",
            "total_duration_seconds": 42.5,
            "command": ["secret-command"],
            "error": "private output",
            "commit": "abc",
            "test_counts": {"total": 10, "fast": 8, "integration": 2, "private": 99},
            "stages": [
                {
                    "name": "check",
                    "status": "failed",
                    "duration_seconds": 3,
                    "budget_exceeded": False,
                    "command": ["private"],
                }
            ],
        },
    )
    assert record["schema_version"] == 1
    assert record["metric_definition_version"] == 1
    encoded = json.dumps(record)
    assert "private" not in encoded and "secret-command" not in encoded and "abc" not in encoded
    assert record["evidence"]["validation"]["duration_seconds"] == 42.5
    summary = summarize([record, record])
    assert summary["evidence_state"] == "observations"
    assert summary["recurring_patterns"] == ["Stage check failed in 2 records."]
    assert summary["hypotheses"] == []


def test_summary_marks_small_samples_insufficient() -> None:
    assert summarize([make_record()])["evidence_state"] == "insufficient"


def test_append_history_has_bounded_retention_and_rejects_symlinks(tmp_path: Path) -> None:
    record = make_record(validation={"status": "passed", "total_duration_seconds": 1})
    for _ in range(3):
        history = append_record(tmp_path, record, retention=2)
    assert len(history["records"]) == 2
    assert json.loads((tmp_path / ".astack/diagnostics.json").read_text())["schema_version"] == 1

    other = tmp_path / "other"
    other.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    (linked_root / ".astack").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        append_record(linked_root, record)


def test_append_preserves_duration_and_sanitizes_every_old_record(tmp_path: Path) -> None:
    record = make_record(validation={"status": "passed", "total_duration_seconds": 12.25})
    first = append_record(tmp_path, record)
    path = tmp_path / ".astack/diagnostics.json"
    first["records"][0]["evidence"]["validation"]["private_field"] = "secret"
    first["records"].append(
        {
            "schema_version": 1,
            "metric_definition_version": 1,
            "evidence": {"validation": "malformed", "unknown": "secret"},
            "extra": "secret",
        }
    )
    path.write_text(json.dumps(first), encoding="utf-8")

    stored = append_record(tmp_path, record)
    assert stored["records"][0]["evidence"]["validation"]["duration_seconds"] == 12.25
    assert "private_field" not in stored["records"][0]["evidence"]["validation"]
    assert stored["records"][2]["evidence"]["validation"]["duration_seconds"] == 12.25
    assert "private_field" not in path.read_text(encoding="utf-8")
    assert "extra" not in path.read_text(encoding="utf-8")


def test_malformed_nested_records_are_safe_and_versions_are_not_rewritten(tmp_path: Path) -> None:
    malformed = {
        "schema_version": 1,
        "metric_definition_version": 1,
        "evidence": {"validation": []},
    }
    assert summarize([malformed, malformed])["evidence_state"] == "observations"
    assert (
        summarize([malformed, {**malformed, "metric_definition_version": 9}])["evidence_state"]
        == "unavailable"
    )

    path = tmp_path / ".astack/diagnostics.json"
    path.parent.mkdir()
    incompatible = {"schema_version": 1, "records": [{**malformed, "metric_definition_version": 9}]}
    path.write_text(json.dumps(incompatible), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported version"):
        append_record(tmp_path, make_record())
    assert json.loads(path.read_text(encoding="utf-8")) == incompatible


def test_load_summary_reports_missing_and_corrupt_as_unavailable(tmp_path: Path) -> None:
    assert load_summary(tmp_path)["evidence_state"] == "unavailable"
    path = tmp_path / ".astack/diagnostics.json"
    path.parent.mkdir()
    path.write_text("not json", encoding="utf-8")
    assert load_summary(tmp_path)["evidence_state"] == "unavailable"


def test_invalid_metric_values_are_omitted() -> None:
    record = make_record(validation={"status": "passed", "total_duration_seconds": float("nan")})
    assert "duration_seconds" not in record["evidence"]["validation"]


@pytest.mark.parametrize("invalid", [[], {}, 10**400])
def test_malformed_history_cannot_interrupt_append_or_summary(tmp_path: Path, invalid) -> None:
    record = {
        "schema_version": 1,
        "metric_definition_version": 1,
        "evidence": {
            "validation": {
                "status": invalid,
                "duration_seconds": invalid,
                "stages": [{"name": "check", "status": invalid}],
            },
        },
    }
    path = tmp_path / ".astack/diagnostics.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "records": [record]}))
    assert load_summary(tmp_path)["evidence_state"] == "insufficient"
    history = append_record(tmp_path, make_record(validation={"status": "passed"}))
    assert history["records"][0]["evidence"] == {"validation": {"stages": [{"name": "check"}]}}
    assert load_summary(tmp_path)["records"] == 2


def test_retired_lifecycle_and_scientific_history_is_read_and_dropped(tmp_path: Path) -> None:
    # History written before lifecycle and scientific inputs were retired still loads.
    retired = {
        "schema_version": 1,
        "metric_definition_version": 1,
        "evidence": {
            "validation": {"status": "failed"},
            "lifecycle": {"phase": "landed", "landing_incomplete": True},
            "scientific": {"status": "declared", "confirmation": "confirmed"},
        },
    }
    path = tmp_path / ".astack/diagnostics.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "records": [retired, retired]}))
    summary = load_summary(tmp_path)
    assert summary["evidence_state"] == "observations"
    assert summary["observations"] == ["Validation status was failed in 2 records."]
    assert summary["recurring_patterns"] == []
    history = append_record(tmp_path, make_record(validation={"status": "passed"}))
    assert [record["evidence"] for record in history["records"]] == [
        {"validation": {"status": "failed"}},
        {"validation": {"status": "failed"}},
        {"validation": {"status": "passed"}},
    ]
    stored = path.read_text(encoding="utf-8")
    assert "lifecycle" not in stored and "scientific" not in stored


def test_known_stage_recurrence_is_sanitized() -> None:
    record = make_record(
        validation={
            "stages": [
                {"name": "check", "status": "failed"},
                {"name": "secret-value", "status": "failed"},
            ]
        },
    )
    assert [stage["name"] for stage in record["evidence"]["validation"]["stages"]] == ["check"]
    result = summarize([record, record])
    assert result["recurring_patterns"] == ["Stage check failed in 2 records."]
