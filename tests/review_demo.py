"""A realistic 22-stage pipeline for testing the review notebook, with no data work to run.

Six regions go through `ingest` and `clean` as `foreach` groups, one of them configured
differently, then ten single steps share one helper module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

REGIONS = ("north", "south", "east", "west", "delta", "bay")
SINGLE_STEPS = (
    ("mosaic", ["clean"]),
    ("prepare", ["mosaic"]),
    ("velocity", ["prepare"]),
    ("maps", ["velocity"]),
    ("seasonal", ["prepare"]),
    ("uncertainty", ["velocity"]),
    ("hotspots", ["velocity", "uncertainty"]),
    ("validation", ["velocity"]),
    ("trend_break", ["mosaic"]),
    ("summary", ["hotspots", "validation", "seasonal", "trend_break", "maps"]),
)
SHARED = "src/subsidence/geodesy.py"
PARAMS = "params.yaml"


def _output(step: str, key: str | None = None) -> str:
    return f"evidence/{step}/{key or 'result'}.json"


def demo_stages() -> list[dict[str, Any]]:
    """The stages as DVC's probe reports them."""
    stages: list[dict[str, Any]] = []

    def add(name: str, script: str, cmd: str, deps: list[str], out: str, params: dict) -> None:
        code = [{"path": script}, {"path": SHARED}]
        data = [{"path": dep} for dep in deps]
        section = {"path": PARAMS, "params": params, "params_missing": []}
        stages.append(
            {
                "name": name,
                "cmd": cmd,
                "wdir": ".",
                "dvcfile": "dvc.yaml",
                "deps": [*code, *data, section],
                "outs": [{"path": out}],
            }
        )

    for region in REGIONS:
        add(
            f"ingest@{region}",
            "scripts/ingest.py",
            f"python scripts/ingest.py {region}",
            [f"data/raw/{region}.csv"],
            _output("ingest", region),
            {"ingest.rate": 1},
        )
        mask = "on" if region == "delta" else "off"
        add(
            f"clean@{region}",
            "scripts/clean.py",
            f"python scripts/clean.py {region} --water-mask {mask}",
            [_output("ingest", region)],
            _output("clean", region),
            {"clean.min_coherence": 0.5},
        )
    for step, upstream in SINGLE_STEPS:
        inputs = [
            _output(parent, region) if parent == "clean" else _output(parent)
            for parent in upstream
            for region in (REGIONS if parent == "clean" else (None,))
        ]
        add(
            step,
            f"scripts/{step}.py",
            f"python scripts/{step}.py",
            inputs,
            _output(step),
            {f"{step}.threshold": 2},
        )
    return stages


def write_demo(target: Path) -> None:
    """The code files the stages name, each with a one-line docstring."""
    for name in ("ingest", "clean", *(step for step, _ in SINGLE_STEPS)):
        path = target / "scripts" / f"{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'"""Do the {name} step."""\n\nfrom subsidence.geodesy import rate\n\n'
            "min_coherence = threshold = 1\nprint(rate(min_coherence, threshold))\n",
            encoding="utf-8",
        )
    shared = target / SHARED
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        '"""Geodesy helpers."""\n\n\ndef rate(a, b):\n    return a / b\n', encoding="utf-8"
    )
    (target / PARAMS).write_text("clean:\n  min_coherence: 0.5\n", encoding="utf-8")
    (target / "dvc.yaml").write_text("stages: {}\n", encoding="utf-8")
    for region in REGIONS:
        raw = target / "data/raw" / f"{region}.csv"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text("a\n1\n", encoding="utf-8")
