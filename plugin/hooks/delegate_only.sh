#!/bin/sh
# PreToolUse hook: the captain delegates, workers implement.
#
# Subagents share the parent's session_id, pid and env; `agent_id` is present
# only in a subagent's payload and is the sole usable discriminator (verified
# against Claude Code 2.1.220).
#
# Default-on at init: `tsubasa init` scaffolds `delegate_only = true` under
# [captain] in .tsubasa/captain.toml, and `tsubasa upgrade` adds the key to a
# config that lacks it. The grep below is unchanged: absent or false still
# exits before reading stdin, so a captain whose config predates the key stays
# unarmed until upgraded, and an explicit `false` disarms.
#
# Armed, the block is path-scoped, not blanket: captain-capture has the captain
# write ADRs itself, so knowledge paths stay writable and source code does not.
dir="$(pwd)"
while :; do
  [ -f "$dir/.tsubasa/captain.toml" ] && break
  [ "$dir" = "/" ] && exit 0
  dir=$(dirname "$dir")
done
grep -Eq '^[[:space:]]*delegate_only[[:space:]]*=[[:space:]]*true' \
  "$dir/.tsubasa/captain.toml" || exit 0

input=$(cat)
if command -v jq >/dev/null 2>&1; then
  agent_id=$(printf '%s' "$input" | jq -r '.agent_id // empty' 2>/dev/null)
  path=$(printf '%s' "$input" |
    jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)
else
  # BSD sed has no \| alternation, so field by field.
  field() {
    printf '%s' "$input" |
      sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
  }
  agent_id=$(field agent_id)
  path=$(field file_path)
  [ -n "$path" ] || path=$(field notebook_path)
fi

[ -n "$agent_id" ] && exit 0   # a worker: this is exactly what should be writing

# Cannot see what is being written: allow rather than block on a payload we
# do not understand. This is a discipline guard, not a security boundary.
[ -n "$path" ] || exit 0

case "$path" in
  *.md|*.markdown) exit 0 ;;
  .tsubasa/*|*/.tsubasa/*) exit 0 ;;
  docs/*|*/docs/*) exit 0 ;;
  adr/*|*/adr/*) exit 0 ;;
esac

cat >&2 <<EOF
Blocked: $path
The captain does not write code. Brief a subagent with the Agent tool and let it
implement (captain-delegate). Writable here: *.md, docs/**, **/adr/**, .tsubasa/**.
Turn this off with delegate_only = false in .tsubasa/captain.toml (takes effect now).
EOF
exit 2
