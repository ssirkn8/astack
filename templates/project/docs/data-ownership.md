# Data ownership without permanent stage worktrees

A linked worktree is a temporary execution workspace, not a durable home for inputs. Plan data ownership before running a stage. Finished stages should normally be removable, and you should keep one only for a named preservation, human-edit, active-job, or verification exception.

## Default layout

| Material | Durable owner | Worktree behavior |
| --- | --- | --- |
| Original snapshots | Configured data root's immutable `raw/`, or a declared external source kept in place | Only immutable raw data is shared across worktrees. Never edit it in place. |
| Mutable interim/processed products | Producing worktree while work is active | Keep these directories worktree-local. Before downstream use outlives the stage, freeze required products into a durable location and verify identity. |
| Expensive frozen derived inputs | Existing durable external or project storage, identified in `data/sources.yml` with derivation and expected checksum | Downstream code resolves a configurable root and verifies exact bytes. This is an immutable input reference, not shared mutable `processed/` storage. |
| Manual evidence or annotations | Versioned evidence when rights permit, otherwise a declared durable private location with verified identity | Preserve unique human edits before cleanup. Ignored status never means disposable. |
| Durable QA and versioned outputs | Component-scoped `evidence/`, reports, and artifacts according to project policy | Keep provenance and output bindings independent of temporary stage paths. Never upload restricted content just to satisfy CI. |
| Scratch and environments | Temporary local state under normal cleanup rules | Environment reproducibility does not prove a unique file is disposable. Do not broaden cleanup rules to force success. |

Reuse your existing data-root settings (`ASTACK_DATA_ROOT` or `.astack/local.toml`) for source storage and keep machine paths out of Git. Additional product-root bindings belong in project code or config, not a new global storage layer. Do not require DVC, copy large originals locally, or move original files off external drives by default.

## Before downstream use or stage completion

1. Identify exact required inputs and products and their durable owners using project manifests and validation, not filenames alone.
2. Record expected identity and derivation in `data/sources.yml` and `evidence/`. Keep frozen source checksums and historical provenance unchanged.
3. Make downstream code accept configurable roots and validate each referenced snapshot before reading it. Never hardcode a stage worktree's absolute path.
4. Run the declared reproduction with old-worktree reads denied, then with the old workspace absent when safe in a disposable proof. Compare scientific values and required output bytes.
5. Only after preservation and reproduction checks pass, run normal guarded cleanup. Unknown data, sole copies, unfinished work, or active processes still block deletion. Never erase a blocker just to make cleanup pass.

Project preservation scripts must never overwrite a different destination input, follow an unsafe symlink, or silently update a checksum after mutation. When copying or moving files, preview source and destination identity and conflicts first, verify a restorable copy, and handle interruption safely.

## State the reproduction level precisely

- **From-raw rebuild:** all required raw identities, stages, and scientific checks are available and verified for the current code and config.
- **Render retained products:** a declared frozen derived product is independently sufficient for the selected outputs, and its bytes and derivation are verified. This can work while an original external drive is unavailable, but it does not prove a from-raw rebuild.

A changed frozen raw input is always a failure. Never refresh its recorded checksum to bless a mutation. A missing raw drive may allow a declared retained-product render when verified products exist, but never treat missing or sole-copy inputs as disposable.

## Escape hatch for necessary retention

Leave a workspace intact when preservation is unknown, destinations are unavailable, jobs are active, or reproduction is unproved. Record the exact blocking paths, why each is required, and one concrete next step in the cleanup note or PR handoff. Reconcile that exception later instead of building a second diary, archive dump, or cleanup coordinator.
