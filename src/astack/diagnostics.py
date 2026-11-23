"""Small, local-only summaries of existing workflow evidence."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
METRIC_DEFINITION_VERSION = 1
DEFAULT_RETENTION = 20
MAX_RETENTION = 100
_STATUSES = {"passed", "failed", "unavailable", "not-run"}
_STAGES = {"check", "integration", "scaffold-smoke", "strict-audit"}
_CAPABILITIES = {"dvc", "quarto", "marimo"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _category(value: Any, allowed: set[str]) -> bool:
    # JSON containers are not hashable. Malformed observations must never become authority.
    return isinstance(value, str) and value in allowed


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _stage_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value in _STAGES:
        return value
    parts = value.split(".")
    if (
        len(parts) == 3
        and parts[0] == "capability"
        and parts[1] in _CAPABILITIES
        and parts[2].isdecimal()
        and 1 <= int(parts[2]) <= 50
    ):
        return value
    return None


def make_record(*, validation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create an allowlisted record from an already finished validation run, with no probes."""
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "evidence": {},
    }
    evidence: dict[str, Any] = record["evidence"]
    if isinstance(validation, dict):
        clean: dict[str, Any] = {}
        if _category(validation.get("status"), _STATUSES):
            clean["status"] = validation["status"]
        duration = _number(
            validation.get("total_duration_seconds", validation.get("duration_seconds"))
        )
        if duration is not None:
            clean["duration_seconds"] = duration
        counts = validation.get("test_counts")
        if isinstance(counts, dict):
            safe_counts = {
                key: count
                for key in ("total", "integration", "fast")
                if (count := _count(counts.get(key))) is not None
            }
            if safe_counts:
                clean["test_counts"] = safe_counts
        stages = validation.get("stages")
        if isinstance(stages, list):
            clean_stages = []
            for stage in stages[:50]:
                if not isinstance(stage, dict) or (name := _stage_name(stage.get("name"))) is None:
                    continue
                item: dict[str, Any] = {"name": name}
                if _category(stage.get("status"), _STATUSES):
                    item["status"] = stage["status"]
                value = _number(stage.get("duration_seconds"))
                if value is not None:
                    item["duration_seconds"] = value
                if isinstance(stage.get("budget_exceeded"), bool):
                    item["budget_exceeded"] = stage["budget_exceeded"]
                clean_stages.append(item)
            if clean_stages:
                clean["stages"] = clean_stages
        if clean:
            evidence["validation"] = clean
    return record


def _summary_unavailable(reason: str) -> dict[str, Any]:
    return {
        "evidence_state": "unavailable",
        "reason": reason,
        "records": 0,
        "observations": [],
        "recurring_patterns": [],
        "hypotheses": [],
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report descriptive observations only, with no score, gate, or recommendation."""
    if not isinstance(records, list):
        return _summary_unavailable("invalid_records")
    if any(not isinstance(r, dict) for r in records):
        return _summary_unavailable("invalid_record")
    if any(
        r.get("schema_version") != SCHEMA_VERSION
        or r.get("metric_definition_version") != METRIC_DEFINITION_VERSION
        for r in records
    ):
        return _summary_unavailable("unsupported_version")
    valid: list[dict[str, Any]] = []
    for record in records:
        evidence = record.get("evidence")
        if isinstance(evidence, dict):
            valid.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "metric_definition_version": METRIC_DEFINITION_VERSION,
                    "evidence": _sanitize_evidence(evidence),
                }
            )
    if len(valid) < 2:
        return {
            "evidence_state": "insufficient",
            "records": len(valid),
            "observations": [],
            "recurring_patterns": [],
            "hypotheses": [],
        }
    observations: list[str] = []
    durations = [
        v
        for r in valid
        if isinstance(r["evidence"].get("validation"), dict)
        if (v := _number(r["evidence"]["validation"].get("duration_seconds"))) is not None
    ]
    if durations:
        observations.append(
            f"Validation duration observed in {len(durations)} of {len(valid)} records."
        )
    failed_runs = sum(
        r["evidence"].get("validation", {}).get("status") == "failed"
        for r in valid
        if isinstance(r["evidence"].get("validation", {}), dict)
    )
    if failed_runs:
        observations.append(f"Validation status was failed in {failed_runs} records.")
    patterns: list[str] = []
    stage_names: set[str] = set()
    for record in valid:
        validation = record["evidence"].get("validation")
        stages = validation.get("stages", []) if isinstance(validation, dict) else []
        for stage in stages:
            if (
                isinstance(stage, dict)
                and isinstance(stage.get("name"), str)
                and stage.get("status") == "failed"
            ):
                stage_names.add(stage["name"])
    for name in sorted(stage_names):
        count = sum(
            any(
                isinstance(s, dict) and s.get("name") == name and s.get("status") == "failed"
                for s in r["evidence"].get("validation", {}).get("stages", [])
            )
            for r in valid
            if isinstance(r["evidence"].get("validation", {}), dict)
        )
        if count >= 2:
            patterns.append(f"Stage {name} failed in {count} records.")
    return {
        "evidence_state": "observations",
        "records": len(valid),
        "observations": observations,
        "recurring_patterns": patterns,
        "hypotheses": [],
    }


def _sanitize_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    # Stored durations use the stable sanitized name, accepted by make_record. Older history may
    # still carry retired lifecycle or scientific keys. They are ignored, not treated as errors.
    validation = value.get("validation")
    record = make_record(validation=validation if isinstance(validation, dict) else None)
    sanitized = record.get("evidence")
    if not isinstance(sanitized, dict):
        return {}
    return {str(key): item for key, item in sanitized.items()}


def append_record(
    root: Path, record: dict[str, Any], *, retention: int = DEFAULT_RETENTION
) -> dict[str, Any]:
    """Append sanitized history. The caller must hold the diagnostics state lock."""
    if not 1 <= retention <= MAX_RETENTION:
        raise ValueError(f"retention must be between 1 and {MAX_RETENTION}")
    root = root.resolve()
    ds = root / ".astack"
    if ds.is_symlink():
        raise ValueError("diagnostics directory must not be a symlink")
    if not ds.exists():
        ds.mkdir()
    path = ds / "diagnostics.json"
    if path.is_symlink():
        raise ValueError("diagnostics file must not be a symlink")
    history: list[dict[str, Any]] = []
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("diagnostics history is invalid") from exc
        if (
            not isinstance(stored, dict)
            or stored.get("schema_version") != SCHEMA_VERSION
            or not isinstance(stored.get("records"), list)
        ):
            raise ValueError("diagnostics history has an unsupported schema")
        history = []
        for prior in stored["records"]:
            if not isinstance(prior, dict):
                raise ValueError("diagnostics history contains an invalid record")
            if (
                prior.get("schema_version") != SCHEMA_VERSION
                or prior.get("metric_definition_version") != METRIC_DEFINITION_VERSION
            ):
                raise ValueError("diagnostics history contains an unsupported version")
            history.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "metric_definition_version": METRIC_DEFINITION_VERSION,
                    "evidence": _sanitize_evidence(prior.get("evidence")),
                }
            )
    if not isinstance(record, dict):
        raise ValueError("diagnostics record is invalid")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("metric_definition_version") != METRIC_DEFINITION_VERSION
    ):
        raise ValueError("diagnostics record has an unsupported version")
    clean = {
        "schema_version": SCHEMA_VERSION,
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "evidence": _sanitize_evidence(record.get("evidence")),
    }
    history = (history + [clean])[-retention:]
    result = {"schema_version": SCHEMA_VERSION, "records": history}
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=ds, prefix=".diagnostics-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def load_summary(root: Path) -> dict[str, Any]:
    """Read and summarize local history, returning unavailable rather than a false pass."""
    root = root.resolve()
    ds = root / ".astack"
    path = ds / "diagnostics.json"
    if ds.is_symlink() or path.is_symlink():
        return _summary_unavailable("unsafe_path")
    if not path.exists():
        return _summary_unavailable("missing")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _summary_unavailable("corrupt")
    if not isinstance(stored, dict) or stored.get("schema_version") != SCHEMA_VERSION:
        return _summary_unavailable("unsupported_version")
    records = stored.get("records")
    if not isinstance(records, list):
        return _summary_unavailable("corrupt")
    return summarize(records)
