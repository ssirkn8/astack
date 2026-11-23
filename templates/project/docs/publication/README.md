# Journal and archive handoff (optional)

These files are inert templates. Nothing uses them until you opt in by adding `[publication]` to
`.astack/project.toml`. After that, the audit blocks release readiness until each file exists, is
complete, and contains no `TODO(publication)` marker.

```toml
[publication]
license = "LICENSE"
citation = "CITATION.cff"
data_availability = "docs/publication/data-availability.md"
code_availability = "docs/publication/code-availability.md"
# Add only after your archive has issued it for this exact release. Never invent a DOI.
# doi = "10.5281/zenodo.0000000"
```

1. Choose a code license and add it as `LICENSE`. astack does not choose one for you. Data licenses
   come from each source in `data/sources.yml` and may differ from the code license.
2. Copy `CITATION.cff` to the repository root and complete it.
3. Complete `data-availability.md` and `code-availability.md`. Name every source in
   `data/sources.yml`, its license, and whether it can be shared. Restricted data needs an access
   route, not a copy.
4. Build the public release with the `publish-public` skill, tag it, and deposit that exact release
   with an archive such as Zenodo, Figshare, Dryad, or an institutional repository.
5. Only after the archive issues the identifier, record it as `[publication].doi`, add it to
   `CITATION.cff`, and cite it in the availability statements. The audit checks the DOI's format,
   not that it resolves, so check it resolves to this release before submission.
