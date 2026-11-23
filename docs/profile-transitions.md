# Change an installed workflow policy

Use the pinned installer that owns the existing installation. Profile changes use its same preview,
plan digest, ownership checks, locks, transaction journal, verified writes, rollback and recovery.
They change policy configuration and manifest metadata, without reinstalling or forking the shared
runtime or rearranging application files. This operation does not upgrade the workflow version.
Upgrade managed bytes separately when the exact installed source differs.

An agent may inspect and explain the policy change. The runtime decides whether the target is
eligible and verifies the applied bytes. It does not supply scientific methods or claim that passing
arbitrary project checks establishes scientific correctness.

```sh
# Preview using an exact published source commit that supports policy transitions.
uv tool run --from 'git+https://github.com/ssirkn8/astack.git@<verified-release-commit>' \
  astack change-policy /path/to/project --release vX.Y.Z --profile data-science \
  --capability scientific-review --capability scientific-validation
```

Inspect current/proposed policy, exact configuration/manifest writes, source identity, required
checks, ownership conflicts, active work/reviews, scientific obligations and safety reductions.
Apply with identical arguments plus `--apply --approve-plan <digest>`. A changed target, source,
registry or receipt snapshot invalidates approval. An unchanged request has no proposed writes.
Use explicit `--candidate` only with disposable unpublished development consumers.

## Profiles and scientific capabilities

Projects keep `.astack/`, the same managed skills, application layout and
project-owned `validation.check` argv in either profile. General is suitable for ordinary software
or hybrid projects. Data Science policy requires both scientific capabilities, while still allowing
the project's own language and check commands. It does not generate a Python or DS application.

`scientific-review` requires the scientific-credibility review axis. `scientific-validation` composes
the owner's `validation.verify` argv with CHECK. Configure meaningful, owner-approved scientific
verification commands before enabling it. A missing or failed check is not passing evidence. The
capability does not invent a methodology, alter output contracts or confirm scientific conclusions.
For example, a hybrid General project can retain scientific review without changing its profile.

To preserve science while returning to General, request `--profile general` and keep both
capabilities. To remove a capability, omit it from the complete requested capability set. A reduction
is explicit in the preview and is blocked while scientific declarations, provenance or review
obligations remain. Historical results and failed/unavailable evidence stay in place, unchanged.
DVC, Quarto and Marimo tooling changes are not managed by this policy transition operation.

## Preservation and recovery limits

All active linked worktrees must finish before changing canonical policy. Their history and
evidence are preserved rather than rewritten. The transaction takes a policy-transition lock and
rechecks active worktrees and scientific obligations under the installer lock. It refuses
unsafe reductions and owner-modified managed files. It never resets, stashes, deletes data, relabels
past validation, or authorizes a PR merge.

If interrupted, use the existing `install --recover` preview and exact-plan apply. Policy journals
can restore the bounded configuration before-image. Recovery blocks if lifecycle obligations changed
since the transaction, and retains conflicting independent owner changes. App source and evidence
are outside the transaction. Source/profile upgrades remain independently explicit.

Multiline root capability arrays require owner normalization before this bounded edit.
Conflicting config files and unknown layouts fail closed.

Disposable General, policy-based DS and hybrid fixtures exercise these supported paths. These tests do not certify
a published release, live consumers, or native Windows/Linux/harness interactions.
