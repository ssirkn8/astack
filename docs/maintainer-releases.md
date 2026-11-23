# Maintainer releases

This page is for people maintaining astack itself. **Projects installed with astack do not inherit
any of it.** The scaffold adds no Release Please files, title check, or commit format, and a
downstream project keeps whatever release process it already has.

## Everyday PR titles

The PR title becomes the squash commit subject, and that subject is what Release Please reads.
Only the title has to follow Conventional Commits. Commits on your branch can say anything.

```text
fix: keep user files during upgrade
feat: add a data check
feat!: remove the old command
docs: explain upgrades
```

CI checks the title on every PR edit, and checks the final subject again after it lands on `main`.
This works the same for a manual merge, `full-stop`, and `full-send`. If you retitle a PR in the
merge dialog, the check on `main` reports a subject that does not conform.

## Release classification

| Title | From `0.1.0` | In the changelog |
|---|---|---|
| `fix:`, `perf:`, `revert:` | `0.1.1` | Yes |
| `feat:` | `0.2.0` | Yes |
| `feat!:`, `fix!:`, or a `BREAKING CHANGE:` footer | `0.2.0` | Yes |
| `docs:`, `style:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:` | No release | No |

While astack is `0.x`, a breaking change raises the minor version. Moving to `1.0.0` is always a
deliberate override. Several changes combine into one proposal at the highest bump.

## Intentional version overrides

Add a `Release-As: 1.0.0` footer to the squash commit body. Review the proposed version before
accepting it, because the footer overrides classification.

To correct a subject that already landed, do not rewrite `main`. Land a follow-up PR with the right
type, or edit the merged PR body with a `BEGIN_COMMIT_OVERRIDE` block and rerun the proposal.

## What a proposal can do

Release Please opens or updates one draft PR. It cannot merge it, tag, or create a GitHub Release.
Branch protection, review, and owner approval still apply. A proposal bumps the manifest,
`pyproject.toml`, and `CHANGELOG.md`. Before taking over the proposal branch to run `uv lock` and
update `.astack/version`, pause proposals (`RELEASE_PROPOSALS_ENABLED`) and wait for all active or
queued `Release proposal` workflow runs to finish (or cancel them) so no in-flight run can
force-push over your commits. The preflight also holds automatically once a proposal is marked
ready or has edits outside those three bot-managed files.

Run `uv run --locked python scripts/check_release_proposal.py --local` to compare the version
sources offline. It names each file that disagrees.

## Resume proposals after publishing

Keep proposals paused through the authorized merge and publication. Release Please's proposal-only
mode leaves `autorelease: pending` on the merged PR and suppresses further proposals while that
label is present. Once the new stable GitHub Release is published, verify that its tag resolves to
the proposal PR's merge commit on `main` and that version metadata agrees with it. Then remove
`autorelease: pending` from that specific merged PR in the GitHub sidebar, or run
`gh pr edit "$release_pr" --repo ssirkn8/astack --remove-label 'autorelease: pending'`, where
`release_pr` is the verified PR number. Only then re-enable proposals and dispatch the workflow
on `main`. With no new releasable commits, expect no new PR.

[Release Please documents recovery from a stale pending label](https://github.com/googleapis/release-please#step-2-ensure-no-autorelease-pending-or-autorelease-triggered-label-in-an-old-pr).

## Hold, retry, and rollback

- **Hold:** set the repository variable `RELEASE_PROPOSALS_ENABLED` to anything except `true`.
- **Automatic holds:** proposals stop when no stable release is published, when the manifest differs
  from the latest release, when the release tag is not on `main`, or when proposals conflict.
- **Retry:** rerun the `Release proposal` workflow. It updates the same draft PR.
- **Reject:** close the draft PR. Later changes can produce a new proposal.
- **Rollback:** land a reviewed `revert:` PR and release forward. Never move or delete a published
  tag, and never publish a lower version.

## Clean-history baseline

The public repository starts at `v0.1.0` with no earlier history. Its config has no `bootstrap-sha`
or `last-release-sha`, because the published `v0.1.0` release is the only boundary. Until that
release exists, the preflight holds on purpose. Otherwise Release Please would treat the entire
history as unreleased.

## Pre-cutover NO-GO items

Local tests cannot prove these. Each stays **NO-GO** until checked on the real public repository
with separate owner authorization.

- [ ] The repository allows squash merging, uses the PR title as the default squash subject
      (`PR_TITLE`), and excludes intermediate commit messages from the squash body (`PR_BODY` or
      `BLANK`). Always verify intentional `Release-As:` or `BREAKING CHANGE:` footers in the final
      squash commit body before merging (when using `BLANK`, enter them manually in the merge dialog
      rather than relying on the PR description).
- [ ] Branch protection on `main` requires `check` and blocks direct pushes.
- [ ] The `v0.1.0` tag and GitHub Release exist on `main`, published by the owner.
- [ ] A repository-scoped GitHub App is installed, with its ID and key configured.
- [ ] With the prerequisites above verified, enable `RELEASE_PROPOSALS_ENABLED` for the
      authorized first live run. Confirm one draft PR opens, with no merge, tag, or GitHub Release.
- [ ] Verify the post-publication label cleanup and proposal resumption against the first
      authorized publication made from a proposal.
