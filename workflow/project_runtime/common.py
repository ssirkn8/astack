# ruff: noqa: F401 -- imports are shared with responsibility modules.
from __future__ import annotations

import argparse
import codecs
import configparser
import errno
import fnmatch
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import re
import secrets
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import zlib
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, BinaryIO
from urllib.parse import unquote

# The runtime runs from `.astack/` inside the project. Writing bytecode there would leave
# `__pycache__/` files that a General project's `.gitignore` does not cover.
sys.dont_write_bytecode = True


def _load_validation_session() -> Any:
    current = Path(__file__).resolve()
    candidates = [current.parent / "validation" / "validation_session.py"]
    candidates.append(current.parent / "validation_session.py")
    if current.parent.name == "project_runtime":
        candidates.append(current.parent.parent / "validation_session.py")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ds_validation_session", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ValidationSession
    raise ImportError("The vendored validation-session runtime is unavailable")


ValidationSession = _load_validation_session()

try:
    from project_policy import (  # type: ignore[import-not-found]
        RISK_LEVELS,
        project_config,
        project_profile,
        review_policy,
        review_policy_for_config,
        run_project_validation,
        validation_commands,
    )
except ImportError:
    from astack.project_policy import (
        RISK_LEVELS,
        project_config,
        project_profile,
        review_policy,
        review_policy_for_config,
        run_project_validation,
        validation_commands,
    )

try:
    from diagnostics import (  # type: ignore[import-not-found]
        load_summary as load_diagnostic_summary,
    )
except ImportError:
    from astack.diagnostics import load_summary as load_diagnostic_summary

try:
    from output_contract import output_contract_summary  # type: ignore[import-not-found]
except ImportError:
    from astack.output_contract import output_contract_summary

try:
    from git_state import (  # type: ignore[import-not-found]
        EXTERNAL_ROOT_ENV_PREFIX,
        canonical_checkout,
        data_root,
        dvc_cache_configured_path,
        dvc_cache_root,
        external_root,
        list_worktrees,
        share_raw_inputs,
        state_path,
    )
except ImportError:
    from astack.git_state import (
        EXTERNAL_ROOT_ENV_PREFIX,
        canonical_checkout,
        data_root,
        dvc_cache_configured_path,
        dvc_cache_root,
        external_root,
        list_worktrees,
        share_raw_inputs,
        state_path,
    )

markdown_it: Any
try:
    from markdown_it import MarkdownIt as markdown_it_parser
except ImportError:  # pragma: no cover - report-check reports itself unavailable
    markdown_it = None
else:
    markdown_it = markdown_it_parser

yaml: Any
try:
    import yaml as yaml_module
except ImportError:  # pragma: no cover - reported as an unavailable check
    yaml = None
else:
    yaml = yaml_module

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
ALLOWED_SCOPES = {"auto", "workflow", "science", "full", "public-release"}
SOURCE_REQUIRED_FIELDS = {
    "allowed_outputs",
    "title",
    "path",
    "origin",
    "provider",
    "access_date",
    "version",
    "license",
    "sensitivity",
    "redistribution",
    "citation",
}
EPISTEMIC_FIELDS = {"license", "sensitivity", "redistribution", "citation"}
# OS browse metadata excluded from raw tree digests and coverage, plus AppleDouble "._*" files.
OS_METADATA_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
EPISTEMIC_STATES = {"known", "unknown", "not_applicable", "not_yet_verified"}
SOURCE_IDENTITY_METHODS = {
    "dvc",
    "immutable_version",
    "object_generation",
    "sha256",
    "sha256_tree",
    "other",
}
MANIFEST_NAME = ".astack/manifest.json"
PLACEHOLDER_EVIDENCE_VALUES = frozenset(
    {
        "tbd",
        "to be determined",
        "unknown",
        "not available",
        "not provided",
        "pending",
        "placeholder",
        "todo",
        "yyyy-mm-dd",
        "n/a",
        "na",
    }
)
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[:=]\s*"
        r"['\"]?[A-Za-z0-9_./+\-=]{12,}"
    ),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
]
# report-check: where figures and cited numbers may point, and the patterns it reads.
REPORT_EVIDENCE_ROOTS = ("evidence/", "artifacts/", "reports/figures/", "reports/tables/")
REPORT_CITED_VALUE = re.compile(
    r"[-+\u2212]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?"
)
REPORT_UNRESOLVED = re.compile(r"\[UNRESOLVED\b")
# The only raw HTML a report may contain: one or more well-formed comments. A comment may
# not open with `>` or `->` and may not contain `--!>`, which browsers read differently.
REPORT_HTML_COMMENTS = re.compile(r"\s*(?:<!--(?!-?>)(?:(?!--!?>).)*-->\s*)+", re.S)
# Pandoc and Quarto read `{...}` after an image or link as attributes. Only these are safe:
# they can't load another file or change visible text.
REPORT_SAFE_ATTRIBUTE = re.compile(
    r"#[\w-]+|\.[\w-]+|(?:width|height)=(?:\"[\w.%]*\"|'[\w.%]*'|[\w.%]+)"
)
REPORT_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
SENSITIVE_FILENAMES = {".env", ".envrc", "credentials.json", "secrets.yml", "secrets.yaml"}
REDISTRIBUTION_DENIALS = (
    "prohibit",
    "not allowed",
    "not permitted",
    "no redistribution",
    "forbidden",
    "restricted",
    "not for redistribution",
    "denied",
    "deny",
    "not_yet_verified",
    "not yet verified",
    "not_applicable",
    "unknown",
)


def _is_sensitive_filename(name: str) -> bool:
    """Shared by the audit and the exporter, case-insensitively; `.env.example` is allowed."""
    folded = name.casefold()
    return folded in SENSITIVE_FILENAMES or (
        folded.startswith(".env.") and folded != ".env.example"
    )


BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".parquet", ".zip"}
RAW_BASE_SHA_ENV = "ASTACK_AUDIT_BASE_SHA"
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "AUX",
        "CON",
        "NUL",
        "PRN",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
WINDOWS_UNSAFE_PATH_CHARS = frozenset('<>:"|?*')
REPRODUCTION_ENGINES = {"command", "dvc"}
RUN_RECORD_SCHEMA_VERSION = 2
RANDOM_SEED_ENV = "ASTACK_RANDOM_SEED"
ACQUISITION_DIR_ENV = "ASTACK_ACQUISITION_DIR"
# An in-place source path names a machine-local external root: "root:<name>/<relative path>".
IN_PLACE_PREFIX = "root:"
IN_PLACE_REASONS = ("size", "license")
DEFAULT_SNAPSHOT_LIMIT_GB = 5
# `uv run` options that take no value and do not select a different environment.
UV_RUN_SAFE_FLAGS = frozenset(
    {"--locked", "--frozen", "--offline", "--no-sync", "--quiet", "-q", "--no-progress"}
)
# Settings that change numerical results or their order, recorded when present (never required).
RUNTIME_SETTING_ENV = (
    "PYTHONHASHSEED",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "CUBLAS_WORKSPACE_CONFIG",
    "GDAL_DATA",
    "PROJ_DATA",
    "PROJ_NETWORK",
)
# Runs in the project interpreter. Native numeric and geospatial libraries are detected, never
# required, and imported only when their distribution is installed.
# Asks DVC itself for its resolved pipeline: foreach and matrix stages, wdir, frozen stages and
# the files a directory output tracks after .dvcignore. Runs in the project's own environment.
# The dependency check runs each DVC stage with this hook loaded into every Python process it
# starts (through PYTHONPATH). It records files opened for reading, project modules imported
# and programs started. It cannot see files that compiled libraries open directly.
TRACE_FILE_ENV = "ASTACK_TRACE_FILE"
TRACE_SITECUSTOMIZE = """
import json, os, sys

def _astack_trace():
    path = os.environ.get("ASTACK_TRACE_FILE")
    if not path:
        return
    # DVC's own process runs the stage and is not part of it. Its children still load this hook.
    argv = list(getattr(sys, "orig_argv", []))
    launcher = [os.path.basename(item) for item in argv[1:3]]
    if launcher[:1] == ["dvc"] or argv[1:3] == ["-m", "dvc"]:
        return
    try:
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    except OSError:
        return
    busy = [False]

    def emit(kind, value):
        line = json.dumps({"pid": os.getpid(), "kind": kind, "value": value})
        try:
            os.write(handle, (line + "\\n").encode())
        except OSError:
            pass

    def name(value):
        if isinstance(value, (str, bytes, os.PathLike)):
            return os.path.abspath(os.fsdecode(value))
        return None

    hook_dir = os.path.dirname(os.path.abspath(__file__))

    def text(value):
        return os.fsdecode(value) if isinstance(value, (str, bytes, os.PathLike)) else str(value)

    def lookup(env, key):
        # Mappings may use str keys (os.environ) or bytes keys (os.environb), never both.
        for candidate in (key, key.encode()):
            try:
                if candidate in env:
                    return env[candidate]
            except Exception:
                continue
        return None

    def launch(program, argv, env):
        # Whether the launched process gets this hook. env None inherits the current environment,
        # which the stage may itself have changed.
        if env is None:
            env = os.environ
        try:
            path = text(lookup(env, "PYTHONPATH") or "")
            hooked = hook_dir in path.split(os.pathsep) and bool(lookup(env, "ASTACK_TRACE_FILE"))
        except Exception:
            hooked = False
        value = {"program": text(program), "argv": [text(item) for item in argv], "hooked": hooked}
        emit("spawn", value)

    def hook(event, args):
        if busy[0]:
            return
        busy[0] = True
        try:
            if event == "open" and args:
                path, mode, flags = (list(args) + [None, None, None])[:3]
                # Anything that can read counts, including r+ and O_RDWR.
                readable = True
                if isinstance(flags, int):
                    readable = (flags & os.O_ACCMODE) != os.O_WRONLY
                elif isinstance(mode, str):
                    readable = "r" in mode or "+" in mode
                target = name(path)
                if target and readable:
                    emit("read", target)
            elif event == "subprocess.Popen" and args:
                program, argv = args[0], args[1]
                if isinstance(argv, (str, bytes)):
                    launch("/bin/sh", [argv], args[3] if len(args) > 3 else None)
                else:
                    argv = list(argv or [])
                    if program is None and argv:
                        program = argv[0]
                    launch(program, argv, args[3] if len(args) > 3 else None)
            elif event in ("os.exec", "os.posix_spawn") and args:
                rest = list(args) + [None, None]
                launch(rest[0], rest[1] or [], rest[2])
            elif event == "os.spawn" and len(args) > 2:
                launch(args[1], args[2], args[3] if len(args) > 3 else None)
            elif event in ("os.system", "os.startfile"):
                launch("/bin/sh", [], None)
        except Exception:
            pass
        finally:
            busy[0] = False

    def modules():
        busy[0] = True
        for module in list(sys.modules.values()):
            target = name(getattr(module, "__file__", None))
            if target:
                emit("read", target)

    import atexit
    emit("start", os.getppid())
    atexit.register(modules)
    sys.addaudithook(hook)

_astack_trace()
del _astack_trace

def _astack_chain():
    # Run the project's own sitecustomize, if any, which this hook's directory shadows.
    import importlib.machinery, importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    search = [entry for entry in sys.path if os.path.abspath(entry or ".") != here]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", search)
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

_astack_chain()
del _astack_chain
"""
# Top-level tooling folders the per-stage check never looks in. Read-only raw inputs are also
# skipped by the write snapshot, because the whole-run raw check already refuses any change.
SNAPSHOT_SKIPPED = frozenset(
    {".git", ".venv", ".dvc", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
)
# Bytecode caches are skipped wherever they are. A .pyc read counts as a read of its source.
BYTECODE_CACHE = "__pycache__"
DEPENDENCY_CHECK_PATH = ".cache/dependency-check.json"
# While a stage runs, derived files it does not declare are made unreadable, so a read by any
# program fails. This journal records their modes first, so they are restored even after a crash.
INPUT_LOCK_JOURNAL = ".cache/input-lock.json"
# Bump when the check itself changes, so checks saved by an older version no longer count.
DEPENDENCY_CHECK_VERSION = 6
# Shell syntax means the stage runs more than one program, so its reads cannot all be traced.
SHELL_SYNTAX = ("&&", "||", ";", "|", ">", "<", "$(", "`")
DEPENDENCY_CHECK_LIMITS = (
    "Writes are checked for every file that persists, from a snapshot before and after each "
    "stage. Derived files a stage does not declare are unreadable while it runs, so a read of one "
    "by a program the stage starts fails the stage. A process that opened a file before the stage "
    "began, such as a long-running server, is not affected, and a process the stage leaves "
    "running, such as a worker holding a writable memory mapping, can still change files after "
    "the check. Symlinks, hard links and files an ACL "
    "keeps readable are reported as lock gaps. Other reads are checked only where Python opens or "
    "imports a file."
)
DVC_PIPELINE_PROBE = """
import json, os, stat, sys
from dvc.dependency import ParamsDependency
from dvc.exceptions import DvcException
from dvc.repo import Repo
from dvc.stage import PipelineStage
stages_only = "--stages-only" in sys.argv[1:]
if os.name != "nt":
    os.umask(0o077)
if not os.path.isdir(".dvc") or os.path.islink(".dvc"):
    raise RuntimeError("DVC is not initialized in this directory")
with Repo(".") as repo:
    top = os.path.abspath(repo.root_dir)
    real_top = os.path.realpath(top)
    # DVC may read default interpolation variables before it exposes any stages.
    original_open = repo.fs.open
    def open_regular(path, mode="r", *args, **kwargs):
        if "r" in mode and not stat.S_ISREG(os.stat(path).st_mode):
            raise OSError(f"{rel(path)} is not a regular file")
        return original_open(path, mode, *args, **kwargs)
    repo.fs.open = open_regular
    def rel(path):
        return os.path.relpath(os.path.abspath(path), top).replace(os.sep, "/")
    def real(path):
        return os.path.relpath(os.path.realpath(path), real_top).replace(os.sep, "/")
    def dep_entry(dep):
        entry = {"path": rel(dep.fs_path), "real": real(dep.fs_path)}
        if isinstance(dep, ParamsDependency):
            try:
                values = dep.read_params()
            except (DvcException, OSError, ValueError) as error:
                problem = f"Could not read parameters from {entry['path']}: {error}"
                if not stages_only:
                    raise RuntimeError(problem) from error
                return entry | {"params_problem": problem}
            wanted = list(dep.params) or list(values)
            entry["params"] = {key: values[key] for key in wanted if key in values}
            entry["params_missing"] = [key for key in wanted if key not in values]
            if not wanted:
                entry["params_missing"] = ["(the whole file)"]
        return entry
    stages = []
    for stage in repo.index.stages:
        if not isinstance(stage, PipelineStage):
            continue
        outs = []
        for out in stage.outs:
            path = os.path.abspath(out.fs_path)
            entry = {"path": rel(path), "real": real(path), "dir": os.path.isdir(path)}
            if entry["dir"]:
                tracked = sorted(repo.dvcignore.find(repo.fs, path))
                entry["files"] = [rel(item) for item in tracked]
                entry["aliases"] = {
                    real(item): rel(item) for item in tracked if os.path.islink(item)
                }
            outs.append(entry)
        deps = [dep_entry(dep) for dep in stage.deps]
        settings = {"always_changed": True} if stage.always_changed else {}
        for out in stage.outs:
            flags = {}
            if not out.use_cache:
                flags["cache"] = False
            if out.persist:
                flags["persist"] = True
            if not out.can_push:
                flags["push"] = False
            if out.remote:
                flags["remote"] = out.remote
            if flags:
                settings["output " + rel(out.fs_path)] = flags
        stages.append({
            "name": stage.addressing,
            "frozen": bool(stage.frozen),
            "cmd": stage.cmd,
            "wdir": rel(stage.wdir),
            "dvcfile": rel(stage.path),
            "settings": settings,
            "deps": deps,
            "outs": outs,
        })
    targets = json.loads(os.environ.get("ASTACK_DVC_TARGETS") or "null")
    selected = None
    if targets:
        selected = sorted({
            item.addressing
            for target in targets
            for item in repo.stage.collect(target, with_deps=True)
        })
    status = None if stages_only else repo.status()
    print(json.dumps({"status": status, "stages": stages, "selected": selected}))
"""
ENVIRONMENT_PROBE = """
import importlib.metadata as metadata, json, platform, sys
distributions = {}
for item in metadata.distributions():
    name = item.metadata.get("Name")
    if name:
        distributions[name] = item.version
installed = {name.casefold() for name in distributions}
native = {}
def probe(key, distribution, read):
    if distribution.casefold() in installed:
        try:
            native[key] = read()
        except Exception as exc:
            native[key] = {"status": "unavailable", "reason": type(exc).__name__}
def blas():
    import numpy
    config = numpy.show_config(mode="dicts")
    return config.get("Build Dependencies", {}).get("blas", {})
probe("numpy", "numpy", lambda: __import__("numpy").__version__)
probe("numpy_blas", "numpy", blas)
probe("gdal", "GDAL", lambda: __import__("osgeo.gdal", fromlist=["gdal"]).__version__)
probe("proj", "pyproj", lambda: __import__("pyproj").proj_version_str)
probe("pyproj", "pyproj", lambda: __import__("pyproj").__version__)
probe("rasterio_gdal", "rasterio", lambda: __import__("rasterio").__gdal_version__)
probe("geos", "shapely", lambda: __import__("shapely").geos_version_string)
print(json.dumps({
    "python": {"implementation": platform.python_implementation(),
               "version": platform.python_version()},
    "platform": {"system": platform.system(), "release": platform.release(),
                 "machine": platform.machine()},
    "distributions": dict(sorted(distributions.items(), key=lambda kv: kv[0].casefold())),
    "native": native,
}, sort_keys=True))
"""
DVC_PIPELINE_FIELDS = ("outs", "outs_no_cache", "metrics", "plots")
DVC_CONTROL_PATHS = {"dvc.lock"}
# A person's record that they signed off a pipeline step or shared code. Workflow metadata, so it
# stays out of the derived folders that pipelines read.
REVIEW_RECORD_ROOT = ".astack/reviews"
# The review notebook shows at most this many code lines per file, then says so.
REVIEW_DIFF_LINE_LIMIT = 1500
# An output preview shows at most this many table rows or bytes of text, then says so.
REVIEW_PREVIEW_ROWS = 100_000
REVIEW_PREVIEW_TEXT_BYTES = 200_000
REVIEW_CLAIMS_PER_PAGE = 25
REVIEW_INLINE_CONTEXT_CHARS = 240
# What each sign-off state is called on screen, and its color in the pipeline map.
REVIEW_STATUS_LABELS = {
    "reviewed": "signed off",
    "changed-since-review": "changed",
    "unreviewed": "not signed off",
    "revoked": "revoked",
    "invalid-record": "invalid record",
    "unknown": "unknown",
}
REVIEW_STATUS_COLORS = {
    "signed off": "#1f6f43",
    "changed": "#8f5b00",
    "commit first": "#a3262a",
    "not signed off": "#555555",
    "revoked": "#7c3aed",
    "invalid record": "#a3262a",
    "unknown": "#a3262a",
}
# Inline, because marimo's reset flattens raw headings and a style sheet cannot reach every output.
REVIEW_HEADING_STYLES = {
    2: "margin:0 0 .15rem;font-size:1.625rem;line-height:1.2;font-weight:700",
    3: (
        "margin:.75rem 0 .2rem;padding-bottom:.15rem;font-size:1.125rem;line-height:1.3;"
        "font-weight:650;border-bottom:1px solid var(--border,#d4d4d8)"
    ),
    4: "margin:.5rem 0 .15rem;font-size:.9375rem;line-height:1.3;font-weight:600",
    5: "margin:.4rem 0 .1rem;font-size:.875rem;line-height:1.3;font-weight:600",
}
# The one action bar stays in view while a long step scrolls.
REVIEW_BAR_STYLE = {
    "position": "sticky",
    "top": "0",
    "z-index": "20",
    "background": "var(--background, #fff)",
    "padding": "0.5rem 0",
    "border-bottom": "1px solid var(--border, #d4d4d8)",
}
# Marimo scrolls an inner element, so walk up through shadow roots. Only a new route resets it,
# so signing off or refreshing keeps the reader's place.
REVIEW_SCROLL_ESM = """
export default { render({ model, el }) {
  el.style.display = "none";
  const route = model.get("route");
  if (globalThis.__astackReviewRoute === route) return;
  globalThis.__astackReviewRoute = route;
  const reset = () => {
    for (let node = el; node; node = node.parentNode || node.host) {
      if (node.scrollTop) node.scrollTop = 0;
    }
  };
  requestAnimationFrame(() => requestAnimationFrame(reset));
} };
"""
# The step's action bar. Every click carries the token this render was given, so the kernel can
# refuse a click from a page that is gone. A double-click's second click has detail 2 and is
# dropped even when the next page has already rendered under the pointer. Keyboard is detail 0.
# marimo can deliver this widget's event before a native button's update, so a native button
# click closes this render in the browser, in the order the person clicked.
REVIEW_ACTIONS_ESM = """
const CSS = `
.bar { display: flex; flex-direction: column; gap: 0.5rem; color: var(--foreground, #18181b); }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
.status { font-weight: 600; }
button { font: inherit; font-size: 0.875rem; padding: 0.25rem 0.75rem; cursor: pointer;
  border-radius: 0.375rem; border: 1px solid var(--border, #d4d4d8);
  background: var(--background, #fff); color: var(--foreground, #18181b); }
button.primary { background: #1f6f43; border-color: #1f6f43; color: #fff; }
button.danger { background: #a3262a; border-color: #a3262a; color: #fff; }
button:disabled { opacity: 0.55; cursor: not-allowed; }
button:focus-visible, input:focus-visible { outline: 2px solid var(--ring, #2563eb);
  outline-offset: 1px; }
input { font: inherit; font-size: 0.875rem; width: 100%; box-sizing: border-box;
  padding: 0.25rem 0.5rem; border-radius: 0.375rem; border: 1px solid var(--border, #d4d4d8);
  background: var(--background, #fff); color: var(--foreground, #18181b); }
.box { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.75rem;
  border-radius: 0.375rem; border: 1px solid var(--border, #d4d4d8); }
.box.warn { border-color: var(--amber-9, #ffb224); }
.box.danger { border-color: var(--red-9, #e5484d); }
`;
export default { render({ model, el }) {
  const token = model.get("token");
  const view = model.get("view");
  const allowed = new Set(view.allowed);
  let closed = false;
  const closeOnNative = (event) => {
    if (event.composedPath().some((node) => node.tagName === "MARIMO-BUTTON")) closed = true;
  };
  globalThis.addEventListener("click", closeOnNative, true);
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const reason = make("input");
  reason.type = "text";
  reason.placeholder = "Why? (optional)";
  reason.setAttribute("aria-label", "Reason for revoking");
  const send = (action) => {
    if (closed || !allowed.has(action)) return;
    closed = true;
    const typed = action === "confirm" ? reason.value : "";
    model.set("event", { token, action, reason: typed });
    model.save_changes();
  };
  const button = (action, label, tone) => {
    const node = make("button", tone, label);
    node.type = "button";
    node.dataset.action = action;
    node.disabled = !allowed.has(action);
    node.addEventListener("click", (event) => { if (event.detail <= 1) send(action); });
    return node;
  };
  const bar = make("div", "bar");
  const row = make("div", "row");
  row.append(
    make("span", "status", view.status),
    button("sign", view.sign.label, view.sign.primary ? "primary" : ""),
    button("next", view.next.label, view.next.primary ? "primary" : ""),
  );
  if (view.can_revoke) row.append(button("revoke", "Revoke…", ""));
  bar.append(row);
  if (view.confirming) {
    const box = make("div", "box warn");
    const buttons = make("div", "row");
    buttons.append(button("confirm", "Confirm revoke", "danger"), button("cancel", "Cancel", ""));
    box.append(make("div", "", "Revoke this sign-off? Nothing changes until you confirm."));
    box.append(reason, buttons);
    bar.append(box);
  }
  if (view.refused) {
    const box = make("div", "box danger");
    const line = make("div", "row");
    line.append(make("span", "", view.refused), button("refresh", "↻ Refresh", ""));
    box.append(line);
    bar.append(box);
  }
  const style = make("style");
  style.textContent = CSS;
  el.replaceChildren(style, bar);
  return () => globalThis.removeEventListener("click", closeOnNative, true);
} };
"""
# Recognize unchanged current and earlier templates, since upgrades preserve owner notebooks.
VIEWER_NOTEBOOK_SHA256 = {
    "notebooks/review.py": (
        "d9d771b55c6ea08360b2027e28d518af17e7db225386fe3539f2fa3366d3078e",
        "503d154e034ea1ebefcfe31611459dce157773361bfbb40ffe5526edf7ad15ab",
        "a896fa013405169c7f5482959f96fe839492b008c56fa793dd02d69a1e04abbf",
    )
}
# Files a pipeline writes into that are labeled even when no stage or run declares them.
LABELED_OUTPUT_ROOTS = ("reports/figures/", "reports/tables/", "artifacts/")
DVC_HASH_PATTERN = re.compile(r"^[0-9a-f]{32,64}$")
VALIDATION_TIERS = ("verify", "release")
# Never exported, even when an allowlist pattern matches.
EXPORT_PRIVATE_PREFIXES = (
    ".astack/local.toml",
    ".astack/worktree.json",
    ".cache/",
    "data/raw/",
    "docs/work-items/",
    "reports/audits/",
    "scratch/",
)
EXPORT_SOURCE_METADATA = {"data/readme.md", "data/sources.yml"}  # casefolded
PUBLICATION_FILES = ("license", "citation", "data_availability", "code_availability")
SPATIAL_FIELDS = (
    "crs",
    "vertical_datum",
    "temporal_extent",
    "resolution",
    "nodata",
    "geometry_validity",
)


@dataclass(frozen=True)
class Finding:
    priority: str
    area: str
    check: str
    message: str
    evidence: str
    sensitive: bool = False


@dataclass(frozen=True)
class PathSnapshot:
    digest: str | None
    mode: int
    kind: str
    target: str | None
    target_is_directory: bool
    target_mode: int | None
    backup: Path | None


SCIENCE_CAPABILITIES = ("scientific-review", "scientific-validation")


def _scaffold_layout_applies(root: Path) -> bool:
    """The Python layout from `astack new` is required unless the project says it is General.

    `astack install` always writes `profile = "general"`, so a missing or unreadable profile is
    treated as a data science project and keeps every check.
    """
    try:
        return bool(project_config(root).get("profile") != "general")
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return True


def _science_applies(root: Path) -> bool:
    """Science checks apply to data science projects and to General projects that opt in."""
    if _scaffold_layout_applies(root):
        return True
    config = project_config(root)
    capabilities = config.get("capabilities", [])
    review = config.get("review", {})
    return bool(
        (isinstance(capabilities, list) and set(SCIENCE_CAPABILITIES) & set(capabilities))
        or (
            isinstance(review, dict)
            and (review.get("scientific") or review.get("scientific_paths"))
        )
        or config.get("scientific_outputs") is not None
        or config.get("output_contract") is not None
        or "analysis" in config
        or (root / "data/sources.yml").exists()
    )


def _run(
    args: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        env=env,
        timeout=timeout,
    )


def _require_regular_file(path: Path) -> None:
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(f"{path} is not a regular file")


def _open_regular_file(path: Path) -> BinaryIO:
    """Reject special files, including a pipe swapped in between stat and open."""
    _require_regular_file(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _sha256(path: Path, *, regular_only: bool = False) -> str:
    digest = hashlib.sha256()
    with _open_regular_file(path) if regular_only else path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _viewer_notebook(root: Path, path: Path) -> bool:
    """An astack notebook nobody has changed: the review notebook."""
    expected = VIEWER_NOTEBOOK_SHA256.get(path.relative_to(root).as_posix(), ())
    try:
        return bool(expected) and _sha256(path) in expected
    except OSError:
        return False


# Digest cache: reuse a file's SHA-256 only for a read-only file (an immutable snapshot) whose
# device, inode, size, mtime and ctime all match. Programs cannot set ctime, so any write or
# permission change forces a rehash. Writable files are always rehashed, because writes through a
# shared memory mapping need not update timestamps. Files touched within the last few seconds are
# never cached, because coarse timestamps could hide a later write.
_DIGEST_MEMO: dict[str, Any] = {}
DIGEST_CACHE_ENV = "ASTACK_DIGEST_CACHE"
DIGEST_CACHE_RECENT_SECONDS = 3


# Strict audits (ship-ready and release) always rehash. A process that opened a writable memory
# mapping before a file was made read-only could still change its bytes without changing its stamp.
_DIGEST_POLICY: dict[str, bool] = {"bypass": False}
# While set, status commands skip astack's own setup writes (markers, DVC cache config), so a
# read-only view such as the review notebook changes nothing in the project.
_READ_ONLY: dict[str, bool] = {"on": False}


def _digest_cache_enabled() -> bool:
    if _DIGEST_POLICY["bypass"]:
        return False
    return os.environ.get(DIGEST_CACHE_ENV, "on").casefold() not in {"off", "0", "false"}


def _file_stamp(path: Path) -> list[int]:
    metadata = path.stat()
    return [
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
    ]


def _stable_stamp(stamp: list[int]) -> bool:
    recent = time.time_ns() - DIGEST_CACHE_RECENT_SECONDS * 1_000_000_000
    read_only = not stamp[5] & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    return read_only and max(stamp[3], stamp[4]) < recent


def _cached_digest(key: str, stamp: object, compute: Any) -> str:
    if not _digest_cache_enabled():
        return str(compute())
    entry = _DIGEST_MEMO.get(key)
    if isinstance(entry, list) and len(entry) == 2 and entry[0] == stamp:
        return str(entry[1])
    digest = str(compute())
    _DIGEST_MEMO[key] = [stamp, digest]
    return digest


def _same_inode(path: Path, expected: os.stat_result) -> bool:
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return False
    return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)


def _unlink_if_same(path: Path, expected: os.stat_result) -> bool:
    """Remove a file only while it is still the inode this process created."""
    if not _same_inode(path, expected):
        return False
    path.unlink()
    return True


def _create_exclusive(path: Path) -> tuple[int, os.stat_result]:
    """Create a new file, never replacing or following an existing path."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return descriptor, os.fstat(descriptor)


def _copy_exclusive(
    source: Path,
    destination: Path,
    on_create: Callable[[os.stat_result], None] | None = None,
) -> None:
    """Copy bytes and metadata into a new file. A failure removes only that new file."""
    with source.open("rb") as reader:
        descriptor, created = _create_exclusive(destination)
        if on_create is not None:
            on_create(created)
        try:
            with os.fdopen(descriptor, "wb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            shutil.copystat(source, destination)
        except BaseException:
            _unlink_if_same(destination, created)
            raise


def _clone_file(source: Path, destination: Path) -> bool:
    """Make a copy-on-write clone of a regular file into a new path.

    False means the caller must copy instead. An existing destination raises FileExistsError and is
    never replaced. APFS uses clonefile(2) and Linux uses the FICLONE ioctl (Btrfs, XFS and
    similar). Any other platform, filesystem, or a destination on another device returns False.
    """
    if sys.platform == "darwin":
        import ctypes

        clonefile = getattr(ctypes.CDLL(None, use_errno=True), "clonefile", None)
        if clonefile is None:
            return False
        clone_nofollow = 0x0001
        result = clonefile(
            os.fsencode(source), os.fsencode(destination), ctypes.c_uint32(clone_nofollow)
        )
        if result == 0:
            return True
        if ctypes.get_errno() == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, "Destination exists", str(destination))
        return False
    if sys.platform.startswith("linux"):
        import fcntl

        ficlone = 0x40049409
        with source.open("rb") as reader:
            descriptor, created = _create_exclusive(destination)
            try:
                fcntl.ioctl(descriptor, ficlone, reader.fileno())
            except OSError:
                os.close(descriptor)
                _unlink_if_same(destination, created)
                return False
            os.close(descriptor)
        return True
    return False


def _clone_or_copy(source: Path, destination: Path) -> str:
    """Copy a file with its metadata into a new path, by clone when possible.

    Returns "clone" or "copy". Never replaces an existing destination.
    """
    if _clone_file(source, destination):
        shutil.copystat(source, destination)
        return "clone"
    _copy_exclusive(source, destination)
    return "copy"


def _cached_sha256(path: Path) -> str:
    """Return a file's SHA-256, reusing a cached digest only for an unchanged file."""
    stamp = _file_stamp(path)
    if not _digest_cache_enabled() or not _stable_stamp(stamp):
        return _sha256(path)
    digest = _cached_digest(f"file:{path.resolve()}", stamp, lambda: _sha256(path))
    if _file_stamp(path) != stamp:  # changed while hashing: never keep that digest
        _DIGEST_MEMO.pop(f"file:{path.resolve()}", None)
        return _sha256(path)
    return digest


def _digest_cache_path(root: Path) -> Path | None:
    try:
        return Path(state_path(Path(canonical_checkout(root)), "digest-cache.json"))
    except (OSError, RuntimeError, ValueError):
        return None


def _load_digest_cache(root: Path) -> None:
    """Load the machine-local cache kept in the Git common directory, outside the worktree."""
    path = _digest_cache_path(root) if _digest_cache_enabled() else None
    if path is None or not path.is_file():
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(value, dict):
        return
    entries = value.get("entries")
    if value.get("schema_version") == 1 and isinstance(entries, dict):
        for key, entry in entries.items():
            _DIGEST_MEMO.setdefault(key, entry)


def _save_digest_cache(root: Path, protected: set[tuple[int, int]] | None = None) -> None:
    """Persist digests. Never replaces a path holding the last name of a protected raw inode."""
    path = _digest_cache_path(root) if _digest_cache_enabled() else None
    if path is None:
        return
    entries = {}
    for key, entry in _DIGEST_MEMO.items():
        if not (isinstance(entry, list) and len(entry) == 2):
            continue
        target = Path(key.split(":", 1)[1])
        try:
            current = _file_stamp(target) if key.startswith("file:") else None
        except OSError:
            continue
        if current is None or current == entry[0]:
            entries[key] = entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if protected is not None:
            destination: os.stat_result | None
            try:
                destination = os.lstat(path)
            except FileNotFoundError:
                destination = None
            if (
                destination is not None
                and (destination.st_dev, destination.st_ino) in protected
                and (stat.S_ISDIR(destination.st_mode) or destination.st_nlink <= 1)
            ):
                return
        temporary = path.with_name(f".{path.name}.{os.getpid()}")
        descriptor, created = _create_exclusive(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"schema_version": 1, "entries": entries}))
            temporary.replace(path)
        except OSError:
            _unlink_if_same(temporary, created)
            raise
    except OSError:
        return


def _create_new_file(
    root: Path,
    parts: tuple[str, ...],
    text: str,
    *,
    allow_path_fallback: bool = True,
    alone: bool = False,
) -> None:
    """Create `root/<parts>` only if it does not exist, never through a link.

    Each folder is opened without following symlinks, so a link swapped in after the caller's
    checks cannot redirect the write, and an existing file, link included, is never replaced.
    With `alone`, the new file is removed again, through the same folder handle and only if it
    is still the file just written, when anything else is in its folder after the write.
    Where the platform cannot open folders that way, the resolved folder must stay inside `root`,
    which still leaves a race, so callers writing anything sensitive pass
    `allow_path_fallback=False` to refuse there. Raises OSError when it cannot create the file.
    """
    *folders, name = parts
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    anchored = (
        os.open in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and os.listdir in os.supports_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )
    if anchored:
        descriptor = os.open(root, directory_flags)
        try:
            for folder in folders:
                try:
                    os.mkdir(folder, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(folder, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            handle = os.open(name, flags, 0o644, dir_fd=descriptor)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                written = os.fstat(stream.fileno())
            if alone and any(entry != name for entry in os.listdir(descriptor)):
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (written.st_dev, written.st_ino):
                    os.unlink(name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
        return
    if not allow_path_fallback or alone:
        raise OSError("this platform cannot create the file without following links")
    parent = root.joinpath(*folders)
    for index in range(1, len(folders) + 1):
        current_folder = root.joinpath(*folders[:index])
        if current_folder.is_symlink() or current_folder.is_junction():
            raise OSError(f"{current_folder} is a link")
        current_folder.mkdir(exist_ok=True)
    if not parent.resolve().is_relative_to(root.resolve()):
        raise OSError(f"{parent} is outside {root}")
    with os.fdopen(os.open(parent / name, flags, 0o644), "w", encoding="utf-8") as stream:
        stream.write(text)


def _ensure_cache_ignored(root: Path) -> None:
    """Keep the runtime's own records out of Git where the project's `.gitignore` does not.

    Only the folders the runtime writes are ignored, plus this file itself, so the owner's own
    files under `.cache/` stay visible to Git and to the audit. Commands call this before they
    write a record, so the first record never dirties the worktree.
    """
    marker = root / ".cache" / ".gitignore"
    if marker.exists() or marker.is_symlink():
        return
    try:
        _create_new_file(
            root,
            (".cache", ".gitignore"),
            "# Created by astack: its run records never belong in Git.\n"
            "/.gitignore\n/validation/\n/runs/\n",
        )
    except OSError:
        return


def _ensure_audit_reports_ignored(root: Path) -> None:
    """Keep local audit reports out of Git without editing the owner's `.gitignore`.

    Audit reports may hold sensitive details. When nothing already ignores `reports/audits/`
    and the folder is missing or empty, write a `.gitignore` inside it that ignores the whole
    folder, itself included. Leave any folder that already holds files, tracked or not, or that
    is a link, so the audit still reports the problem instead of hiding the owner's files.
    """
    reports = root / "reports"
    audits = reports / "audits"
    if any(path.is_symlink() or path.is_junction() for path in (reports, audits)):
        return
    try:
        if audits.exists() and (not audits.is_dir() or any(audits.iterdir())):
            return
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", "reports/audits/sensitive-audit.md"],
            cwd=root,
            check=False,
            capture_output=True,
        )
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "--", "reports/audits"],
            cwd=root,
            check=False,
            capture_output=True,
        )
    except OSError:
        return
    if ignored.returncode != 1 or tracked.returncode != 0 or tracked.stdout:
        return
    try:
        _create_new_file(
            root,
            ("reports", "audits", ".gitignore"),
            "# Created by astack: local audit reports may hold sensitive details.\n*\n",
            allow_path_fallback=False,
            # A file saved while the checks ran would be hidden, so give the folder back.
            alone=True,
        )
    except OSError:
        return


def _stream_contains_secret(path: Path) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    overlap = ""
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            text = overlap + decoder.decode(block)
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                return True
            overlap = text[-4096:]
        text = overlap + decoder.decode(b"", final=True)
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _finding(
    priority: str,
    area: str,
    check: str,
    message: str,
    evidence: str,
    *,
    sensitive: bool = False,
) -> Finding:
    return Finding(priority, area, check, message, evidence, sensitive)


def _safe_managed_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    windows_path = PureWindowsPath(relative)
    if (
        not relative
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe managed path: {relative!r}")
    candidate = root / path
    current = candidate
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ValueError(f"managed path uses a symlink: {relative}")
        current = current.parent
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"managed path escapes project: {relative}")
    return candidate


COMPONENT_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
COMPONENT_LIFECYCLES = {
    "data/interim": "interim",
    "data/processed": "processed",
    "artifacts": "artifact",
    "evidence": "evidence",
    "reports/figures": "figure",
    "reports/tables": "table",
}
# Output locations a pipeline writes. A stage may read one only as another stage's output.
DERIVED_LIFECYCLES = frozenset(
    {
        "interim",
        "processed",
        "artifact",
        "evidence",
        "figure",
        "table",
        "scratch",
        "legacy-evidence",
    }
)
# Code that changes results but is not reviewed line by line, so it never resets a review.
UNREVIEWED_CODE = frozenset({"uv.lock"})
# Data, not code: these never count toward a component's reviewed code.
NOT_CODE_LIFECYCLES = frozenset({"raw", "manual", *DERIVED_LIFECYCLES})
MACHINE_OUTPUT_SUFFIXES = {
    ".json",
    ".csv",
    ".parquet",
    ".npy",
    ".npz",
    ".tif",
    ".tiff",
    ".pkl",
    ".pickle",
    ".joblib",
    ".onnx",
    ".pt",
    ".pth",
}
