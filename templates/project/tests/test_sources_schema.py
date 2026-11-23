import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml


def _runtime() -> Any:
    """Use the vendored runtime's field lists, so this test cannot drift from the audit."""
    # The runtime imports its sibling helpers from .astack/, as it does when run directly.
    helpers = str(Path(".astack").resolve())
    if helpers not in sys.path:
        sys.path.insert(0, helpers)
    spec = importlib.util.spec_from_file_location("astack_workflow", ".astack/workflow.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_RUNTIME = _runtime()
REQUIRED = _RUNTIME.SOURCE_REQUIRED_FIELDS
PLACEHOLDERS = _RUNTIME.PLACEHOLDER_EVIDENCE_VALUES
EPISTEMIC_FIELDS = _RUNTIME.EPISTEMIC_FIELDS
EPISTEMIC_STATES = _RUNTIME.EPISTEMIC_STATES


def has_evidence(value: object) -> bool:
    if not isinstance(value, str):
        return bool(value)
    normalized = " ".join(value.split()).casefold()
    return bool(normalized) and normalized not in PLACEHOLDERS


def valid_state(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") not in EPISTEMIC_STATES:
        return False
    if value["status"] == "known":
        return has_evidence(value.get("value"))
    if value["status"] == "not_applicable":
        return has_evidence(value.get("reason"))
    return True


def has_text_evidence(value: object) -> bool:
    return isinstance(value, str) and has_evidence(value)


def test_sources_schema() -> None:
    data = yaml.safe_load(Path("data/sources.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("schema_version") == 2
    assert isinstance(data.get("sources"), dict)
    for source_id, record in data["sources"].items():
        assert isinstance(source_id, str) and source_id
        assert isinstance(record, dict)
        assert not [
            field
            for field in REQUIRED
            if field != "allowed_outputs"
            and not (
                valid_state(record.get(field))
                if field in EPISTEMIC_FIELDS
                else has_text_evidence(record.get(field))
            )
        ]
        allowed_outputs = record["allowed_outputs"]
        assert isinstance(allowed_outputs, list) and allowed_outputs
        assert all(isinstance(output, str) and has_evidence(output) for output in allowed_outputs)
        if "://" in str(record["path"]):
            assert any(
                has_text_evidence(record.get(field)) for field in ("acquisition", "retrieval")
            )
        checksum = str(record.get("raw_checksum", ""))
        for prefix in ("sha256-tree-v2:", "sha256-tree-v1:", "sha256:"):
            checksum = checksum.removeprefix(prefix)
        if "://" not in str(record["path"]) or checksum:
            assert len(checksum) == 64
            assert all(character in "0123456789abcdefABCDEF" for character in checksum)
        assert checksum or valid_state(record.get("identity"))
