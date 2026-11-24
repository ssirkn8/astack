from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    ALLOWED_SCOPES,
    VALIDATION_TIERS,
    Any,
    Path,
    _ensure_cache_ignored,
    argparse,
    json,
    output_contract_summary,
    share_raw_inputs,
    sys,
)
from .project_state import (
    audit,
)
from .publication import (
    export_public,
    publish_audit,
)
from .reports import (
    report_check,
)
from .reproduction import (
    run_acquisition,
    run_dvc_cache_gc,
    run_dvc_cache_status,
    run_dvc_status,
    run_reproduction,
    run_validation,
)
from .review import pending_reviews, review_status, run_review_policy
from .sources import (
    source_checksum,
    verify_source,
)
from .staleness import output_status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflow.py", description="Project workflow checks and helpers, run from the root."
    )
    # A metavar keeps the hidden compatibility alias out of the command list.
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="Plan or apply an upgrade to a published astack release (defaults to latest stable)",
    )
    upgrade_parser.add_argument(
        "release",
        nargs="?",
        default=None,
        help="Exact published release tag such as v1.2.1 (defaults to latest stable release)",
    )
    upgrade_parser.add_argument("--apply", action="store_true")
    upgrade_parser.add_argument(
        "--approve-plan", help="Exact dry-run plan_digest to apply (implies --apply)"
    )
    audit_parser = subparsers.add_parser(
        "audit", help="Check workflow conformance, provenance and evidence (just ship-ready)"
    )
    output_contract_parser = subparsers.add_parser(
        "output-contract", help="Read the declared scientific output contract summary"
    )
    output_contract_parser.add_argument(
        "--analysis", help="One analysis from the registry's analyses (default: whole project)"
    )
    # `--work-unit` was this option's old name.
    output_contract_parser.add_argument("--work-unit", dest="analysis", help=argparse.SUPPRESS)
    audit_parser.add_argument("--format", choices=["json", "text"], default="text")
    audit_parser.add_argument(
        "--run-tools", action="store_true", help="Also run the project's configured checks"
    )
    audit_parser.add_argument(
        "--strict", action="store_true", help="Exit non-zero unless the project is ship-ready"
    )
    audit_parser.add_argument(
        "--analysis",
        help="Check this registry analysis: it must exist and its confirmation must be current",
    )
    # `--output-scope` was this option's old name.
    audit_parser.add_argument("--output-scope", dest="analysis", help=argparse.SUPPRESS)
    audit_parser.add_argument(
        "--ci",
        action="store_true",
        help="Explicit limited engineering/metadata CI, not scientific release proof",
    )
    validation_parser = subparsers.add_parser(
        "validate", help="Run one declared validation tier from .astack/project.toml"
    )
    validation_parser.add_argument("tier", choices=("check", *VALIDATION_TIERS))

    file_audit_help = "File an audit report as a GitHub issue, or keep it local"
    file_audit_parser = subparsers.add_parser("file-audit", help=file_audit_help)
    # `publish` was this command's old name. It never published the project; see publish-public.
    legacy_parser = subparsers.add_parser("publish", description=file_audit_help)
    for audit_report_parser in (file_audit_parser, legacy_parser):
        audit_report_parser.add_argument("--report", type=Path, required=True)
        audit_report_parser.add_argument("--commit", required=True)
        audit_report_parser.add_argument("--scope", choices=sorted(ALLOWED_SCOPES), required=True)
        audit_report_parser.add_argument(
            "--to",
            dest="publish",
            choices=["auto", "github", "local", "none"],
            default="auto",
            help="Where the report goes. auto files an issue when GitHub is available",
        )
        # `--publish` was the destination option's old name.
        audit_report_parser.add_argument(
            "--publish",
            dest="publish",
            choices=["auto", "github", "local", "none"],
            help=argparse.SUPPRESS,
        )
        audit_report_parser.add_argument("--actionable-count", type=int, required=True)
        audit_report_parser.add_argument("--remove-on-success", action="store_true")

    share_raw_parser = subparsers.add_parser(
        "share-raw",
        help="Link immutable raw inputs from the canonical checkout into this linked worktree",
    )
    share_raw_parser.add_argument("--apply", action="store_true")
    reproduce_parser = subparsers.add_parser(
        "reproduce", help="Run the declared reproduction and record the run (just reproduce)"
    )
    reproduce_parser.add_argument(
        "--promote",
        action="store_true",
        help="Also write a durable run record under evidence/ or reports/qa/.",
    )
    reproduce_parser.add_argument(
        "--verify",
        action="store_true",
        help="Fail unless the rerun matches a promoted reference record.",
    )
    subparsers.add_parser(
        "acquire", help="Run the declared [acquisition] command to obtain inputs (just acquire)"
    )
    export_parser = subparsers.add_parser(
        "export-public",
        help="Write an allowlisted, history-free public copy of the committed project",
    )
    export_parser.add_argument("--out", type=Path, required=True)
    verify_source_parser = subparsers.add_parser(
        "verify-source",
        help="Hash bytes retrieved for an external source and write its verification record",
    )
    verify_source_parser.add_argument("source_id")
    verify_source_parser.add_argument("retrieved", type=Path)
    checksum_parser = subparsers.add_parser(
        "checksum",
        help="Print the raw_checksum value for a file (sha256) or directory (sha256-tree-v2)",
    )
    checksum_parser.add_argument("path", type=Path)
    report_check_parser = subparsers.add_parser(
        "report-check",
        help="Check that report figures and cited numbers point at evidence (just report-check)",
    )
    report_check_parser.add_argument(
        "reports", nargs="*", type=Path, help="Reports to check (default: every report in reports/)"
    )
    subparsers.add_parser(
        "outputs", help="Label each pipeline output current, outdated, missing or unknown"
    )
    review_parser = subparsers.add_parser(
        "review-status",
        help="Show what the review notebook shows: each pipeline step and its sign-off",
    )
    review_parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="Fail unless a person signed off every step and shared file as it is now (RELEASE)",
    )
    review_policy_parser = subparsers.add_parser(
        "review-policy",
        help="Classify changed or explicit paths for review risk, paths, and required axes",
    )
    review_policy_parser.add_argument(
        "--root",
        type=Path,
        help="Target project checkout root to inspect as data (default: current directory)",
    )
    review_policy_parser.add_argument(
        "--base",
        help="Git base ref or commit to compare against (default: resolved default/CI base branch)",
    )
    review_policy_parser.add_argument(
        "--risk",
        choices=("auto", "low", "medium", "high", "critical"),
        default="auto",
        help="Optional requested risk floor (cannot lower the computed floor)",
    )
    review_policy_parser.add_argument(
        "--scientific-changes",
        action="store_true",
        help="Explicitly mark the changes as touching scientific claims or analysis",
    )
    review_policy_parser.add_argument(
        "paths",
        nargs="*",
        help="Explicit relative paths to classify instead of inspecting Git changes",
    )
    subparsers.add_parser("dvc-status", help="Show DVC pipeline status (DVC projects only)")
    dvc_cache_parser = subparsers.add_parser(
        "dvc-cache", help="Inspect or clean this project's DVC cache (DVC projects only)"
    )
    dvc_cache_commands = dvc_cache_parser.add_subparsers(dest="dvc_cache_command", required=True)
    dvc_cache_commands.add_parser("status")
    dvc_cache_gc = dvc_cache_commands.add_parser("gc")
    dvc_cache_gc.add_argument("--apply", action="store_true")
    return parser


def _strict_ready(result: dict[str, Any]) -> bool:
    """Exploratory work cannot claim reproduction, so it is gated on its software checks."""
    verdicts = result["verdicts"]
    reproducibility = str(verdicts["computational_reproducibility"])
    if result.get("analysis_stage", "material") == "material":
        reproducibility_ready = reproducibility == "pass"
    else:
        reproducibility_ready = (
            reproducibility != "fail" and str(verdicts.get("software_checks")) == "pass"
        )
    return (
        str(verdicts["workflow_conformance"]) == "pass"
        and reproducibility_ready
        and str(verdicts["release_readiness"]) != "fail"
    )


def _context_command(args: argparse.Namespace) -> str:
    parts = [args.command]
    value = getattr(args, "dvc_cache_command", None)
    if value:
        parts.append(str(value))
    return " ".join(parts)


def _emit_result(result: object, cwd: Path, *, command: str) -> None:
    del cwd, command
    payload = dict(result) if isinstance(result, dict) else {"result": result}
    print(json.dumps(payload, indent=2, sort_keys=True))


def _with_errors(function: Any) -> Any:
    def wrapped() -> None:
        try:
            function()
        except SystemExit:
            raise
        except Exception as exc:
            print(
                json.dumps(
                    {"error": {"type": type(exc).__name__, "message": str(exc)}},
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

    return wrapped


@_with_errors
def main() -> None:
    args = _parser().parse_args()
    if args.command == "upgrade":
        try:
            from release_upgrade import upgrade_release  # type: ignore[import-not-found]
        except ImportError:
            from astack.release_upgrade import upgrade_release

        # Check `is not None` so `--approve-plan ""` still attempts apply and fails closed.
        result = upgrade_release(
            Path.cwd(),
            args.release,
            apply=bool(args.apply or args.approve_plan is not None),
            approve_plan=args.approve_plan,
        )
        _emit_result(result, Path.cwd(), command="upgrade")
        return
    if args.command == "output-contract":
        print(
            json.dumps(output_contract_summary(Path.cwd(), args.analysis), indent=2, sort_keys=True)
        )
        return
    if args.command == "audit":
        cwd = Path.cwd().resolve()
        if args.ci and not (args.strict and args.run_tools):
            raise SystemExit("--ci requires --strict --run-tools")
        options: dict[str, Any] = {"ci": True} if args.ci else {}
        if args.analysis:
            options["output_scope"] = args.analysis
        result = audit(cwd, run_tools=args.run_tools, strict=args.strict, **options)
        if args.format == "json":
            _emit_result(result, cwd, command=_context_command(args))
        else:
            print("Workflow audit")
            for key, value in result["verdicts"].items():
                print(f"- {key}: {value}")
            for finding in result["findings"]:
                print(
                    f"- {finding['priority']} [{finding['area']}] "
                    f"{finding['message']} ({finding['evidence']})"
                )
        if args.ci:
            if args.format == "text":
                print(
                    "Limited CI: source identity/reproduction NOT VERIFIED. "
                    "Strict local ship-ready required."
                )
            if result.get("ci_validation", {}).get("status") != "pass":
                raise SystemExit(1)
        elif args.strict and not _strict_ready(result):
            raise SystemExit(1)
        return
    if args.command == "validate":
        cwd = Path.cwd().resolve()
        _emit_result(run_validation(cwd, args.tier), cwd, command=_context_command(args))
        return
    if args.command == "share-raw":
        cwd = Path.cwd().resolve()
        _emit_result(share_raw_inputs(cwd, apply=args.apply), cwd, command=_context_command(args))
        return
    if args.command == "reproduce":
        cwd = Path.cwd().resolve()
        _ensure_cache_ignored(cwd)
        result = run_reproduction(cwd, promote=args.promote, verify=args.verify)
        _emit_result(result, cwd, command=_context_command(args))
        if result["status"] == "mismatch" or (args.verify and result["status"] != "verified"):
            raise SystemExit(1)
        return
    if args.command == "acquire":
        cwd = Path.cwd().resolve()
        _emit_result(run_acquisition(cwd), cwd, command=_context_command(args))
        return
    if args.command == "export-public":
        cwd = Path.cwd().resolve()
        _emit_result(export_public(cwd, args.out), cwd, command=_context_command(args))
        return
    if args.command == "verify-source":
        cwd = Path.cwd().resolve()
        result = verify_source(cwd, args.source_id, args.retrieved.resolve())
        _emit_result(result, cwd, command=_context_command(args))
        return
    if args.command == "checksum":
        cwd = Path.cwd().resolve()
        _emit_result(source_checksum(args.path), cwd, command=_context_command(args))
        return
    if args.command == "report-check":
        cwd = Path.cwd().resolve()
        result = report_check(cwd, args.reports)
        _emit_result(result, cwd, command=_context_command(args))
        if result["status"] in {"failed", "unavailable"}:
            raise SystemExit(1)
        return
    if args.command == "review-status":
        cwd = Path.cwd().resolve()
        if args.require_reviewed:
            pending = pending_reviews(cwd)
            _emit_result(
                {"status": "failed" if pending else "passed", "pending": pending},
                cwd,
                command=_context_command(args),
            )
            if pending:
                raise SystemExit(1)
            return
        _emit_result(review_status(cwd), cwd, command=_context_command(args))
        return
    if args.command == "review-policy":
        target_root = (args.root or Path.cwd()).resolve()
        result = run_review_policy(
            target_root,
            base=args.base,
            paths=args.paths or None,
            requested_risk=args.risk,
            scientific_changes=args.scientific_changes,
        )
        _emit_result(result, target_root, command=_context_command(args))
        return
    if args.command == "outputs":
        cwd = Path.cwd().resolve()
        _emit_result(output_status(cwd), cwd, command=_context_command(args))
        return
    if args.command == "dvc-status":
        cwd = Path.cwd().resolve()
        _emit_result(run_dvc_status(cwd), cwd, command=_context_command(args))
        return
    if args.command == "dvc-cache":
        root = Path.cwd().resolve()
        result = (
            run_dvc_cache_status(root)
            if args.dvc_cache_command == "status"
            else run_dvc_cache_gc(root, apply=args.apply)
        )
        _emit_result(result, root, command=_context_command(args))
        return
    if args.command not in {"file-audit", "publish"}:
        raise SystemExit(f"Unknown command: {args.command}")
    cwd = Path.cwd().resolve()
    result = publish_audit(
        cwd,
        args.report,
        commit=args.commit,
        scope=args.scope,
        publish=args.publish,
        actionable_count=args.actionable_count,
        remove_on_success=args.remove_on_success,
    )
    _emit_result(result, cwd, command=_context_command(args))
    if args.publish == "github" and result["status"] != "github":
        raise SystemExit(1)
