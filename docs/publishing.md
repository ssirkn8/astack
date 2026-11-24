# Publishing

Public release uses a clean allowlisted export or a new repository. A branch is not a privacy boundary because deleted files remain in prior Git history.

Never flip a private research repository to public, and never try to sanitize a private repo by deleting files on a release branch. Build a fresh export from an explicit allowlist instead.

## How `publish-public` and `export-public` work

1. **Declare source release rights in `data/sources.yml`.** Every source must have known `redistribution` terms and explicit `allowed_outputs`. Any data or derived output included in a public export must be permitted by `public-export` in `allowed_outputs` (for the covering source, or for every source when the artifact is derived from multiple inputs).
2. **Declare an explicit allowlist in `.astack/project.toml`.** List only the exact committed files or directory globs that belong in the public copy, plus optional secret/path scan hooks (`{out}` expands to the export directory):

```toml
[public_export]
include = [
  "README.md",
  "LICENSE",
  "CITATION.cff",
  "pyproject.toml",
  "uv.lock",
  "src/**",
  "tests/**",
  "reports/figures/analysis/**",
]
exclude = []
scan = [["gitleaks", "dir", "{out}"]]
```

3. **Run the deterministic exporter into a new empty directory outside the repository:**

```sh
astack run export-public --out /path/to/public-release
```

(Delegates to `.astack/workflow.py export-public --out ...`.)

What the exporter enforces automatically:

- Refuses dirty working trees, untracked files, symlinks, and Git submodules.
- Reads committed files only and strips all Git history.
- Excludes raw data (`data/raw/`), internal work items (`docs/work-items/`), audit reports (`reports/audits/`), scratch files, caches, and local state by default.
- Checks `redistribution` and `allowed_outputs` in `data/sources.yml`.
- Runs the built-in secret scanner plus every command in `[public_export] scan` (such as `gitleaks`).
- Leaves nothing behind on failure and prints `export_sha256` and the exact exported file list on success.

## Extra private history guards

Projects can declare additional sensitive roots in `.astack/project.toml`:

```toml
[privacy]
private_history_paths = ["client-private", "notes/internal.txt"]
private_history_safe_paths = ["client-private/README.md"]
```

`private_history_paths` checks project-relative roots across reachable Git history. `private_history_safe_paths` allows only exact documented file exceptions (never a directory prefix). These settings supplement and never disable credential scanning or raw immutability checks.

## Human review before pushing

Automated scans catch common secrets and policy violations, not everything. Before publishing or pushing a public repository:

- Inspect the exported directory for notebook outputs, private filesystem paths, unapproved large artifacts, or free-text license restrictions. If something should not be there, narrow `[public_export] include` or add to `exclude` and re-export rather than deleting files from the export directory by hand.
- Check code, data, text, and figure licensing across the export.
- Confirm reproducibility instructions clearly distinguish public inputs from unavailable or restricted inputs.
- Verify report claims and public artifacts trace to approved evidence, and run setup and tests inside the exported copy.
- Creating or pushing a public repository always requires explicit human approval (`publish-public` never creates or pushes a remote without it).
