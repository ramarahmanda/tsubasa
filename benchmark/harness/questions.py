"""Parse benchmark/questions/*.md into Question records.

Question file shape, per category file:

    ### <ID> — <title>
    > the question, possibly wrapped over several `>` lines
    **Gold.** ...
    **Locator.** ...
    **Trap.** / **Withheld term.** / **Repos required.** / ...

Category f-negative is the one structural exception: its items carry
**Why unanswerable.** and **Confabulation looks like.** in place of
**Gold.** / **Trap.**, and no **Locator.** at all, because the correct answer
is that no locator exists. Those are normalised here rather than special-cased
downstream, and `gold_kind` records which shape the item came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from config import CATEGORY_ORDER, QUESTIONS_DIR

HEADING_RE = re.compile(r"^###\s+([A-Z])(\d+)\s*(?:[—-]\s*(.*))?$")
SECTION_END_RE = re.compile(r"^##+\s")
# A field marker is a whole line starting with **Name.** — short, capitalised,
# period inside the bold. Inline emphasis (**physical replication**, **beta**)
# never matches: it is mid-line, and has no trailing period inside the markers.
FIELD_RE = re.compile(r"^\*\*([A-Z][^*\n]{0,40}?)\.\*\*[ \t]*(.*)$")

GOLD_FIELDS = ("gold", "why unanswerable")
TRAP_FIELDS = ("trap", "confabulation looks like", "failure mode")

# **Required.** / **Forbidden.** items: `- ` starts an item, an indented
# `forms:` / `sources:` line qualifies the item above it, anything else
# continues the current line. Wrapping is free; the leading `- ` is the only
# structural marker.
ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
QUALIFIER_RE = re.compile(r"^\s+(forms|sources):\s*(.*)$", re.IGNORECASE)


@dataclass
class Question:
    qid: str                     # "C7"
    category: str                # "c-status"
    title: str
    prompt_text: str             # the blockquote, verbatim: the ONLY thing the arms see
    gold: str
    gold_kind: str               # "gold" | "unanswerable"
    locator: str
    trap: str
    # the workspace rubric: what must be said, and what is false here. Empty
    # for a question that has not been given one yet; the judge falls back to
    # grading against `gold` for those.
    required: list[dict] = field(default_factory=list)     # {fact, forms, sources}
    forbidden: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)   # every parsed marker, verbatim
    category_rubric: str = ""    # the category file's own pass/fail preamble
    honesty_probe: bool = False
    expect_abstain: bool = False
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def order_key(self) -> tuple[int, int]:
        cat = CATEGORY_ORDER.index(self.category)
        return (cat, int(self.qid[1:]))

    @property
    def has_rubric(self) -> bool:
        return bool(self.required)

    def to_dict(self) -> dict:
        return {
            "qid": self.qid, "category": self.category, "title": self.title,
            "prompt_text": self.prompt_text, "gold": self.gold, "gold_kind": self.gold_kind,
            "locator": self.locator, "trap": self.trap,
            "required": self.required, "forbidden": self.forbidden, "fields": self.fields,
            "category_rubric": self.category_rubric, "honesty_probe": self.honesty_probe,
            "expect_abstain": self.expect_abstain, "parse_warnings": self.parse_warnings,
        }


def _blockquote(lines: list[str]) -> str:
    """First contiguous run of `>` lines in the block."""
    out, started = [], False
    for line in lines:
        if line.startswith(">"):
            out.append(line[1:].strip())
            started = True
        elif started:
            break
    return " ".join(x for x in out if x).strip()


def _fields(lines: list[str]) -> dict[str, str]:
    """Every `**Name.** body` marker in the block, body joined until the next
    marker or a horizontal rule."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        m = FIELD_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            found.setdefault(current, [])
            if m.group(2).strip():
                found[current].append(m.group(2).strip())
            continue
        if line.strip() == "---":
            current = None
            continue
        if current is not None:
            found[current].append(line.rstrip())
    return {k: "\n".join(v).strip() for k, v in found.items()}


def _items(body: str) -> list[str]:
    """`- ` delimited items, wrapped lines folded back in."""
    out: list[str] = []
    for line in body.splitlines():
        m = ITEM_RE.match(line)
        if m:
            out.append(m.group(1).strip())
        elif line.strip() and out:
            out[-1] += " " + line.strip()
    return [x for x in out if x]


def _required(body: str) -> list[dict]:
    """One record per required fact: what must be said, the forms that count,
    and every source that supports it. `forms`/`sources` are optional; a fact
    with no sources is a rubric bug and is reported as a parse warning."""
    facts: list[dict] = []
    key: str | None = None
    for line in body.splitlines():
        item = ITEM_RE.match(line)
        qual = QUALIFIER_RE.match(line) if not item else None
        if item:
            facts.append({"fact": item.group(1).strip(), "forms": "", "sources": ""})
            key = "fact"
        elif qual and facts:
            key = qual.group(1).lower()
            facts[-1][key] = qual.group(2).strip()
        elif line.strip() and facts and key:
            facts[-1][key] += " " + line.strip()
    return facts


def _rubric(text: str) -> str:
    """The prose between the category file's H1 and its first `---`: the
    category's own statement of what passing means."""
    head = text.split("\n---", 1)[0]
    body = [ln.strip() for ln in head.splitlines()[1:] if ln.strip()]
    return " ".join(body)


def parse_file(path: Path) -> list[Question]:
    text = path.read_text()
    category = path.stem
    rubric = _rubric(text)
    lines = text.splitlines()

    starts = [i for i, ln in enumerate(lines) if HEADING_RE.match(ln)]
    questions: list[Question] = []
    for n, start in enumerate(starts):
        # a block ends at the next question OR at the next `##` section
        # (f-negative and g-git-history both append a verification appendix)
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        for i in range(start + 1, end):
            if SECTION_END_RE.match(lines[i]):
                end = i
                break
        block = lines[start:end]
        m = HEADING_RE.match(block[0])
        assert m
        qid = f"{m.group(1)}{m.group(2)}"
        title = (m.group(3) or "").strip()

        fields = _fields(block[1:])
        warnings: list[str] = []

        prompt = _blockquote(block[1:])
        if not prompt:
            warnings.append("no blockquote question found")

        gold, gold_kind = "", ""
        for marker in GOLD_FIELDS:
            if marker in fields:
                gold = fields[marker]
                gold_kind = "unanswerable" if marker == "why unanswerable" else "gold"
                break
        if not gold:
            warnings.append("no **Gold.** or **Why unanswerable.** field")

        locator = fields.get("locator", "")
        if not locator:
            # f-negative items have no locator by construction: nothing to point at.
            if gold_kind == "unanswerable":
                warnings.append("no **Locator.** (expected: the item is unanswerable by design)")
            else:
                warnings.append("no **Locator.**")

        trap = ""
        for marker in TRAP_FIELDS:
            if marker in fields:
                trap = fields[marker]
                break

        required = _required(fields.get("required", ""))
        forbidden = _items(fields.get("forbidden", ""))
        for fact in required:
            if not fact["sources"]:
                warnings.append(f"**Required.** fact with no sources: {fact['fact'][:60]!r}")

        honesty = "honesty probe" in fields
        questions.append(Question(
            qid=qid, category=category, title=title, prompt_text=prompt,
            gold=gold, gold_kind=gold_kind or "gold", locator=locator, trap=trap,
            required=required, forbidden=forbidden,
            fields=fields, category_rubric=rubric, honesty_probe=honesty,
            expect_abstain=(gold_kind == "unanswerable" or honesty),
            parse_warnings=warnings,
        ))
    return questions


def load_questions(questions_dir: Path = QUESTIONS_DIR) -> list[Question]:
    """All questions in canonical order: category file order, then qid number."""
    out: list[Question] = []
    for name in CATEGORY_ORDER:
        path = questions_dir / f"{name}.md"
        if path.is_file():
            out.extend(parse_file(path))
    for extra in sorted(questions_dir.glob("*.md")):
        if extra.stem not in CATEGORY_ORDER:
            raise SystemExit(f"unknown category file {extra} (add it to CATEGORY_ORDER)")
    out.sort(key=lambda q: q.order_key)
    return out


def usable(q: Question) -> bool:
    """Has both a question to ask and something to grade against."""
    return bool(q.prompt_text) and bool(q.gold)
