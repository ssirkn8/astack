# Journal and archive handoff (optional)

These files are inert templates until you opt in by adding `[publication]` to `.astack/project.toml`. Once enabled, the audit blocks release readiness until every file exists, is complete, and has no `TODO(publication)` markers left.

```toml
[publication]
license = "LICENSE"
citation = "CITATION.cff"
data_availability = "docs/publication/data-availability.md"
code_availability = "docs/publication/code-availability.md"
# Add only after your archive has issued it for this exact release. Never invent a DOI.
# doi = "10.5281/zenodo.0000000"
```

1. Pick a code license and add it as `LICENSE`. astack does not pick one for you. Data licenses come from each source in `data/sources.yml` and may differ from the code license.
2. Copy `CITATION.cff` to the repository root and fill it in.
3. Complete `data-availability.md` and `code-availability.md`. List every source in `data/sources.yml`, its license, and whether it can be shared. Give restricted data an access route, not a copy.
4. Build the clean public release with the `publish-public` skill, tag it, and deposit that exact release in an archive such as Zenodo, Figshare, Dryad, or an institutional repository.
5. After the archive issues an identifier, add it as `[publication].doi`, update `CITATION.cff`, and cite it in the availability statements. The audit checks DOI syntax only, so verify it resolves to this release before submission.
