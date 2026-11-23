from __future__ import annotations

import argparse
import importlib
import json
import keyword
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .result_contract import make_result_envelope, render_command_result
from .scaffold import LockGenerationError, create_project, workflow_version

# Maintainer-only. The public export leaves the release machinery out.
release_cli: Any
try:
    release_cli = importlib.import_module("astack.release_cli")
except ModuleNotFoundError as exc:
    if exc.name != "astack.release_cli":
        raise
    release_cli = None


def _package_name(value: str) -> str:
    package = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if package and (package[0].isdigit() or keyword.iskeyword(package)):
        package = f"project_{package}"
    return package or "project"


# Variables that point Git at another repository, or stop it from finding an enclosing one.
_GIT_REPOSITORY_ENV = (
    "GIT_CEILING_DIRECTORIES",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)


def _git_environment() -> dict[str, str]:
    """Let Git find repositories from the directory alone, with English diagnostics."""
    environment = {k: v for k, v in os.environ.items() if k not in _GIT_REPOSITORY_ENV}
    environment["LC_ALL"] = "C"
    environment["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
    return environment


def _enclosing_repository(target: Path) -> Path | None:
    """Return the work tree that already contains `target`, or None when there is none."""
    existing = target.resolve()
    while not existing.exists():
        existing = existing.parent
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=existing if existing.is_dir() else existing.parent,
        env=_git_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip())
    # Git also says "not a git repository" when an enclosing .git exists but can't be read,
    # so absence counts only when no ancestor has a .git entry at all.
    markers = [
        str(p / ".git") for p in (existing, *existing.parents) if os.path.lexists(p / ".git")
    ]
    if "not a git repository" in result.stderr and not markers:
        return None
    detail = result.stderr.strip() or "git rev-parse failed"
    if markers:
        detail += f" (Git could not read {markers[0]})"
    # Any other failure (for example dubious ownership) may hide an enclosing repository.
    raise SystemExit(
        f"astack new could not tell whether {target} is inside a Git repository: "
        f"{detail}. Fix that, or pass --no-git."
    )


def _git_plan(target: Path, *, no_git: bool) -> tuple[bool, str]:
    """Decide before writing anything whether `astack new` will run `git init`."""
    if no_git:
        return False, "skipped (--no-git)"
    if shutil.which("git") is None:
        raise SystemExit(
            "astack new runs `git init`, but git was not found. Install git, or pass --no-git."
        )
    enclosing = _enclosing_repository(target)
    if enclosing is not None:
        return False, f"skipped (already inside the Git repository at {enclosing})"
    return True, "initialized"


def _prompt(label: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print(f"{label} is required.")


# Commands removed when the CLI was cut down, and what replaces each one.
RETIRED_COMMANDS = {
    "init": "Use `astack new <dir>`. It asks for missing details when run in a terminal.",
    "bootstrap": "`astack new` and `astack install` now write .pi/settings.json. "
    "Use `astack doctor` to check a project.",
    "setup": "`astack new` and `astack install` now write .pi/settings.json. "
    "Use `astack doctor` to check a project.",
    "upgrade-release": "Run `python .astack/workflow.py upgrade <tag>` in the project.",
    "validate": "Run `python .astack/workflow.py validate <tier>` in the project.",
}


def _project_details(args: argparse.Namespace) -> dict[str, Any]:
    """Flags win. In a terminal, ask for anything missing. Elsewhere, name what is missing."""
    values = {
        "project_name": args.name,
        "package_name": args.package,
        "description": args.description,
        "intended_use": args.intended_use,
    }
    missing = [key for key, value in values.items() if not value]
    if missing and not sys.stdin.isatty():
        flags = {"project_name": "--name", "package_name": "--package"}
        names = ", ".join(flags.get(key, "--" + key.replace("_", "-")) for key in missing)
        raise SystemExit(f"astack new: missing {names} (or run it in a terminal to be asked)")
    if not values["project_name"]:
        default = args.target.name.replace("-", " ").replace("_", " ").title()
        values["project_name"] = _prompt("Project name", default=default)
    if not values["package_name"]:
        values["package_name"] = _prompt(
            "Package name", default=_package_name(values["project_name"])
        )
    if not values["description"]:
        values["description"] = _prompt("Description")
    if not values["intended_use"]:
        values["intended_use"] = _prompt("Intended use")
    # Data science projects get DVC, which labels stale outputs (--no-dvc opts out), and Marimo,
    # which is required: the review notebook is the only place a person signs off code.
    defaults = {"marimo"} | (set() if args.no_dvc else {"dvc"})
    tools = sorted(set(args.tool) | defaults)
    return {**values, "tools": tools}


def _context_command(args: argparse.Namespace) -> str:
    parts = [args.command]
    value = getattr(args, "release_command", None)
    if value:
        parts.append(str(value))
    return " ".join(parts)


def _emit_result(result: object, cwd: Path, *, command: str) -> None:
    del cwd
    payload = dict(result) if isinstance(result, dict) else {"result": result}
    result_payload = dict(payload)
    # Only interpret explicit operation-level signals. Nested status fields describe
    # resources, not the execution of this command.
    raw_blockers = result_payload.get("blockers")
    blockers = (
        [
            item
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("summary"), str)
            else {"id": f"operation-blocker-{index}", "summary": str(item)}
            for index, item in enumerate(raw_blockers, 1)
        ]
        if isinstance(raw_blockers, list)
        else []
    )
    status = result_payload.get("status")
    outcome = "completed"
    if isinstance(status, str) and status in {
        "blocked",
        "BLOCKED",
        "CONFLICT_BLOCKED",
        "BLOCKED_ACCESS",
    }:
        outcome = "blocked"
    elif isinstance(status, str) and status in {
        "failed",
        "FAILED",
        "IMPLEMENTATION_FAILURE",
        "REVIEW_FAILURE",
    }:
        outcome = "failed"
    if blockers and outcome == "completed":
        outcome = "blocked"
    if outcome in {"blocked", "failed"} and not blockers:
        blockers = [{"id": "operation-status", "summary": f"Operation status: {status}"}]
    raw_evidence = result_payload.get("evidence")
    evidence = (
        [
            item
            for item in raw_evidence
            if isinstance(item, dict)
            and all(key in item for key in ("id", "summary", "required", "status"))
        ]
        if isinstance(raw_evidence, list)
        else []
    )
    readiness = result_payload.get("readiness")
    if not isinstance(readiness, dict) or not all(
        key in readiness for key in ("status", "summary", "required_evidence")
    ):
        readiness = None
    envelope = make_result_envelope(
        command="workflow-runtime",
        outcome=outcome,
        summary=f"{command} {outcome}."
        if readiness is None
        else f"{command} executed. Readiness: {readiness['status']}.",
        result={"operation": command, "payload": result_payload},
        blockers=blockers,
        evidence=evidence,
        readiness=readiness,
    )
    payload["result_envelope"] = envelope
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(render_command_result(envelope), file=sys.stderr)


def _with_errors(function: Any) -> Any:
    def wrapped() -> None:
        try:
            function()
        except SystemExit:
            raise
        except Exception as exc:
            command = "unknown"
            try:
                command = _context_command(_parser().parse_args())
            except (Exception, SystemExit):
                pass
            envelope = make_result_envelope(
                command="workflow-runtime",
                outcome="failed",
                summary=f"{command} failed.",
                result={
                    "operation": command,
                    "payload": {"error": {"type": type(exc).__name__, "message": str(exc)}},
                },
                blockers=[{"id": "command-failed", "summary": str(exc) or type(exc).__name__}],
            )
            print(
                json.dumps(
                    {
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "result_envelope": envelope,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            print(render_command_result(envelope), file=sys.stderr)
            raise SystemExit(1) from exc

    return wrapped


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="astack")
    parser.add_argument("--version", action="version", version=workflow_version())
    subparsers = parser.add_subparsers(dest="command", required=True)

    new = subparsers.add_parser(
        "new", help="Create a data science project (asks for missing details in a terminal)"
    )
    new.add_argument("target", type=Path)
    new.add_argument("--name")
    new.add_argument("--package")
    new.add_argument("--description")
    new.add_argument("--intended-use")
    new.add_argument("--tool", action="append", default=[], choices=("dvc", "marimo", "quarto"))
    new.add_argument(
        "--no-dvc",
        action="store_true",
        help="Do not add DVC, which runs the pipeline and labels stale outputs by default",
    )
    new.add_argument(
        "--no-git", action="store_true", help="Do not run `git init` in the new project"
    )

    install = subparsers.add_parser(
        "install", help="Add astack to an existing repository, or upgrade it (dry-run first)"
    )
    install.add_argument("target", type=Path)
    source = install.add_mutually_exclusive_group()
    source.add_argument("--release", help="Exact published release tag")
    source.add_argument(
        "--candidate", action="store_true", help="Explicit unpublished development candidate"
    )
    install.add_argument(
        "--check-argv", action="append", help="One exact check command as a JSON argv array"
    )
    install.add_argument(
        "--initialize", action="store_true", help="Initialize an empty destination"
    )
    install.add_argument("--apply", action="store_true")
    install.add_argument("--approve-plan")
    install.add_argument(
        "--recover", action="store_true", help="Plan recovery of an interrupted install"
    )

    doctor = subparsers.add_parser(
        "doctor", help="Check that a project's tools and skill files are in place"
    )
    doctor.add_argument("--root", type=Path, default=Path.cwd())
    doctor.add_argument(
        "--harness", choices=("opencode", "pi", "codex", "claude-code"), required=True
    )

    policy = subparsers.add_parser(
        "change-policy", help="Switch a project between General and Data Science (dry-run first)"
    )
    policy.add_argument("target", type=Path)
    policy.add_argument("--profile", choices=("general", "data-science"), required=True)
    policy.add_argument("--capability", action="append", default=[])
    policy_source = policy.add_mutually_exclusive_group(required=True)
    policy_source.add_argument("--release")
    policy_source.add_argument("--candidate", action="store_true")
    policy.add_argument("--apply", action="store_true")
    policy.add_argument("--approve-plan")

    skills = subparsers.add_parser(
        "skills", help="Verify the pinned skills, or copy them to a folder outside a project"
    )
    skills.add_argument("--dest", type=Path, help="Explicit provider skill discovery directory")
    skills.add_argument("--apply", action="store_true", help="Copy after full ownership preflight")
    if release_cli is not None:
        release_cli.register(subparsers)
    return parser


@_with_errors
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in RETIRED_COMMANDS:
        raise SystemExit(f"astack {sys.argv[1]} was removed. {RETIRED_COMMANDS[sys.argv[1]]}")
    args = _parser().parse_args()
    if args.command == "change-policy":
        from .installation import transition_project_policy

        _emit_result(
            transition_project_policy(
                args.target,
                profile=args.profile,
                capabilities=args.capability,
                release=args.release,
                candidate=args.candidate,
                apply=args.apply,
                approve_plan=args.approve_plan,
            ),
            args.target,
            command="change-policy",
        )
        return
    if args.command == "skills":
        from .scaffold import repository_root
        from .upstream_skills import LOCK, install, payload

        root = repository_root()
        if args.dest is None:
            if args.apply:
                raise SystemExit("astack skills: --apply requires --dest")
            print(json.dumps({"verified_files": len(payload(root)), "lock": str(LOCK)}))
        else:
            print(json.dumps(install(root, args.dest, apply=args.apply), indent=2))
        return
    if args.command == "install":
        from .installation import install_project, recover_installation

        try:
            if args.recover:
                install_result = recover_installation(
                    args.target, apply=args.apply, approve_plan=args.approve_plan
                )
            else:
                commands = (
                    [json.loads(value) for value in args.check_argv] if args.check_argv else None
                )
                install_result = install_project(
                    args.target,
                    release=args.release,
                    candidate=args.candidate,
                    commands=commands,
                    initialize=args.initialize,
                    apply=args.apply,
                    approve_plan=args.approve_plan,
                )
        except (ValueError, OSError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        _emit_result(install_result, args.target, command="install")
        return
    if args.command == "doctor":
        from .environment import doctor

        root = args.root.resolve()
        environment_result = doctor(root, args.harness)
        _emit_result(environment_result, root, command=args.command)
        # Keep actionable diagnostics visible without requiring JSON inspection.
        for check in environment_result.get("checks", []):
            if check["status"] != "READY":
                print(f"{check['id']}: {check['status']} - {check['summary']}", file=sys.stderr)
                print(f"  Recovery: {check['recovery']}", file=sys.stderr)
        if environment_result.get("status") == "BLOCKED":
            raise SystemExit(1)
        return
    if args.command == "new":
        details = _project_details(args)
        initialize_git, git_status = _git_plan(args.target, no_git=args.no_git)
        try:
            files = create_project(args.target, lock=True, **details)
        except LockGenerationError as exc:
            print(
                f"Project generated at {args.target}, but setup did not finish: {exc}",
                file=sys.stderr,
            )
            print(f"Run `cd {args.target}` and `uv lock` to retry.", file=sys.stderr)
            if initialize_git:
                print(
                    "Git was not initialized yet. Also run `git init -b main` there.",
                    file=sys.stderr,
                )
            raise SystemExit(1) from exc
        if initialize_git:
            initialized = subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=args.target,
                env=_git_environment(),
                check=False,
                capture_output=True,
                text=True,
            )
            if initialized.returncode != 0:
                print(
                    f"Project generated at {args.target}, but `git init` failed: "
                    f"{initialized.stderr.strip()}",
                    file=sys.stderr,
                )
                print(f"Run `cd {args.target}` and `git init` to retry.", file=sys.stderr)
                raise SystemExit(1)
        _emit_result(
            {"status": "created", "target": str(args.target), "files": files, "git": git_status},
            Path.cwd(),
            command=args.command,
        )
        print(f"\nGit: {git_status}. Nothing is committed yet.", file=sys.stderr)
        print(
            f"Next: cd {args.target} && just check, then open it in your agent.",
            file=sys.stderr,
        )
        return
    if args.command == "release" and release_cli is not None:
        cwd = Path.cwd()
        _emit_result(release_cli.run(args, cwd), cwd, command=_context_command(args))
        return
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
