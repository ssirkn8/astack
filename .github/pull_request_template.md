## Work item

Closes #

Fallback work item: `docs/work-items/`

## Change

Describe the focused change and why it is needed.

## Evidence

<!-- If you verified this by looking at it and a picture saves the reviewer from rerunning it, attach it with the pr-visuals skill. Otherwise leave images out. -->
<!-- After implementation and review fixes, run the source-comments pass on changed code. Zero new comments is fine. -->

- [ ] `just check` passes
- [ ] `just integration` passes
- [ ] `just scaffold-smoke` passes when scaffold, upgrade, managed-file, or packaged assets change
- [ ] Generated projects preserve project-owned files and receive only allowlisted workflow files
- [ ] Managed-file checksums, package assets, and upgrade behavior are covered when affected
- [ ] OpenCode command and remote-action boundaries remain explicit when affected
- [ ] GitHub operations remain mocked in tests
- [ ] Scientific policy changes distinguish objective checks from interpretive review
- [ ] No secret, private data, build artifact, or unrelated generated file is included

## Release mode

- [ ] Not a release, or semantic version, `.astack/version`, changelog, and migration evidence are complete
- [ ] A temporary package build passes for a release change

## Remaining risk

State untested behavior, unavailable evidence, compatibility risks, and follow-up work.
