"""Semantic query expansion, graphify-style: the query ladder's second rung.

`match_entities` and `title_events` are lexical: a question whose wording
shares no stems with the record's names returns nothing. The caller
(`tsubasa query`) escalates here exactly when its lexical pass finds no
title-matched events, i.e. the verdict surface is empty. One headless Claude
call reads the FULL graph vocabulary and selects up to 6 tokens that
semantically match the question; anything not verbatim in the vocabulary is
discarded, so the expansion can widen recall but never invent a record. Any
failure falls back to the lexical path; a query never fails or blocks on the
expansion.

Each call appends one JSON line of cost accounting to $TSUBASA_SEMANTIC_LOG
when set, else `.tsubasa/semantic-cost.jsonl` under the captain root. That
path is outside the benchmark graph fingerprint (guard.py GRAPH_FILES covers
graph/*.toon, memory/*.md, state.toon, events/, tasks/), and if the fixture
is read-only the record goes to stderr instead of failing the query.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import llm
from .config import TSUBASA_DIR
from .models import now_iso

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# 6, not 12: a wide selection full of common stems (error, buffer, return)
# gives many irrelevant titles multiple hits and floods the ranked caps
MAX_TOKENS = 6
TIMEOUT = 120  # a query must not hang on an expansion that a fallback covers

# Hard constraints verbatim from graphify's query reference (step 0,
# ~/.claude/skills/graphify/references/query.md): the model may only select,
# never invent.
PROMPT = """\
You bridge a question onto a knowledge graph's searchable vocabulary.

Question: {question}

Vocabulary (every searchable token in the graph):
{vocab}

Select up to {cap} tokens from this exact list that semantically match the
query intent. Hard constraints:
- You MUST pick only tokens present in the vocabulary. Do NOT invent tokens.
- If a query concept has no plausible token in the vocab, skip it — do not
  substitute a near-synonym from training memory.
- If no vocab tokens match the query at all, output an empty list.
- Prefer the rarest, most discriminating tokens for this question.
- Avoid generic tokens (error, buffer, check, return) unless nothing rarer
  matches the concept.

Reply with ONLY a JSON array of the selected tokens, e.g. ["token1","token2"]
or [].
"""


def expand(root: Path, question: str, vocab: dict[str, int]) -> list[str]:
    """Vocab tokens semantically near the question, [] when none or on failure.

    Prints one audit line so the expansion is visible in the query output;
    never raises — the lexical path must survive any model failure.
    """
    model = DEFAULT_MODEL
    started = time.monotonic()
    try:
        picked, cost = _ask(question, vocab, model)
    except Exception as e:
        print(f"semantic expansion: unavailable ({e}); lexical match only",
              file=sys.stderr)
        return []
    elapsed = time.monotonic() - started
    accepted: list[str] = []
    for tok in picked:
        tok = str(tok).strip().lower()
        # strict parse: a token the vocabulary does not carry is invented
        if tok in vocab and tok not in accepted:
            accepted.append(tok)
        if len(accepted) >= MAX_TOKENS:
            break
    _log(root, question, model, accepted, cost, elapsed)
    if accepted:
        print(f"semantic expansion: [{' '.join(accepted)}] "
              f"({model}, ${cost:.4f}, {elapsed:.1f}s)")
    else:
        print("semantic expansion: none matched")
    return accepted


def _ask(question: str, vocab: dict[str, int], model: str) -> tuple[list, float]:
    prompt = PROMPT.format(question=question, vocab=" ".join(sorted(vocab)),
                           cap=MAX_TOKENS)
    envelope = llm.run_claude_json(prompt, model=model, timeout=TIMEOUT)
    picked = llm.extract_json(str(envelope.get("result", "")))
    if not isinstance(picked, list):
        raise llm.LLMError(f"expected a JSON array, got {type(picked).__name__}")
    return picked, float(envelope.get("total_cost_usd") or 0.0)


def _log(root: Path, question: str, model: str, tokens: list[str],
         cost: float, elapsed: float) -> None:
    line = json.dumps({"ts": now_iso(), "question": question, "model": model,
                       "tokens": tokens, "cost_usd": cost,
                       "seconds": round(elapsed, 2)})
    override = os.environ.get("TSUBASA_SEMANTIC_LOG", "")
    path = Path(override) if override else root / TSUBASA_DIR / "semantic-cost.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:  # read-only fixture: the record survives, the query does too
        print(f"semantic-cost: {line}", file=sys.stderr)
