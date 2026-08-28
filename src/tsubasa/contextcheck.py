"""Session context regrouping: decide *when* to ask, never *what* the groups are.

adr-session-context-regrouping. A UserPromptSubmit hook runs on every prompt, so
it cannot afford a model call. Everything here is regex and counting; the
captain does the grouping from the session it already holds.

Both halves must hold before the hook speaks:

    a) the session touched >= 2 distinct target sets
    b) the prompt is deictic and names none of them

False-asks cost more than misses (ADR, Context), so every ambiguity resolves
toward silence: a dimension that does not vary is ambient rather than a
context, a value mentioned once is noise, and any parse failure yields nothing.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

# The captain supplies the grouping; the hook supplies only the evidence for it.
TEMPLATE = """CONTEXT CHECK: this session touches {n} contexts ({names}) and this prompt
names none of them. Before any tool call: compact the whole session, weighted
toward the last 10 turns, group it by context, and print for each group:
doing / access / dev / test / next. Then ask which one. Do not act until the
user answers."""

# Deixis: an action verb plus a reference with no referent. BOTH are required
# (ADR: "push/deploy/run/apply/ship/commit + it/that/the fix/same"), so "deploy
# the ab chart" is not deictic and "deploy it" is. The lists are closed and
# exact-word on purpose: every word added trades precision for recall, and
# tuning them against the false-ask rate is a release step (ADR, Phases 4).
VERBS = ("push", "deploy", "run", "apply", "ship", "commit", "merge", "rotate", "delete")
REFS = ("it", "that", "this one", "the fix", "same", "again")

_VERB_RE = re.compile(r"\b(?:%s)\b" % "|".join(VERBS), re.I)
_REF_RE = re.compile(r"\b(?:%s)\b" % "|".join(r.replace(" ", r"\s+") for r in REFS), re.I)
# A word in either list can never also be a context name: "deploy" the verb must
# not be masked by "deploy/ab" the path head.
_CLOSED = {w for phrase in VERBS + REFS for w in phrase.split()}

# `-n` is a namespace next to kubectl and a line number next to grep, so the
# flag only counts in a blob that names a cluster tool.
_K8S_RE = re.compile(r"\b(?:kubectl|kubens|helm|oc|k9s|argocd|flux)\b", re.I)
_NS_RE = re.compile(r"(?:^|\s)(?:-n|--namespace)[=\s]\s*([A-Za-z0-9][\w.-]*)")
_VAULT_RE = re.compile(r"\bvault\s+(?:[-\w=.]+\s+)*?([A-Za-z0-9][\w.-]*/[\w./-]*)", re.I)
_VAULT_MOUNT_RE = re.compile(r"^(?:secret|kv)/(?:data/|metadata/)?", re.I)
# >= 3 labels ending in letters: "api.stg.example.com" is a host, "2.1.226" and
# "values.yaml" are not.
_HOST_RE = re.compile(r"\b([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+\.[a-z]{2,})\b", re.I)
# >= 3 segments, so both segments of the head are directories: "deploy/ab" is a
# context, "tests/test_hooks.py" is a file. The lookbehind keeps the match at a
# path boundary, which also drops the host and path halves of a URL.
_PATH_RE = re.compile(r"(?<![\w./-])(/?(?:[A-Za-z0-9][\w.-]*/){2,}[\w.-]+)")
# A dotted filename is not a host.
_EXTS = {"py", "md", "yaml", "yml", "json", "jsonl", "sh", "toml", "txt", "lock",
         "ts", "tsx", "js", "go", "rs", "toon", "tf", "sql", "log"}

# A file body is not a target; its path already is.
_SKIP_KEYS = ("content", "new_string", "old_string")
_MAX_BLOB = 2000     # a command or a prompt fits; a pasted file does not need to
_MAX_LINES = 20000   # a hook must stay cheap on a long session


def is_deictic(prompt: str) -> bool:
    return bool(_VERB_RE.search(prompt) and _REF_RE.search(prompt))


def _records(path: Path):
    """(cwd, [text]) per transcript line. Tool INPUTS and user prompts only:
    tool output is what the session read, not what it acted on."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if n >= _MAX_LINES:
                return
            try:
                rec = json.loads(line)
                kind = rec.get("type")
                content = rec.get("message", {}).get("content")
            except Exception:
                continue  # the transcript schema is not a contract: skip, never raise
            blobs = []
            if kind == "user" and not rec.get("isMeta"):
                if isinstance(content, str):
                    blobs.append(content)
                elif isinstance(content, list):
                    blobs += [b["text"] for b in content
                              if isinstance(b, dict) and b.get("type") == "text"
                              and isinstance(b.get("text"), str)]
            elif kind == "assistant" and isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    blobs += [v for k, v in (b.get("input") or {}).items()
                              if isinstance(v, str) and k not in _SKIP_KEYS]
            yield rec.get("cwd") if isinstance(rec.get("cwd"), str) else None, \
                [b[:_MAX_BLOB] for b in blobs]


def _path_head(raw: str, roots: list[str]) -> str | None:
    for r in roots:  # a cwd prefix is where the session lives, not what it targets
        if raw.startswith(r + "/"):
            raw = raw[len(r) + 1:]
            break
    segs = [s for s in raw.split("/") if s]
    return "/".join(segs[:2]) if len(segs) >= 3 else None


def scan(transcript_path: str | Path) -> dict[str, dict[str, int]]:
    """{dimension: {value: mentions}} over the whole session."""
    recs = list(_records(Path(transcript_path)))
    roots = sorted({c.rstrip("/") for c, _ in recs if c}, key=len, reverse=True)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for cwd, blobs in recs:
        if cwd:
            counts["repo"][Path(cwd).name.lower()] += 1
        for blob in blobs:
            if _K8S_RE.search(blob):
                for m in _NS_RE.finditer(blob):
                    counts["ns"][m.group(1).lower()] += 1
            for m in _VAULT_RE.finditer(blob):
                segs = [s for s in _VAULT_MOUNT_RE.sub("", m.group(1)).lower().split("/") if s]
                if len(segs) >= 2:
                    counts["vault"]["/".join(segs[:2])] += 1
            for m in _HOST_RE.finditer(blob):
                host = m.group(1).lower()
                if host.rsplit(".", 1)[-1] not in _EXTS:
                    counts["host"][host] += 1
            for m in _PATH_RE.finditer(blob):
                head = _path_head(m.group(1), roots)
                if head:  # one dimension per first segment: deploy/* and src/*
                    counts["path:" + head.split("/")[0]][head] += 1
    return counts


def targets(counts: dict[str, dict[str, int]]) -> list[str]:
    """The values of every dimension that actually varies.

    A namespace used all session, or the single repo the session lives in,
    cannot tell two contexts apart: it is ambient, and counting it would let one
    stray path head split an ordinary single-context session in two. A value
    must also be mentioned twice, so a path named once invents nothing.
    """
    out: list[str] = []
    for _, values in sorted(counts.items()):
        seen = sorted(v for v, n in values.items() if n >= 2)
        if len(seen) >= 2:
            out += seen
    return out


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[\W_]+", text.lower()) if len(t) > 1 and not t.isdigit()}


def names_a_target(prompt: str, names: list[str]) -> bool:
    """Does the prompt say which context it means?

    Only discriminating tokens count. A token every target shares ("akasha" in
    both akasha/ab and akasha-zy) picks out neither, and a token from the
    deictic lists is a verb before it is a name.
    """
    sets = [_tokens(n) for n in names]
    discriminators = set().union(*sets) - set.intersection(*sets) - _CLOSED
    return bool(_tokens(prompt) & discriminators)


def injection(prompt: str, transcript_path: str | Path) -> str | None:
    """The text to inject, or None to stay silent."""
    names = targets(scan(transcript_path))
    if len(names) < 2 or not is_deictic(prompt) or names_a_target(prompt, names):
        return None
    return TEMPLATE.format(n=len(names), names=", ".join(names))
