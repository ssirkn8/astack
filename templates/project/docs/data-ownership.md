# Data ownership without permanent stage worktrees

A linked worktree is a temporary code/execution workspace, not the durable address of an input.
Plan ownership before running a stage. Finished stages should normally be removable; retain one
only for a named unresolved preservation, human-edit, active-job, or verification exception.

## Concrete default layout

| Material | Durable owner | Worktree behavior |
| --- | --- | --- |
| Original snapshots | Existing configured data root's immutable `raw/`, or a declared external source kept in place | Only immutable raw data is shared by the standard worktree layout. Never edit it in place. |
| Mutable interim/processed products | Producing worktree while work is active | Keep these directories worktree-local. Before downstream use outlives the stage, freeze required products into a project-owned durable location and verify identity. |
| Expensive frozen derived inputs | Existing durable external/project storage, identified in `data/sources.yml` with derivation and expected checksum | Downstream code resolves a declared configurable root and verifies the exact bytes. This is an immutable input reference, not shared mutable `processed/` storage. |
| Manual evidence or annotations | Versioned evidence when rights permit, otherwise a declared durable private location with verified identity | Preserve unique human edits before cleanup. Ignored status never means disposable. |
| Durable QA and versioned outputs | Component-scoped `evidence/`, reports and artifacts according to project policy | Keep machine-readable provenance and stable output bindings independent of temporary stage paths. Do not upload restricted content to satisfy CI. |
| Scratch and environments | Temporary local state under the existing cleanup policy | Environment reproducibility does not prove a unique file is disposable. Do not broaden cleanup rules to force success. |

Reuse the existing data-root configuration (`ASTACK_DATA_ROOT` or the project-local data root setting)
for source storage. Keep machine paths in local configuration. Additional product-root bindings
belong to project-owned code/configuration, not a new global workflow storage framework. Do not
require DVC, copy large originals locally, or move original files off external drives by default.

## Before downstream use or stage completion

1. Identify the exact required inputs/products and their durable owners. Classify obligations
   through the project's manifests and scientific validation, not filenames alone.
2. Record expected identity and derivation using the existing `data/sources.yml` and evidence
   layout. Keep frozen source checksums and historical provenance unchanged.
3. Make downstream code accept configurable roots and validate each referenced snapshot before
   reading it. Do not make publication reproduction depend on a stage's absolute path.
4. Run the declared reproduction with old-worktree reads denied, then with the old workspace
   absent when safe in a disposable proof. Compare the scientific values and required output bytes.
5. Only after required preservation and reproduction checks pass, use the normal guarded cleanup
   path under its existing authority. Unknown data, sole copies, unfinished work or active users
   still block deletion. Do not manually erase a blocker just to make cleanup pass.

Project-owned preservation must never overwrite a different destination input, follow an unsafe
symlink or silently update a checksum after mutation. If a move/copy is genuinely needed, preview
exact source/destination identity and conflicts, verify a restorable copy, and use interruption-safe
recovery. This guide supplies no automatic migration or new deletion authority.

## State the reproduction level precisely

- **From-raw rebuild:** all required raw identities, stages and scientific checks are available
  and verified for the intended code/configuration.
- **Render retained products:** a declared frozen derived product is independently sufficient
  for the selected outputs, and its bytes and derivation are verified. This can work while an
  original external drive is unavailable, but does not prove a new from-raw rebuild.

A changed frozen raw input remains a failure. Do not refresh its recorded checksum to bless it.
A missing raw drive may permit a *declared retained-product render* when sufficient verified
products exist, but never infer that missing or unique inputs are disposable.

## Small escape hatch for necessary retention

Leave the workspace intact when preservation is unknown, destinations are unavailable, jobs are
active, or reproduction has not been proved. Use the existing pending cleanup record/PR handoff
with the exact blocking paths, why each is required, and one concrete next step. Reconcile that
same exception later; do not create a second progress diary, permanent agent, archive dump, or
cleanup coordinator. Automatic preservation/retry support must be independently implemented and
validated before relying on it. This guide does not claim such a new runtime exists.
