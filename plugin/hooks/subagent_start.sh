#!/bin/sh
# SubagentStart hook: SessionStart context is parent-thread only and never
# reaches a Task-spawned subagent, so every worker would otherwise run
# captain-unaware and the brief would have to restate the house rules by hand.
# Inject the invariant half here; the brief carries only the task-specific half.
#
# Scoping: TSUBASA_SUBAGENT_MATCHER holds an unanchored, case-insensitive regex
# matched against agent_type ("explore|general" matches either, "^Explore$" is
# exact). Unset means inject into every subagent.
#
# Fail open everywhere: an unparseable payload, a bad regex or an unreadable
# stdin all inject. A worker told the rules twice is harmless; one that silently
# gets none is the bug this fixes.
emit() {
  # SubagentStart drops raw stdout; only hookSpecificOutput.additionalContext lands.
  cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"House rules for work in this repo (from the captain, non-negotiable):\n- Cite an event/ADR/PR/file for every claim about this system. If you cannot cite it, say \"I don't know\" instead of guessing.\n- Knowledge you are missing is in the graph, not just the code: run `tsubasa query \"<topic>\"`.\n- Commits and PRs carry no AI attribution: no Co-Authored-By, no \"Generated with\" trailer.\n- Branch names carry the ADR id from your brief.\n- Permissions, credentials and secret values: escalate to the captain. Never guess one, never invent one, never commit one."}}
JSON
  exit 0
}

# Captain house rules only apply inside a captain workspace.
dir="$(pwd)"
while :; do
  [ -f "$dir/.tsubasa/captain.toml" ] && break
  [ "$dir" = "/" ] && exit 0
  dir=$(dirname "$dir")
done

# No matcher: stay stdin-independent so a shell that never closes the pipe
# cannot stall a subagent spawn.
[ -n "${TSUBASA_SUBAGENT_MATCHER:-}" ] || emit

payload=$(cat) || emit
agent_type=$(printf '%s' "$payload" |
  sed -n 's/.*"agent_type"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
[ -n "$agent_type" ] || emit

# grep exit 1 is a definite mismatch; 2 (bad regex) falls through to emit.
printf '%s\n' "$agent_type" | grep -Eiq -- "$TSUBASA_SUBAGENT_MATCHER"
[ $? -eq 1 ] && exit 0
emit
