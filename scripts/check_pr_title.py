"""Check one intended squash subject. Intermediate branch commits are never checked."""

from __future__ import annotations

import os
import re
import sys

SUBJECT = re.compile(
    r"(?:feat|fix|perf|docs|style|chore|test|refactor|build|ci|revert)"
    r"(?:\([^()\r\n]+\))?!?: [^\r\n]+"
)
HELP = (
    "Use a Conventional Commit title, because it becomes the squash subject that Release "
    "Please reads. Examples: 'fix: keep user files', 'feat: add a check', "
    "'feat!: drop a command', 'docs: explain upgrades'."
)


def is_valid(title: str) -> bool:
    return SUBJECT.fullmatch(title) is not None


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    if is_valid(title):
        print(title)
        return 0
    print(f"Not a Conventional Commit subject: {title!r}. {HELP}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
