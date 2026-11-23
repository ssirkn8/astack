---
name: source-comments
description: Improve code comments inside changed source files after implementing a change or applying review fixes. Not for writing GitHub or PR comments, filing or merging a PR, or attaching PR images.
metadata:
  permission-level: workspace-write
---

# Source Comments

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## When it applies

Run one pass after implementation and again after review fixes that change code, before opening or
updating a PR. Skip it when no source changed.

Goal: a person reading the PR diff or the review notebook's step page can understand and challenge
the code. Zero new comments is a good result for straightforward code. There is no comment quota.

Run it before `just reproduce`, `just ship-ready`, and human sign-off. Sign-off and DVC stages hash
exact file bytes, so even a comment edit resets a step's sign-off and marks its outputs stale.
Existing projects whose `AGENTS.md` does not load `project-policy` can opt in by adding one bullet
pointing to `.opencode/skills/source-comments/SKILL.md`.

## Scope

Edit only code this change touched, plus comments that it made wrong. Never rewrite unrelated
owner code, generated or vendored files, pinned upstream skills, or anything under `data/raw/`.
Keep each pipeline stage's one-line docstring, because the review notebook shows it as the step's
purpose.

## The pass

1. **Enforce in code first.** Turn checkable invariants such as units, CRS, schema, bounds, and
   uniqueness into assertions, types, or tests. A new check is a behavior change, so keep it inside
   this change's scope and rerun the checks.
2. **Prune.** Delete narration of what the next line does, notes about the editing session, and
   comments the final code made false.
3. **Explain what remains.** Ask where a reviewer would still lack context. Add a short comment next
   to that logic for a scientific, CRS, or numerical assumption, a tradeoff, a non-obvious
   invariant, an edge case, or a source. Cite only a source you actually read.

Never write that something is tested, validated, correct, reviewed, or approved. Those claims belong
in evidence, and comments go stale.

## Stop conditions

- If you cannot tell why code does something, do not invent a reason. Name the gap under the PR's
  Remaining risk.
- If a new check would reject real data or change a declared output, stop and apply the human
  decision policy.

## General examples

Enforce an invariant:

```python
# Before
# ids are unique
index = {row.id: row for row in rows}

# After
index = {row.id: row for row in rows}
if len(index) != len(rows):
    raise ValueError("duplicate row ids")
```

Prune narration:

```python
# Before
# Loop over users and collect their emails
emails = [user.email for user in users]

# After
emails = [user.email for user in users]
```

Explain a tradeoff:

```python
# The billing API treats a repeated POST as a new charge. Retry only connection failures,
# where the request never arrived.
retry = Retry(connect=3, read=0, redirect=0, status=0, other=0)
```

Leave straightforward code alone:

```python
def full_name(user: User) -> str:
    return f"{user.first_name} {user.last_name}"
```

## Data science examples

Enforce units and CRS:

```python
# Before
# parcels must be projected, in meters
area_km2 = parcels.area / 1e6

# After
crs = parcels.crs
if crs is None or not crs.is_projected or crs.axis_info[0].unit_name != "metre":
    raise ValueError(f"expected a projected CRS in meters, got {crs}")
area_km2 = parcels.area / 1e6
```

Prune a claim nobody verified:

```python
# Before
# Validated imputation, safe for production
filled = df["rainfall_mm"].fillna(0)

# After
# Assumes a missing gauge reading means no rain. Gauge outages would bias totals low.
filled = df["rainfall_mm"].fillna(0)
```

Explain a scientific assumption:

```python
# Split by site because repeat visits to one site are correlated. A random row split would
# leak site effects into the test folds and overstate skill.
scores = cross_val_score(model, X, y, groups=df["site_id"], cv=GroupKFold(n_splits=5))
```

Leave straightforward code alone:

```python
totals = df.groupby("site_id")["count"].sum()
```

## Validation and output

Reread each changed comment against the final code. Run the project's checks if the pass changed
anything beyond comments. Report in one line what was enforced, pruned, or added, or that no comment
changes were needed. Do not add that line to the code.
