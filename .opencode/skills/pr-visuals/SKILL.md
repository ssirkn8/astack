---
name: pr-visuals
description: Attach annotated images to a pull request when a picture proves something text cannot. Use when filing or updating a PR after verifying UI, notebook, figure, or report behavior by looking at it.
---

# PR Visuals

Most PRs need no image, and a PR without one says nothing about it.

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## When to attach

Attach the best frame when both are true:

- You verified the change by looking at it.
- The picture saves the reviewer from rerunning it.

Typical fits are UI and notebook flows, changed figures, report layout, and visual bugs. Skip
refactors, backend logic, docs, and anything a test log already proves. Never run a separate capture
pass just to have an image. Reuse the run you already did for verification.

If a capture mattered but could not be attached, say so in the PR, so missing evidence never reads
as no evidence.

## Capture

Save working copies in `.cache/pr-visuals/<branch>/`. That path is gitignored and worktree-local,
so its contents are disposable. Never commit images to the feature branch.

- Use before/after pairs when behavior changed, captured under the same conditions.
- Prefer synthetic or fixture data.
- Use PNG. Publishing accepts nothing else.

Annotate only when the relevant spot is not obvious. In a browser, prefer outlining the element by
selector before the screenshot. For an existing image, use exact pixel coordinates:

```sh
uv run --no-project --with 'pillow>=10' python .opencode/skills/pr-visuals/pr_visuals.py \
  annotate in.png out.png --box 40,30,240,60 --arrow 300,110,285,75
```

## Publish

Look at every chosen image first. Do not publish one showing a secret, token, private path, personal
detail, or real record from restricted data. If you cannot tell, do not publish it, and ask.

```sh
python3 .opencode/skills/pr-visuals/pr_visuals.py publish .cache/pr-visuals/<branch>/after.png
```

The command prints one commit-pinned URL per image. It strips PNG metadata and appends to the
`pr-evidence` branch without touching your worktree. It refuses unless GitHub confirms the repository
is private. For a repository that is intentionally public, pass `--allow-public` only when a person
has approved publishing these images.

These URLs need a logged-in GitHub session with repository access. Never upload to a third-party
image host. Never delete or force-push `pr-evidence`, because every posted image depends on it. A
leaked image needs a history rewrite, which is a destructive action that requires the owner.

## In the PR

Put images under the Evidence section. Caption what to look at and which commit each image came from,
and state what the images do not prove:

```md
| Before (`abc1234`) | After (`def5678`) |
| --- | --- |
| ![Before: the toolbar overlaps the prompt](URL) | ![After: the prompt sits above the toolbar](URL) |

Not shown: touch input.
```

Before removing a worktree, publish or explicitly abandon any capture the PR relies on.
