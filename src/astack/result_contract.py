"""Versioned command-result envelopes for machine boundaries and readable output."""

from __future__ import annotations

from typing import Any

RESULT_SCHEMA_VERSION = 1
RESULT_OUTCOMES = frozenset({"completed", "blocked", "failed", "awaiting_input", "no_op"})
EVIDENCE_STATUSES = frozenset({"passed", "failed", "unknown", "skipped", "unavailable"})
READINESS_STATUSES = frozenset({"ready", "not_ready", "not_assessed"})
DECISION_STATUSES = frozenset({"pending", "resolved", "not_required"})
APPROVAL_STATUSES = frozenset({"required", "approved", "denied", "not_required"})

_COMMAND_RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    "workflow-runtime": ("operation", "payload"),
}

_ENVELOPE_FIELDS = {
    "schema_version",
    "command",
    "outcome",
    "summary",
    "result",
    "blockers",
    "evidence",
    "decisions",
    "approvals",
    "readiness",
    "next_action",
    "required_input",
    "notes",
}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Result {label} must be a non-empty string")
    return value


def _records(
    value: object,
    label: str,
    required_fields: tuple[str, ...],
    allowed_statuses: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"Result {label} must be a list")
    records: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"Each result {label} entry must be an object")
        missing = [field for field in required_fields if field not in item]
        if missing:
            raise ValueError(f"Result {label} entry is missing: " + ", ".join(missing))
        identifier = _text(item.get("id"), f"{label} id")
        if identifier in identifiers:
            raise ValueError(f"Result {label} ids must be unique")
        identifiers.add(identifier)
        _text(item.get("summary"), f"{label} summary")
        if allowed_statuses is not None and (
            not isinstance(item.get("status"), str) or item["status"] not in allowed_statuses
        ):
            raise ValueError(f"Result {label} status is invalid")
        records.append(item)
    return records


def validate_result_envelope(
    value: object,
    *,
    expected_command: str | None = None,
) -> dict[str, Any]:
    """Validate one versioned result envelope and its known command-specific fields."""
    if not isinstance(value, dict):
        raise ValueError("Command result must be a JSON object")
    missing = sorted(_ENVELOPE_FIELDS - value.keys() - {"notes"})
    if missing:
        raise ValueError("Command result is missing: " + ", ".join(missing))
    extra = sorted(value.keys() - _ENVELOPE_FIELDS)
    if extra:
        raise ValueError("Command result has unsupported fields: " + ", ".join(extra))
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != RESULT_SCHEMA_VERSION
    ):
        raise ValueError("Command result schema version is unsupported")
    command = _text(value.get("command"), "command identity")
    if expected_command is not None and command != expected_command:
        raise ValueError("Command result identity does not match the expected command")
    outcome = value.get("outcome")
    # Model-produced JSON can contain unhashable arrays or objects in enum fields.
    # Reject their types before membership so callers receive a validation error.
    if not isinstance(outcome, str) or outcome not in RESULT_OUTCOMES:
        raise ValueError("Command result outcome is invalid")
    _text(value.get("summary"), "summary")
    result = value.get("result")
    if not isinstance(result, dict):
        raise ValueError("Command-specific result must be an object")
    required_result_fields = _COMMAND_RESULT_FIELDS.get(command, ())
    missing_result_fields = [field for field in required_result_fields if field not in result]
    if missing_result_fields:
        raise ValueError("Command-specific result is missing: " + ", ".join(missing_result_fields))
    blockers = _records(value.get("blockers"), "blockers", ("id", "summary"))
    evidence = _records(
        value.get("evidence"),
        "evidence",
        ("id", "status", "required", "summary"),
        EVIDENCE_STATUSES,
    )
    if any(type(item.get("required")) is not bool for item in evidence):
        raise ValueError("Result evidence required must be a boolean")
    decisions = _records(
        value.get("decisions"), "decisions", ("id", "status", "summary"), DECISION_STATUSES
    )
    approvals = _records(
        value.get("approvals"), "approvals", ("id", "status", "summary"), APPROVAL_STATUSES
    )

    readiness = value.get("readiness")
    if readiness is not None:
        if not isinstance(readiness, dict):
            raise ValueError("Result readiness must be an object or null")
        _text(readiness.get("summary"), "readiness summary")
        if (
            not isinstance(readiness.get("status"), str)
            or readiness["status"] not in READINESS_STATUSES
        ):
            raise ValueError("Result readiness status is invalid")
        required_evidence = readiness.get("required_evidence")
        if not isinstance(required_evidence, list) or any(
            not isinstance(identifier, str) or not identifier.strip()
            for identifier in required_evidence
        ):
            raise ValueError("Result readiness required_evidence must be a list of ids")
        evidence_by_id = {item["id"]: item for item in evidence}
        if any(identifier not in evidence_by_id for identifier in required_evidence):
            raise ValueError("Result readiness references missing evidence")
        if readiness["status"] == "ready":
            if outcome in {"blocked", "failed", "awaiting_input"}:
                raise ValueError("Ready results cannot have an unsuccessful execution outcome")
            if blockers:
                raise ValueError("Ready results cannot contain blockers")
            if not required_evidence:
                raise ValueError("Ready results must identify required evidence")
            if any(evidence_by_id[item]["status"] != "passed" for item in required_evidence):
                raise ValueError("Ready results require every referenced evidence item to pass")
            if any(
                item["required"]
                and (item["id"] not in required_evidence or item["status"] != "passed")
                for item in evidence
            ):
                raise ValueError(
                    "Ready results require every required evidence item to be referenced and passed"
                )
            if any(item["status"] == "pending" for item in decisions):
                raise ValueError("Ready results cannot contain pending decisions")
            if any(item["status"] in {"required", "denied"} for item in approvals):
                raise ValueError("Ready results require all approvals to be resolved")

    if outcome in {"blocked", "failed"} and not blockers:
        raise ValueError(f"{outcome.title()} results require at least one blocker")
    required_input = value.get("required_input")
    if required_input is not None:
        _text(required_input, "required_input")
    if outcome == "awaiting_input" and required_input is None:
        raise ValueError("Awaiting-input results require required_input")
    next_action = value.get("next_action")
    if next_action is not None:
        _text(next_action, "next_action")
    notes = value.get("notes", [])
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise ValueError("Result notes must be a list of strings")
    return value


def make_result_envelope(
    *,
    command: str,
    outcome: str,
    summary: str,
    result: dict[str, Any],
    blockers: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    approvals: list[dict[str, Any]] | None = None,
    readiness: dict[str, Any] | None = None,
    next_action: str | None = None,
    required_input: str | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    envelope = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command": command,
        "outcome": outcome,
        "summary": summary,
        "result": result,
        "blockers": blockers or [],
        "evidence": evidence or [],
        "decisions": decisions or [],
        "approvals": approvals or [],
        "readiness": readiness,
        "next_action": next_action,
        "required_input": required_input,
        "notes": notes or [],
    }
    return validate_result_envelope(envelope)


def render_command_result(envelope: object) -> str:
    """Render a concise human-readable summary without dumping the command payload."""
    result = validate_result_envelope(envelope)
    lines = [
        "## Result",
        f"**Outcome:** {result['outcome'].replace('_', ' ')}",
        f"**Summary:** {result['summary']}",
    ]
    details = result["result"]
    if result["command"] == "workflow-runtime" and isinstance(details.get("payload"), dict):
        details = details["payload"]
        if result["command"] == "workflow-runtime":
            lines.append(f"**Operation:** {result['result']['operation']}")
    if details:
        lines.append("**Details:**")
        detail_items = list(details.items())
        for key, value in detail_items[:10]:
            lines.append(f"- {key.replace('_', ' ')}: {_display_value(value)}")
        if len(detail_items) > 10:
            lines.append(f"- {len(detail_items) - 10} additional fields omitted")
    readiness = result.get("readiness")
    if isinstance(readiness, dict):
        lines.append(
            f"**Readiness:** {readiness['status'].replace('_', ' ')}. {readiness['summary']}"
        )
    blockers = result["blockers"]
    if blockers:
        lines.append("**Blockers:**")
        lines.extend(f"- {item['id']}: {item['summary']}" for item in blockers)
    evidence = result["evidence"]
    if evidence:
        lines.append("**Evidence:**")
        lines.extend(
            f"- {item['id']}: {item['status']} ({'required' if item['required'] else 'optional'})"
            for item in evidence
        )
    decisions = [item for item in result["decisions"] if item["status"] == "pending"]
    if decisions:
        lines.append("**Pending decisions:**")
        lines.extend(f"- {item['id']}: {item['summary']}" for item in decisions)
    approvals = [item for item in result["approvals"] if item["status"] in {"required", "denied"}]
    if approvals:
        lines.append("**Approvals:**")
        lines.extend(f"- {item['id']}: {item['status']}" for item in approvals)
    if result.get("required_input"):
        lines.append(f"**Required input:** {result['required_input']}")
    if result.get("next_action"):
        lines.append(f"**Next action:** {result['next_action']}")
    return "\n".join(lines)


def _display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= 160 else text[:157] + "..."
    if isinstance(value, list):
        if not value:
            return "none"
        if len(value) <= 3 and all(isinstance(item, (str, int, float)) for item in value):
            return ", ".join(str(item) for item in value)
        return f"{len(value)} items"
    if isinstance(value, dict):
        status = value.get("status")
        summary = value.get("summary")
        if status is not None and summary is not None:
            return f"{status}: {_display_value(summary)}"
        return f"object with {len(value)} fields"
    return type(value).__name__
