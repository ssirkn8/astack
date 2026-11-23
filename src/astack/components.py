from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NATIVE_DISCOVERY_ROOTS = {"codex": ".agents/skills", "claude-code": ".claude/skills"}


UPSTREAM_NOTICES = ".astack/notices"


@dataclass(frozen=True)
class Component:
    id: str
    kind: str
    source: str
    target: str
    executable: bool = False
    # Pinned upstream bytes carry their lock checksum, verified before delivery.
    sha256: str = ""


def _catalog_path(root: Path) -> Path:
    source = root / "workflow" / "components.json"
    return source if source.exists() else root / "components.json"


def load_components(root: Path) -> tuple[Component, ...]:
    payload: Any = json.loads(_catalog_path(root).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Invalid workflow component catalog")
    values = payload.get("components")
    if not isinstance(values, list):
        raise ValueError("Invalid workflow component catalog")
    components: list[Component] = []
    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Invalid workflow component entry")
        component = Component(
            id=str(value.get("id", "")),
            kind=str(value.get("kind", "")),
            source=str(value.get("source", "")),
            target=str(value.get("target", "")),
            executable=value.get("executable", False) is True,
        )
        if (
            not component.id
            or component.kind
            not in {"opencode", "package", "pi", "workflow", "generated-adapter", "upstream"}
            or not component.source
            or not component.target
            or component.id in seen_ids
            or component.target in seen_targets
        ):
            raise ValueError("Invalid or duplicate workflow component entry")
        seen_ids.add(component.id)
        seen_targets.add(component.target)
        components.append(component)
    # Declare discovery roots once. Generated wrappers share canonical skill components
    # and the ordinary checksum-based upgrade preservation rules.
    discovery = payload.get("generated_discovery", {})
    if not isinstance(discovery, dict) or any(
        harness not in NATIVE_DISCOVERY_ROOTS or root_path != NATIVE_DISCOVERY_ROOTS[harness]
        for harness, root_path in discovery.items()
    ):
        raise ValueError("Invalid generated discovery inventory")
    for component in tuple(components):
        # Supporting files (templates, references) are read through the canonical path.
        if (
            component.kind != "opencode"
            or not component.source.startswith("skills/")
            or not component.source.endswith("/SKILL.md")
        ):
            continue
        for harness, root_path in discovery.items():
            generated = Component(
                id=f"adapter.{harness}.{component.id}",
                kind="generated-adapter",
                source=component.source,
                target=f"{root_path}/{component.source.removeprefix('skills/')}",
            )
            if generated.id in seen_ids or generated.target in seen_targets:
                raise ValueError("Duplicate generated discovery component")
            seen_ids.add(generated.id)
            seen_targets.add(generated.target)
            components.append(generated)
    upstream = payload.get("upstream_skills")
    if upstream is not None:
        for generated in _upstream_components(root, upstream, list(discovery.values())):
            if generated.id in seen_ids or generated.target in seen_targets:
                raise ValueError("Duplicate upstream skill component")
            seen_ids.add(generated.id)
            seen_targets.add(generated.target)
            components.append(generated)
    return tuple(components)


def _upstream_components(root: Path, lock_path: object, roots: list[str]) -> list[Component]:
    """Deliver every pinned upstream skill file to each native discovery root.

    Claude Code reads only `.claude/skills`. Codex and Pi read `.agents/skills`. OpenCode reads
    both. License notices are delivered once.
    """
    if lock_path != "integrations/t3/upstream.lock.json":
        raise ValueError("Invalid upstream skill lock reference")
    lock: Any = json.loads((root / lock_path).read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("schema_version") != 1:
        raise ValueError("Unsupported upstream lock schema")
    result: list[Component] = []

    def deliver(item: dict[str, Any], target: str) -> Component:
        return Component(
            id=f"upstream:{target}",
            kind="upstream",
            source=str(item["path"]),
            target=target,
            executable=item["executable"] is True,
            sha256=str(item["sha256"]),
        )

    for source in lock["sources"]:
        for item in source["files"]:
            relative = str(item["path"])
            if relative.startswith(".agents/skills/"):
                skill_file = relative.removeprefix(".agents/skills/")
                if skill_file.split("/")[0] not in source["skills"]:
                    raise ValueError("Unexpected skill in upstream lock")
                result.extend(deliver(item, f"{discovery}/{skill_file}") for discovery in roots)
            elif relative.startswith("integrations/t3/licenses/"):
                notice = relative.removeprefix("integrations/t3/licenses/")
                result.append(deliver(item, f"{UPSTREAM_NOTICES}/{notice}"))
            else:
                raise ValueError("Unexpected source path in upstream lock")
    return result


def component_source(root: Path, component: Component) -> Path:
    if component.kind == "upstream":
        return root / component.source
    if component.kind in {"opencode", "generated-adapter"}:
        base = root / ".opencode" if (root / ".opencode").exists() else root / "opencode"
        return base / component.source
    if component.kind == "package":
        source = root / "src" / "astack" / component.source
        return source if source.exists() else root.parent / component.source
    if component.kind == "pi":
        base = root / ".pi" if (root / ".pi").exists() else root / "pi"
        return base / component.source
    base = root / "workflow" if (root / "workflow").exists() else root
    return base / component.source
