# Maintainer releases

This guide is only for maintaining astack itself. **Projects created or installed with astack do not inherit any of it.** The scaffold adds no Release Please config, PR title check, or commit format, so downstream projects keep whatever release workflow they already use.

## Everyday PR titles

PR titles become the squash commit subjects on `main`, and Release Please reads those subjects to decide the next version. Only the PR title must follow Conventional Commits. Intermediate commits on your branch can say anything.

```text
fix: keep user files during upgrade
feat: add a data check
feat!: remove the old command
docs: explain upgrades
```

CI checks the PR title on every edit and verifies the final commit subject again after it lands on `main`. This applies equally to manual merges, `full-stop`, and `full-send`. If you rewrite a title in the GitHub merge dialog, the post-merge check on `main` flags non-conforming subjects.

## Release classification

| Title prefix | From `0.1.0` | Included in changelog |
| --- | --- | --- |
| `fix:`, `perf:`, `revert:` | `0.1.1` | Yes |
| `feat:` | `0.2.0` | Yes |
| `feat!:`, `fix!:`, or `BREAKING CHANGE:` footer | `0.2.0` | Yes |
| `docs:`, `style:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:` | No release | No |

While astack is `0.x`, breaking changes bump the minor version. Moving to `1.0.0` is always a deliberate override. Multiple merged PRs combine into a single proposal at the highest applicable bump.

## Intentional version overrides

Add a `Release-As: 1.0.0` footer to the squash commit body and verify the proposed version before accepting, because the footer overrides normal classification.

To correct a commit subject that already landed, never rewrite `main`. Either merge a follow-up PR with the intended type, or edit the merged PR description with a `BEGIN_COMMIT_OVERRIDE` block and rerun the proposal workflow.

## Working with a Release Please proposal PR

Release Please opens and maintains one draft PR. It cannot merge the PR, push tags, or publish a GitHub Release. Branch protection, review, and owner approval still apply.

A proposal updates the Release Please manifest, `pyproject.toml`, and `CHANGELOG.md`. Before taking over the proposal branch to run `uv lock` and update `.astack/version`:

1. Pause proposals by setting the repository variable `RELEASE_PROPOSALS_ENABLED` to anything other than `true`.
2. Wait for all active or queued `Release proposal` workflow runs to finish (or cancel them) so no in-flight run force-pushes over your commits.
3. Preflight also holds automatically once a proposal PR is marked ready for review or edited outside those three bot-managed files.

Check version agreement across repository files offline before merging:

```sh
uv run --locked python scripts/check_release_proposal.py --local
```

It reports every file whose version disagrees.

## Resuming proposals after publishing

Keep proposals paused through the authorized merge and GitHub Release publication. In proposal-only mode, Release Please leaves `autorelease: pending` on the merged proposal PR and suppresses all future proposals while that label remains.

After the new stable GitHub Release is published:

1. Verify that the new release tag resolves to the proposal PR's merge commit on `main` and that version metadata agrees with it.
2. Remove `autorelease: pending` from that specific merged PR in the GitHub sidebar, or run:
   ```sh
   gh pr edit "$release_pr" --repo ssirkn8/astack --remove-label 'autorelease: pending'
   ```
   where `release_pr` is the verified PR number.
3. Re-enable `RELEASE_PROPOSALS_ENABLED=true` and dispatch the workflow on `main`. With no new releasable commits, expect no new draft PR.

See [Release Please's pending-label recovery steps](https://github.com/googleapis/release-please#step-2-ensure-no-autorelease-pending-or-autorelease-triggered-label-in-an-old-pr) for background.

## Hold, retry, reject, and rollback

- **Hold:** set the repository variable `RELEASE_PROPOSALS_ENABLED` to anything except `true`.
- **Automatic holds:** preflight stops when no stable release is published, when the manifest differs from the latest release, when the release tag is not on `main`, or when proposals conflict.
- **Retry:** rerun the `Release proposal` workflow to update the existing draft PR.
- **Reject:** close the draft PR. Later releasable commits can open a fresh proposal.
- **Rollback:** land a reviewed `revert:` PR and release forward. Never move or delete a published tag, and never publish a lower version number.

## Clean-history baseline

The public repository starts at `v0.1.0` with no prior history. Its Release Please configuration intentionally omits `bootstrap-sha` and `last-release-sha`, using the published `v0.1.0` release as the sole boundary. Until that release exists, preflight holds on purpose so Release Please does not treat the entire history as unreleased.

## Pre-cutover NO-GO checklist

Local tests cannot verify GitHub repository configuration. Every item below stays **NO-GO** until verified on `ssirkn8/astack` with explicit owner authorization:

- [ ] Squash merging is enabled, uses `PR_TITLE` as the default squash subject, and excludes intermediate commit messages from the squash body (`PR_BODY` or `BLANK`). Always verify intentional `Release-As:` or `BREAKING CHANGE:` footers in the final squash body before merging (with `BLANK`, enter them manually in the merge dialog).
- [ ] Branch protection on `main` requires `check` and blocks direct pushes.
- [ ] The `v0.1.0` tag and GitHub Release exist on `main`, published by the owner.
- [ ] A repository-scoped GitHub App is installed with its ID and key configured.
- [ ] After verifying the items above, enable `RELEASE_PROPOSALS_ENABLED` for the authorized first live run and confirm one draft PR opens without merging, tagging, or creating a GitHub Release.
- [ ] Verify `autorelease: pending` label cleanup and proposal resumption against the first authorized publication made from a proposal.
