# Public Release Policy

Public release uses a clean allowlisted export or a new repository. A branch is not a privacy
boundary because deleted files remain in prior Git history.

Required checks:

- source redistribution and derived figure/output rights from `redistribution` and
  `allowed_outputs` in `data/sources.yml`
- secrets, sensitive records, private paths, symlinks, submodules, and large files
- code, data, text, and figure licensing
- clean-history destination
- reproducibility instructions that distinguish public and unavailable inputs
- report claims and public artifacts traceable to approved evidence

Exclude raw data, internal work items, audits, private reports, and notebooks by default. Build the
export from an allowlist rather than copying the private project and deleting known hazards.

Projects can declare additional private history roots in `.astack/project.toml` with
`[privacy].private_history_paths`. These project-relative roots are checked across reachable Git
history, and `[privacy].private_history_safe_paths` permits only exact documented exceptions. The
policy supplements, and never disables, credential scanning or raw-source immutability checks.
