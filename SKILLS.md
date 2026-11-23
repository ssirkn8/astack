# Skill Authoring Standard

This file applies only to maintainers of this repository. Generated projects do not receive it.

It covers the skills astack authors under `.opencode/skills/`. It does not cover the pinned
upstream skills under `.agents/skills/` (tstack and Matt Pocock's skills). Those stay byte-for-byte
as published, including their one-line aliases, and change only by re-locking
`integrations/t3/upstream.lock.json` after reviewing the upstream diff.

## Format

Skills live at `.opencode/skills/<name>/SKILL.md` and use valid OpenCode frontmatter:

```yaml
---
name: lowercase-hyphenated-name
description: State what the skill does and when it should be used.
metadata:
  permission-level: read-only
---
```

Permission metadata documents the expected side-effect level. Skill procedures must still require
explicit user confirmation for Git, remote, and public-release actions where applicable.

## Content

A skill should contain only action-specific behavior. Do not repeat universal rules from `AGENTS.md` or generic command boilerplate.

Every skill must make clear:

- when it applies
- its goal
- allowed and forbidden side effects
- stop conditions
- validation
- output

Commands under `.opencode/commands/` are short invocation wrappers. Use only OpenCode-supported command frontmatter fields.
