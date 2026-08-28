#!/bin/sh
# UserPromptSubmit hook: a session that touched two contexts just got a prompt
# that names neither ("push the fix"). Ask which one before anything moves.
# adr-session-context-regrouping.
#
# Thin by design: the decision is mechanical but not shell-shaped (transcript
# JSONL, regex target extraction, deixis), so it lives in `tsubasa
# context-check` and this file only carries the payload there and the answer
# back. No model call on this path; it runs on every prompt.
#
# Silence is the normal outcome. No captain here, no CLI on PATH, an
# unparseable payload, a missing transcript: exit 0 and the prompt goes through
# untouched. Breaking a prompt costs more than missing an ambiguous one.
dir="$(pwd)"
while :; do
  [ -f "$dir/.tsubasa/captain.toml" ] && break
  [ "$dir" = "/" ] && exit 0
  dir=$(dirname "$dir")
done

command -v tsubasa >/dev/null 2>&1 || exit 0
input=$(cat) || exit 0
text=$(printf '%s' "$input" | tsubasa context-check 2>/dev/null) || exit 0
[ -n "$text" ] || exit 0

# UserPromptSubmit does surface plain stdout, but only additionalContext is
# labelled as hook context rather than as the user speaking. Target names come
# out of the transcript, so the string is escaped, never interpolated raw.
if command -v jq >/dev/null 2>&1; then
  ctx=$(printf '%s' "$text" | jq -Rs .)
else
  ctx=$(printf '%s' "$text" | awk '
    { gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); out = out sep $0; sep = "\\n" }
    END { printf "\"%s\"", out }')
fi
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":%s}}\n' "$ctx"
exit 0
