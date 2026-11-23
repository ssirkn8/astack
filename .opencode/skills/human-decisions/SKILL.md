---
name: human-decisions
description: Apply the shared policy for when and how workflow agents ask people for decisions
---

# Human Decisions

## Decision policy

Uncertainty triggers investigation. Resolve authority through explicit current user instruction,
issue or proposal acceptance criteria, output contracts or durable project decisions, workflow
policy, repository conventions, established best practice, and safe reversible defaults before
asking.

- `MUST_ASK`: stop for required scientific headline-output confirmation, material unresolved
  scientific-method choices, authoritative-source conflicts that change scope or intent, material
  scope expansion, unresolved privacy or licensing, workflow-upgrade application, publication or
  destructive authority, or another explicit human-authority boundary.
- `MAY_ASK`: ask only when a meaningful preference remains among multiple valid choices. Choose a
  conventional safe option autonomously when the preference is not worth interrupting execution.
- `MUST_NOT_ASK`: do not interrupt for discoverable facts, routine implementation details,
  reversible low-risk choices, required checks, in-scope fixes, or choices already governed by policy
  or repository conventions.

Batch foreseeable questions during planning. After approval, ask only about newly discovered
`MUST_ASK` conditions. There is no arbitrary numeric question limit. Missing, skipped, or unavailable
evidence is never a pass.

## Interaction protocol

1. When a response is warranted and the client supports it, use the harness's native question tool
   (OpenCode `question`, Claude Code `AskUserQuestion`, Codex `request_user_input`) as the first
   interaction. State the decision context and material consequences. Use one to five concise
   choices for bounded decisions and enable multiple selection only when the choices combine.
2. Keep a free-form answer available. For a scientific, methodological, or other material decision
   that needs explanation, do not present a choice set as exhaustive. If the question tool cannot
   capture the needed explanation, ask an open-ended question in text and stop.
3. Keep human decisions separate from tool permissions. Permission to call a question tool only
   permits the interaction. A tool-permission response is never approval for the underlying operation.
4. A declined choice means stop without the authorized operation. A dismissed or canceled question,
   rejected tool call, empty answer, or unavailable question interaction is no response and is not
   approval. For a required decision, ask an open-ended question in text and stop. For a non-required
   preference, use a safe default only when the decision policy allows it, and do not claim the human
   selected that default.
5. Do not re-ask when the current user request already supplies the exact authority required by the
   operation, unless that operation requires a separate confirmation.
6. Preserve each operation's durable evidence. A question response does not replace an output
   contract confirmation, an exact upgrade plan digest, or runtime validation.

Render any approval-bearing plan in chat before asking about it. A free-form requested change is
not approval: reconcile it, render the resulting plan, and ask again. A restart verifies durable
evidence rather than inferring approval from a prior conversational summary.

## Covered decision boundaries

- Workflow upgrade: show the exact dry-run plan, preserved files, and conflicts before required apply
  authorization. Existing exact apply authority need not be requested again.
- Public publication: show the allowlisted export, exclusions, destination, visibility, licensing
  verdict, and publication consequences before required authorization.
- Audit issue publication: an explicit audit invocation authorizes only the issue write its skill
  describes, not code fixes, commits, or PRs.
- Scientific output and method decisions: show the proposed contract or method, alternatives, and
  consequences before required confirmation. Scientific consent is separate from execution consent.
