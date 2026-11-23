from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    LABELED_OUTPUT_ROOTS,
    Any,
    Path,
)
from .dependency_check import dependency_checks
from .pipeline import _dvc_pipeline, _safe_pipeline_path, _stage_states
from .reproduction import (
    _durable_record_root,
    _load_run_record,
    _reproduction_engine,
    _reproduction_outputs,
    _run_record_root,
    _valid_run_record,
)
from .validation import _output_policy, _project_config


def _stage_reason(changes: object, output: str) -> tuple[str, str]:
    """Label and reason for one output of a stage that `dvc status` reports as changed."""
    reasons: list[str] = []
    for change in changes if isinstance(changes, list) else [changes]:
        if isinstance(change, str):
            reasons.append(change)
            continue
        if not isinstance(change, dict):
            continue
        for kind, detail in change.items():
            if kind == "changed outs" and isinstance(detail, dict) and output in detail:
                if detail[output] == "deleted":
                    return "missing", "deleted since its run"
                reasons.append(f"changed since its run ({detail[output]})")
            elif kind == "changed deps" and isinstance(detail, dict):
                reasons.append("inputs or code changed: " + ", ".join(sorted(detail)))
            elif kind != "changed outs":
                reasons.append(kind)
    return "outdated", "; ".join(reasons) or "its stage is not up to date"


def _untrusted_stages(root: Path) -> dict[str, str]:
    """Stages whose last checked run read or wrote files dvc.yaml does not declare."""
    reasons: dict[str, str] = {}
    for name, check in dependency_checks(root).items():
        writes, reads = check.get("undeclared_writes") or [], check.get("undeclared_reads") or []
        if writes:
            reasons[name] = f"its stage wrote undeclared files: {', '.join(writes[:3])}"
        elif reads:
            reasons[name] = f"its stage read undeclared files: {', '.join(reads[:3])}"
    return reasons


def _dvc_labels(root: Path) -> tuple[list[dict[str, Any]], str]:
    pipeline, problem = _dvc_pipeline(root)
    if pipeline is None:
        return [], problem
    status = pipeline["status"]
    stages = [
        stage
        for stage in pipeline["stages"]
        if isinstance(stage, dict) and isinstance(stage.get("outs"), list)
    ]
    # DVC status already lists a stage whose outputs were deleted, so that carries down too.
    checks = dependency_checks(root)
    states = _stage_states(root, stages, status, _untrusted_stages(root))
    labels: list[dict[str, Any]] = []
    for stage in stages:
        name = str(stage["name"])
        for out in stage["outs"]:
            relative = _safe_pipeline_path(out.get("path") if isinstance(out, dict) else None)
            if relative is None:
                continue  # Outside the project, so no report can cite it.
            entry: dict[str, Any] = {"path": relative, "stage": name}
            real = _safe_pipeline_path(out.get("real"))
            if real is not None and real != relative:
                entry["real"] = real
            if out.get("dir"):
                entry["files"] = [str(item) for item in out.get("files", [])]
                aliases = out.get("aliases")
                if isinstance(aliases, dict):
                    entry["aliases"] = {
                        str(real): str(lexical)
                        for real, lexical in aliases.items()
                        if _safe_pipeline_path(real) is not None
                    }
            check = checks.get(name)
            if check is None:
                entry["dependency_check"] = "not-run"
            elif check.get("untraced"):
                entry["dependency_check"] = "untraced"
            elif check.get("inputs_locked") is False:
                entry["dependency_check"] = "inputs-not-locked"
            else:
                entry["dependency_check"] = "passed"
            label, reason = states[name]
            if not (root / relative).exists():
                entry |= {"label": "missing", "reason": "not produced yet"}
            elif label == "outdated" and name in status and not reason:
                label, reason = _stage_reason(status[name], relative)
                entry |= {"label": label, "reason": reason}
            else:
                entry |= {"label": label, "reason": reason}
            labels.append(entry)
    return labels, ""


def _command_labels(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    outputs, errors = _reproduction_outputs(root, config)
    if errors or not outputs:
        return []
    component = config.get("reproduction", {}).get("component")
    component_layout, _ = _output_policy(config)
    try:
        folders = [
            _run_record_root(root, component),
            _durable_record_root(root, component_layout, component),
        ]
    except (RuntimeError, ValueError):
        folders = []
    records = [path for folder in folders for path in folder.glob("*.json")]
    current: set[str] = set()
    recorded: set[str] = set()
    for path in records:
        record = _load_run_record(path)
        covered = record.get("outputs") if isinstance(record, dict) else None
        if not isinstance(covered, dict):
            continue
        recorded.update(covered)
        if _valid_run_record(root, path, config):
            current.update(covered)
    labels: list[dict[str, Any]] = []
    for output in outputs:
        entry: dict[str, Any] = {"path": output, "stage": "reproduction"}
        if not (root / output).exists():
            entry |= {"label": "missing", "reason": "not produced yet"}
        elif output in current:
            entry |= {"label": "current", "reason": "matches a current run record"}
        elif output in recorded:
            entry |= {
                "label": "outdated",
                "reason": "inputs, code, configuration or the output changed since its run",
            }
        else:
            entry |= {"label": "outdated", "reason": "no run record"}
        labels.append(entry)
    return labels


def _covering(labels: list[dict[str, Any]], relative: str) -> dict[str, Any] | None:
    """The output that holds a file. A DVC directory output holds only the files it tracks.

    A citation resolves symlinks, so an output also matches through its real path.
    """
    for entry in labels:
        # A tracked symlink inside a directory output resolves to wherever it points.
        lexical = entry.get("aliases", {}).get(relative)
        if lexical is not None and lexical in entry.get("files", []):
            return entry
    for entry in labels:
        output = str(entry["path"])
        for base in dict.fromkeys([output, str(entry.get("real") or output)]):
            if relative == base:
                return entry
            if relative.startswith(base.rstrip("/") + "/"):
                files = entry.get("files")
                lexical = output.rstrip("/") + relative[len(base.rstrip("/")) :]
                if files is None or lexical in files:
                    return entry
    return None


def output_status(root: Path) -> dict[str, Any]:
    """Label every pipeline output current, outdated, missing or unknown, with a reason.

    DVC projects use `dvc status` against dvc.lock. Command projects use run records.
    Files under reports/figures, reports/tables and artifacts that no stage or run declares
    are outdated, because nothing records how they were made.
    """
    config = _project_config(root)
    engine = _reproduction_engine(root, config)
    problem = ""
    if engine == "dvc":
        labels, problem = _dvc_labels(root)
    else:
        labels = _command_labels(root, config)
    for prefix in LABELED_OUTPUT_ROOTS:
        folder = root / prefix
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            relative = path.relative_to(root).as_posix()
            if _covering(labels, relative) is None:
                labels.append(
                    {
                        "path": relative,
                        "stage": None,
                        "label": "outdated",
                        "reason": "no run record",
                    }
                )
    counts = {
        label: sum(entry["label"] == label for entry in labels)
        for label in ("current", "outdated", "missing", "unknown")
    }
    return {
        "engine": engine,
        "status": "unknown" if problem else "available",
        "problem": problem or None,
        "counts": counts,
        "outputs": labels,
    }


def evidence_label(status: dict[str, Any], relative: str) -> dict[str, Any] | None:
    """The label of the output that holds a cited file, or None when no run produced it."""
    return _covering(list(status.get("outputs", [])), relative)


def run_record_citation(root: Path, relative: str) -> bool | None:
    """Whether a cited run record is a current one, or None when the path is not a run record.

    A run record is evidence about a run, not a pipeline output, so it may be cited only while
    it still describes the current state.
    """
    if not (relative.startswith("evidence/") and "/runs/" in relative):
        return None
    if not relative.endswith(".json"):
        return False
    try:
        return _valid_run_record(root, root / relative, _project_config(root))
    except (OSError, RuntimeError, ValueError):
        return False
