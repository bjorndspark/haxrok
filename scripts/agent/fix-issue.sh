#!/usr/bin/env bash
#
# fix-issue.sh — hand a GitHub issue to an AI agent CLI, non-interactively.
#
# Usage:
#   make fix-issue n=<issue-number> [agent=devin]
#   ./scripts/agent/fix-issue.sh <issue-number> [agent]
#
# agent: devin (default) | opencode | claude — must be installed + authed.
#
# The agent runs in this checkout: reads AGENTS.md, implements the fix,
# runs make lint/test, commits to main, pushes, and closes the issue.
#
set -euo pipefail

ISSUE="${1:?usage: fix-issue.sh <issue-number> [agent]}"
AGENT="${2:-devin}"

command -v gh >/dev/null || { echo "error: gh CLI required (gh auth login)" >&2; exit 1; }
command -v "$AGENT" >/dev/null || { echo "error: agent CLI '$AGENT' not on PATH" >&2; exit 1; }

REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

PROMPT_FILE="$(mktemp "/tmp/issue-${ISSUE}-XXXXXX.md")"
trap 'rm -f "$PROMPT_FILE"' EXIT

# The agent runs unattended with full permissions and pushes to main, so
# the issue text is effectively code. Only people with write access may
# author it: refuse anyone else's issue, and drop their comments.
AUTHOR="$(gh issue view "$ISSUE" --repo "$REPO" --json author --jq .author.login)"
PERMISSION="$(gh api "repos/${REPO}/collaborators/${AUTHOR}/permission" --jq .permission 2>/dev/null || echo none)"
case "$PERMISSION" in
  admin | maintain | write) ;;
  *)
    echo "error: issue #${ISSUE} is by ${AUTHOR} (permission: ${PERMISSION}); only collaborators with write access can hand issues to an agent" >&2
    exit 1
    ;;
esac

# gh's --jq embeds jq; no separate jq binary needed.
gh issue view "$ISSUE" --repo "$REPO" \
  --json number,title,body,labels,comments --jq '
    ([.comments[] | select(.authorAssociation | IN("OWNER", "MEMBER", "COLLABORATOR"))]) as $trusted |
    "# Issue #\(.number): \(.title)",
    "",
    "Labels: \([.labels[].name] | join(", "))",
    "",
    .body,
    (if ($trusted | length) > 0
     then "\n## Comments\n" + ([$trusted[] | "**\(.author.login):** \(.body)"] | join("\n\n"))
     else "" end)' > "$PROMPT_FILE"

cat >> "$PROMPT_FILE" <<EOF

## Instructions

- Read AGENTS.md first — it is the engineering contract (structure, naming,
  response envelope, git rules).
- Solo dev: commit directly to \`main\` — no branches, no PRs.
- Implement the fix, then run \`make lint\` and \`make test\` (app containers
  must be up — \`docker compose up -d\` first if they are not).
- If the change affects documented behavior, update the docs in the same
  commit: \`make docs\` for model/route changes, \`AGENTS.md\` for new
  commands, env vars or structure, \`DECISIONS.md\` for a real trade-off.
- Commit with an intent-named message and \`git push\`.
- Close the issue with \`gh issue close ${ISSUE} --comment\` naming the
  commit that fixed it.
EOF

echo "==> ${REPO} issue #${ISSUE} -> ${AGENT} (prompt: ${PROMPT_FILE})"

case "$AGENT" in
  devin)
    # -p prints and exits; dangerous mode runs unattended (no prompts possible
    # in non-interactive mode); trust check is skipped per Devin CLI docs.
    devin -p --prompt-file "$PROMPT_FILE" \
      --respect-workspace-trust false --permission-mode dangerous
    ;;
  opencode)
    opencode run "$(cat "$PROMPT_FILE")"
    ;;
  claude)
    claude -p "$(cat "$PROMPT_FILE")" --dangerously-skip-permissions
    ;;
  *)
    echo "error: unknown agent '$AGENT' (devin|opencode|claude)" >&2
    exit 1
    ;;
esac
