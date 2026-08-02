#!/bin/sh
# PreToolUse hook on the Agent tool: remind the captain to arm the fleet
# watch (45s Monitor + /loop 3m heartbeat) at the moment it spawns a worker.
# A reminder, not a guard: it never blocks the spawn.
#
# Delivery verified against Claude Code 2.1.220: PreToolUse JSON output may
# carry hookSpecificOutput.additionalContext (optional in the schema next to
# permissionDecision/updatedInput), and it is injected into the model context
# as a hook_additional_context message, independent of the permission flow.
# Plain stdout on exit 0 does NOT reach the model for PreToolUse, and exit 2
# blocks, so JSON with additionalContext alone is the only non-blocking route.
# permissionDecision is deliberately omitted: the permission system is
# untouched.
dir="$(pwd)"
while :; do
  [ -f "$dir/.tsubasa/captain.toml" ] && break
  [ "$dir" = "/" ] && exit 0
  dir=$(dirname "$dir")
done

input=$(cat)
if command -v jq >/dev/null 2>&1; then
  agent_id=$(printf '%s' "$input" | jq -r '.agent_id // empty' 2>/dev/null)
else
  agent_id=$(printf '%s' "$input" |
    sed -n 's/.*"agent_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi
# A worker spawning a helper supervises its own; the reminder is the captain's.
[ -n "$agent_id" ] && exit 0

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"fleet watch: arm the 45s monitor + /loop 3m heartbeat if not armed (captain-delegate)"}}
EOF
exit 0
