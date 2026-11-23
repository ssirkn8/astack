"""Read-only summaries for declared scientific output contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # Vendored consumers can report the capability as unavailable.
    yaml = None  # type: ignore[assignment]

try:
    from .project_policy import project_config_path
except ImportError:
    from project_policy import project_config_path  # type: ignore[import-not-found, no-redef]

_REQUIRED = ("data_products", "human_outputs", "validation", "machine_evidence")
_CONTROL_DIRS = {".astack", ".git", ".venv", ".opencode", ".pi"}
_SENSITIVE = {".env", ".envrc", "credentials.json", "secrets.yml", "secrets.yaml"}
_PROGRESS_FIELDS = {"status", "evidence"}


def output_contract_digest(contract: dict[str, Any]) -> str:
    """Hash scientific definition while excluding mutable progress and optional diagnostics."""
    outputs = contract.get("outputs", {})
    semantic_outputs: dict[str, Any] = {}
    if isinstance(outputs, dict):
        for section in _REQUIRED:
            items = outputs.get(section, [])
            if isinstance(items, list):
                semantic_outputs[section] = [
                    {key: value for key, value in item.items() if key not in _PROGRESS_FIELDS}
                    if isinstance(item, dict)
                    else item
                    for item in items
                ]
    definition = {
        "research_question": contract.get("research_question"),
        # The digest input keeps this key name so confirmations recorded before analyses were
        # renamed from work units still match.
        "work_unit": contract.get("analysis"),
        "outputs": semantic_outputs,
    }
    if "context" in contract:
        definition["context"] = contract["context"]
    encoded = json.dumps(
        definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _registry_contract(
    root: Path, declaration: dict[str, Any], analysis: str | None
) -> tuple[dict[str, Any], Path]:
    """Derive one analysis's contract from the shared registry, without copying definitions."""

    def load(relative: object, prefix: str) -> tuple[dict[str, Any], Path]:
        if not isinstance(relative, str):
            raise ValueError("Scientific registry paths must be relative strings")
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != prefix:
            raise ValueError(f"Scientific registry path must be confined to {prefix}/")
        path = root / rel
        if any(
            root.joinpath(*rel.parts[:index]).is_symlink() for index in range(1, len(rel.parts) + 1)
        ):
            raise ValueError("Scientific registry paths must not use symlinks")
        if yaml is None:
            raise RuntimeError("YAML support is unavailable")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise ValueError("Scientific output registry/evidence schema must be 2")
        return value, path

    registry, path = load(declaration.get("path"), "docs")
    progress, _ = load(declaration.get("evidence_path"), "evidence")
    definitions = registry.get("outputs")
    validations = registry.get("validation")
    if "work_units" in registry:
        raise ValueError(
            f"Rename `work_units` to `analyses` in {path.relative_to(root)}. "
            "The entries stay the same."
        )
    bindings = registry.get("analyses")
    if not all(isinstance(value, dict) for value in (definitions, validations, bindings)):
        raise ValueError("Scientific registry requires outputs, validation and analyses mappings")
    assert (
        isinstance(definitions, dict)
        and isinstance(validations, dict)
        and isinstance(bindings, dict)
    )
    binding = (
        bindings.get(analysis)
        if analysis is not None
        else {"outputs": list(definitions), "validation": list(validations)}
    )
    if binding is None and analysis is not None:
        declared = ", ".join(sorted(map(str, bindings))) or "none"
        raise LookupError(
            f"Analysis {analysis!r} is not in the registry's analyses (declared: {declared})."
        )
    if not isinstance(binding, dict):
        raise ValueError(f"No scientific output binding for analysis {analysis!r}")
    selected = binding.get("outputs")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) for item in selected)
    ):
        raise ValueError("Scientific analysis binding requires stable output IDs")
    validation_ids = binding.get("validation", [])
    if not isinstance(validation_ids, list) or any(
        not isinstance(item, str) for item in validation_ids
    ):
        raise ValueError("Scientific analysis validation must contain stable IDs")
    validation_ids = list(validation_ids)
    sections: dict[str, list[dict[str, Any]]] = {
        section: [] for section in (*_REQUIRED, "optional_diagnostics")
    }
    progress_outputs = progress.get("outputs", {})
    progress_validation = progress.get("validation", {})
    if not isinstance(progress_outputs, dict) or not isinstance(progress_validation, dict):
        raise ValueError("Scientific evidence requires output and validation mappings")
    for identifier in sorted(set(selected)):
        definition = definitions.get(identifier)
        if not isinstance(definition, dict):
            raise ValueError(f"Unknown scientific output ID: {identifier}")
        if definition.get("type") not in {
            "figure",
            "map",
            "table",
            "dataset",
            "metric",
            "report",
            "other",
        }:
            raise ValueError(f"Scientific output {identifier} requires a supported type")
        if definition.get("role") not in {"headline", "supplementary", "review", "diagnostic"}:
            raise ValueError(f"Scientific output {identifier} requires a supported role")
        if any(key in definition for key in ("status", "evidence", "confirmation")):
            raise ValueError("Scientific definitions must not contain execution progress")
        required_validation = definition.get("validation", [])
        if not isinstance(required_validation, list) or any(
            not isinstance(item, str) for item in required_validation
        ):
            raise ValueError("Output validation references must contain stable IDs")
        validation_ids.extend(required_validation)
        state = progress_outputs.get(identifier, {})
        if not isinstance(state, dict):
            raise ValueError("Scientific output progress must be a mapping")
        entry = {
            **definition,
            "id": identifier,
            "status": state.get("status", "planned"),
            "evidence": state.get("evidence", []),
        }
        section = "data_products" if definition["type"] == "dataset" else "human_outputs"
        sections[section].append(entry)
    for identifier in sorted(set(validation_ids)):
        definition = validations.get(identifier)
        state = progress_validation.get(identifier, {})
        if not isinstance(definition, dict) or not isinstance(state, dict):
            raise ValueError(f"Unknown or malformed validation ID: {identifier}")
        if any(key in definition for key in ("status", "evidence", "confirmation")):
            raise ValueError("Validation definitions must not contain execution progress")
        sections["validation"].append(
            {
                **definition,
                "id": identifier,
                "status": state.get("status", "planned"),
                "evidence": state.get("evidence", []),
            }
        )
    confirmations = progress.get("confirmations", {})
    machine_definitions = binding.get("machine_evidence", [])
    machine_progress = progress.get("machine_evidence", {}).get(analysis or "project", {})
    if not isinstance(machine_definitions, list) or not isinstance(machine_progress, dict):
        raise ValueError("Machine evidence must be separate from human scientific outputs")
    for definition in machine_definitions:
        if not isinstance(definition, dict) or not isinstance(definition.get("id"), str):
            raise ValueError("Machine evidence definitions require stable IDs")
        state = machine_progress.get(definition["id"], {})
        if not isinstance(state, dict) or any(key in definition for key in ("status", "evidence")):
            raise ValueError("Machine evidence definitions and progress must be separate")
        sections["machine_evidence"].append(
            {
                **definition,
                "status": state.get("status", "planned"),
                "evidence": state.get("evidence", []),
            }
        )
    if not isinstance(confirmations, dict):
        raise ValueError("Scientific confirmations must be a mapping")
    confirmation = confirmations.get(analysis or "project")
    contract = {
        "schema_version": 1,
        "analysis": analysis or "project",
        "research_question": binding.get("research_question", registry.get("research_question")),
        "deadline_mode": False,
        "headline_outputs_confirmed": isinstance(confirmation, dict),
        "confirmation": confirmation,
        "outputs": sections,
        "context": binding.get("context", registry.get("context")),
        "contributing_analyses": sorted(bindings),
    }
    return contract, path


def output_contract_summary(root: Path, analysis: str | None = None) -> dict[str, Any]:
    """Summarize declared output-contract progress without writing or approving anything."""
    root = Path(root).resolve()
    result: dict[str, Any] = {
        "path": None,
        "declaration": "not_declared",
        "status": "not_declared",
        "confirmation": "unknown",
        "definition_digest": None,
        "analysis": None,
        "research_question": None,
        "required_outputs": [],
        "required_validation": [],
        "optional_deferrals": [],
        "blockers": [],
        "scientific_review_disclaimer": (
            "Automated evidence checks do not determine scientific validity or approval."
        ),
    }
    config_path = project_config_path(root)
    if not root.is_dir():
        result.update(declaration="unknown", status="unknown")
        result["blockers"].append("Linked worktree is unavailable.")
        return result
    try:
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
    except FileNotFoundError:
        return result
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result.update(declaration="unknown", status="unknown")
        result["blockers"].append(f"Project configuration unavailable or malformed: {exc}")
        return result
    if "output_contract" in config:
        result.update(declaration="declared", status="unknown")
        result["blockers"].append(
            "[output_contract] single-file contracts were removed. Move the contract into "
            "[scientific_outputs] as described in docs/scientific-outputs.md."
        )
        return result
    declaration = config.get("scientific_outputs")
    if declaration is None:
        if analysis is not None:
            result.update(declaration="unknown", status="unknown", analysis=analysis)
            result["blockers"].append(
                f"Analysis {analysis!r} was named, but this project declares no "
                "[scientific_outputs] registry."
            )
        return result
    result.update(declaration="declared", status="unknown")
    try:
        if not isinstance(declaration, dict):
            raise ValueError("Scientific output declaration must be a mapping")
        contract, registry_path = _registry_contract(root, declaration, analysis)
        result["path"] = str(registry_path.relative_to(root))
        result["representation"] = "project-registry"
        if registry_path.suffix.lower() not in {".yml", ".yaml"}:
            raise ValueError("Scientific output registry must be a .yml or .yaml file")
        resolved = registry_path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("Scientific output registry is not a confined regular file")
    except LookupError as exc:
        # A named analysis must exist: a mistyped key would otherwise check nothing.
        result.update(analysis=analysis)
        result["blockers"].append(str(exc))
        return result
    except Exception as exc:
        result["blockers"].append(f"Scientific output registry unavailable or malformed: {exc}")
        return result
    sections = contract.get("outputs")
    result["contributing_analyses"] = contract["contributing_analyses"]
    for field in ("analysis", "research_question"):
        result[field] = contract.get(field) if isinstance(contract.get(field), str) else None
    if not isinstance(sections, dict):
        result["blockers"].append("Output contract requires an outputs mapping.")
        return result
    if any(section not in (*_REQUIRED, "optional_diagnostics") for section in sections):
        # Unknown categories cannot silently hide deliverables from confirmation or validation.
        result["blockers"].append("Output contract contains unsupported output categories.")
        return result
    if (
        not isinstance(contract.get("research_question"), str)
        or not contract["research_question"].strip()
    ):
        result["blockers"].append("The research question must be a non-empty string.")
    try:
        digest = output_contract_digest(contract)
    except (TypeError, ValueError) as exc:
        result["blockers"].append(f"Scientific definition cannot be summarized: {exc}")
        return result
    if not isinstance(contract.get("deadline_mode"), bool):
        result["blockers"].append("deadline_mode must be boolean.")
    result["definition_digest"] = digest
    confirmed = contract.get("headline_outputs_confirmed") is True
    receipt = contract.get("confirmation")
    if (
        confirmed
        and isinstance(receipt, dict)
        and receipt.get("definition_digest") == digest
        and isinstance(receipt.get("evidence"), str)
        and receipt["evidence"].strip()
    ):
        result["confirmation"] = "confirmed"
    elif confirmed:
        result["confirmation"] = "reconfirmation_required"
        result["blockers"].append(
            "Headline outputs lack a matching persisted confirmation receipt."
        )
    elif isinstance(contract.get("headline_outputs_confirmed"), bool):
        result["confirmation"] = "unconfirmed"
        result["blockers"].append(
            "Headline outputs and required validation need human confirmation."
        )
    else:
        result["blockers"].append("headline_outputs_confirmed must be boolean.")
    identifiers: set[str] = set()
    all_required_complete = True
    for section in (*_REQUIRED, "optional_diagnostics"):
        items = sections.get(section)
        if not isinstance(items, list):
            result["blockers"].append(f"Output section {section} must be a list.")
            continue
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
                or item["id"] in identifiers
                or not isinstance(item.get("description"), str)
                or not item["description"].strip()
            ):
                result["blockers"].append(f"Invalid or duplicate item in {section}.")
                all_required_complete = False if section in _REQUIRED else all_required_complete
                continue
            identifiers.add(item["id"])
            required = section in _REQUIRED
            entry = {
                "id": item["id"],
                "description": item["description"],
                "type": item.get("type"),
                "role": item.get("role"),
                "status": item.get("status") if isinstance(item.get("status"), str) else None,
                "evidence": (
                    item["evidence"]
                    if isinstance(item.get("evidence"), list)
                    and all(isinstance(value, str) for value in item["evidence"])
                    else []
                ),
                "evidence_status": "valid",
            }
            status = item.get("status")
            evidence = item.get("evidence")
            if not isinstance(status, str) or status not in {
                "planned",
                "complete",
                "blocked",
                "deferred",
            }:
                result["blockers"].append(f"Item {item['id']} has an invalid status.")
            if not isinstance(evidence, list) or any(
                not isinstance(value, str) for value in evidence
            ):
                entry["evidence_status"] = "invalid"
            elif status == "complete":
                if not evidence:
                    entry["evidence_status"] = "missing"
                for evidence_path in evidence:
                    evidence_rel = Path(evidence_path)
                    try:
                        # Match audit safety: neither evidence nor its parents may be symlinks.
                        if any(
                            root.joinpath(*evidence_rel.parts[:index]).is_symlink()
                            for index in range(1, len(evidence_rel.parts) + 1)
                        ):
                            raise ValueError("evidence path uses a symlink")
                        evidence_file = (root / evidence_rel).resolve(strict=True)
                        safe = (
                            not evidence_rel.is_absolute()
                            and ".." not in evidence_rel.parts
                            and evidence_file.is_relative_to(root)
                            and evidence_file.is_file()
                            and evidence_file != resolved
                            and evidence_file.stat().st_size > 0
                            and not any(
                                part in _SENSITIVE
                                or part == ".env"
                                or part.startswith(".env.")
                                or part in _CONTROL_DIRS
                                for part in (
                                    *evidence_rel.parts,
                                    *evidence_file.relative_to(root).parts,
                                )
                            )
                        )
                        if (
                            safe
                            and subprocess.run(
                                ["git", "rev-parse", "--is-inside-work-tree"],
                                cwd=root,
                                capture_output=True,
                            ).returncode
                            == 0
                        ):
                            ignored = subprocess.run(
                                ["git", "check-ignore", "--quiet", "--", evidence_path],
                                cwd=root,
                                capture_output=True,
                            )
                            safe = ignored.returncode == 1
                    except (OSError, ValueError):
                        safe = False
                    if not safe:
                        entry["evidence_status"] = "invalid"
                        break
            else:
                entry["evidence_status"] = "incomplete"
            if required:
                (
                    result["required_validation"]
                    if section == "validation"
                    else result["required_outputs"]
                ).append(entry)
                if status != "complete" or entry["evidence_status"] != "valid":
                    all_required_complete = False
                    result["blockers"].append(
                        f"Required item {item['id']} is incomplete or lacks valid evidence."
                    )
            elif status == "deferred":
                result["optional_deferrals"].append(entry)
    if analysis is None:
        scoped = [output_contract_summary(root, name) for name in contract["contributing_analyses"]]
        covered_outputs = {
            output["id"]
            for item in scoped
            for output in item["required_outputs"]
            if output.get("type") is not None
        }
        covered_validation = {
            validation["id"] for item in scoped for validation in item["required_validation"]
        }
        # Do not turn confirmations for a subset into approval of unbound scientific outputs.
        coverage_complete = all(
            output["id"] in covered_outputs for output in result["required_outputs"]
        ) and all(
            validation["id"] in covered_validation for validation in result["required_validation"]
        )
        if (
            scoped
            and coverage_complete
            and all(item["confirmation"] == "confirmed" for item in scoped)
        ):
            result["confirmation"] = "confirmed"
            result["blockers"] = [
                blocker
                for blocker in result["blockers"]
                if blocker != "Headline outputs and required validation need human confirmation."
            ]
    result["status"] = (
        "complete" if all_required_complete and not result["blockers"] else "incomplete"
    )
    return result
