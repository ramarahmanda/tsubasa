#!/bin/sh
# SessionStart hook: load the Captain persona + hot knowledge tier.
# Walks up from cwd to find .tsubasa/ (a captain may live at a workspace root).
#
# Fires on startup|resume|clear|compact. On clear/compact the conversation is
# reset but the system prompt survives, so CLAUDE.md still imports .tsubasa/
# persona.md and memory/hot.md. Everything those two carry is therefore already
# in front of the model, and restating it here would be a second copy free to
# drift from the first. What this hook adds is the pair the model demonstrably
# drifts from mid-session and no file can assert as an imperative: who it is,
# and what its first tool call must be, plus one line pointing at persona.md so
# the rules there read as the captain's own and not as background text.
# The knowledge dump stays behind the clear|compact guard: re-injecting it is
# exactly wrong at the moment context got tight.
event=$(sed -n 's/.*"source"[[:space:]]*:[[:space:]]*"\([a-z]*\)".*/\1/p' | head -1)
here=$(dirname "$0")
dir="$(pwd)"
while [ "$dir" != "/" ]; do
  if [ -f "$dir/.tsubasa/captain.toml" ]; then
    name=$(sed -n 's/^name *= *"\(.*\)"/\1/p' "$dir/.tsubasa/captain.toml" | head -1)
    role=$(sed -n 's/^role *= *"\(.*\)"/\1/p' "$dir/.tsubasa/captain.toml" | head -1)
    echo "You are captain-${name:-captain} (${role:-Engineering Director}) of this repo."
    echo "GRAPH-FIRST (non-negotiable): for ANY question about this system and ANY"
    echo "design/change request, your FIRST tool call is: tsubasa query \"<topic>\""
    echo "(plus 'tsubasa goal list' before proposing designs). Only AFTER reading the"
    echo "graph do you search code — to verify current state, never as the starting"
    echo "point. Grep can miss what the graph remembers."
    echo "Your standing rules are in .tsubasa/persona.md, included by CLAUDE.md. Follow them."
    # Hooks bind at session start, so a workspace that turns delegate_only on
    # against an older plugin build gets no enforcement and no error. Say which.
    if grep -Eq '^[[:space:]]*delegate_only[[:space:]]*=[[:space:]]*true' \
         "$dir/.tsubasa/captain.toml"; then
      if [ -x "$here/delegate_only.sh" ] && grep -q delegate_only "$here/hooks.json" 2>/dev/null; then
        echo "delegate_only is ON: you cannot Edit/Write source in this session."
        echo "Writable: *.md, docs/**, **/adr/**, .tsubasa/**. Everything else goes to a subagent."
      else
        echo "WARNING: delegate_only = true but this plugin build has no PreToolUse"
        echo "hook, so nothing is enforced. Update the tsubasa plugin and restart."
      fi
    fi
    echo ""
    case "$event" in
      clear|compact) ;;
      *) [ -f "$dir/.tsubasa/memory/hot.md" ] && cat "$dir/.tsubasa/memory/hot.md" ;;
    esac
    exit 0
  fi
  dir=$(dirname "$dir")
done
exit 0
