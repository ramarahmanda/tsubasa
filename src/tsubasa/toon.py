"""Minimal TOON (Token-Oriented Object Notation) codec.

Implements the subset tsubasa needs for its at-rest graph files:

  scalars            key: value
  nested objects     key:            (children indented 2)
  scalar arrays      key[3]: a,b,c
  tabular arrays     key[2]{f1,f2}:  (one indented row per item: v1,v2)
  object arrays      key[2]:         (items as "- " entries)
  block strings      key: |          (indented lines, for multiline bodies)

Strings that could be misread (contain commas/colons/newlines, look like
numbers or booleans, have edge whitespace) are JSON-quoted. Round-trip of
encode() output through decode() is guaranteed for JSON-able dicts whose
keys are strings.
"""

from __future__ import annotations

import json
import re

INDENT = "  "
_PLAIN_RE = re.compile(r"^[^\s\"#,:\[\]{}|-][^,:\n\"]*$")
_NUMLIKE_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


# ---------------------------------------------------------------- encoding

def _fmt_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    if _PLAIN_RE.match(s) and not _NUMLIKE_RE.match(s) and s not in ("true", "false", "null") and s == s.strip():
        return s
    return json.dumps(s, ensure_ascii=False)


def _fmt_key(k: str) -> str:
    return k if _KEY_RE.match(k) else json.dumps(k, ensure_ascii=False)


def _is_scalar(v) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _is_tabular(items: list) -> bool:
    if len(items) < 2 or not all(isinstance(i, dict) and i for i in items):
        return False
    keys = list(items[0].keys())
    return all(list(i.keys()) == keys and all(_is_scalar(v) and not (isinstance(v, str) and "\n" in v) for v in i.values()) for i in items) and all(_KEY_RE.match(k) for k in keys)


def _encode_pair(key: str, value, depth: int, out: list[str]) -> None:
    pad = INDENT * depth
    k = _fmt_key(key)
    if isinstance(value, str) and "\n" in value:
        out.append(f"{pad}{k}: |")
        for line in value.split("\n"):
            out.append(f"{pad}{INDENT}{line}")
        return
    if _is_scalar(value):
        out.append(f"{pad}{k}: {_fmt_scalar(value)}")
        return
    if isinstance(value, dict):
        out.append(f"{pad}{k}:")
        for ck, cv in value.items():
            _encode_pair(str(ck), cv, depth + 1, out)
        return
    if isinstance(value, list):
        n = len(value)
        if n == 0:
            out.append(f"{pad}{k}[0]:")
            return
        if all(_is_scalar(v) and not (isinstance(v, str) and "\n" in v) for v in value):
            out.append(f"{pad}{k}[{n}]: " + ",".join(_fmt_scalar(v) for v in value))
            return
        if _is_tabular(value):
            fields = list(value[0].keys())
            out.append(f"{pad}{k}[{n}]{{{','.join(fields)}}}:")
            for item in value:
                out.append(f"{pad}{INDENT}" + ",".join(_fmt_scalar(item[f]) for f in fields))
            return
        out.append(f"{pad}{k}[{n}]:")
        for item in value:
            _encode_item(item, depth + 1, out)
        return
    raise TypeError(f"cannot encode {type(value)}")


def _encode_item(item, depth: int, out: list[str]) -> None:
    pad = INDENT * depth
    if _is_scalar(item) and not (isinstance(item, str) and "\n" in item):
        out.append(f"{pad}- {_fmt_scalar(item)}")
        return
    if isinstance(item, dict) and item:
        first = True
        for k, v in item.items():
            if first:
                sub: list[str] = []
                _encode_pair(str(k), v, 0, sub)
                out.append(f"{pad}- {sub[0]}")
                out.extend(f"{pad}{INDENT}{line}" for line in sub[1:])
                first = False
            else:
                _encode_pair(str(k), v, depth + 1, out)
        return
    raise TypeError(f"cannot encode list item {type(item)}")


def encode(obj: dict) -> str:
    if not isinstance(obj, dict):
        raise TypeError("top-level TOON value must be a dict")
    out: list[str] = []
    for k, v in obj.items():
        _encode_pair(str(k), v, 0, out)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- decoding

_HEAD_RE = re.compile(
    r"""^(?P<key>"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_.-]*)   # key
        (?:\[(?P<n>\d+)\](?:\{(?P<fields>[^}]*)\})?)?           # [N] or [N]{f,f}
        :(?:\s(?P<rest>.*))?$""",
    re.VERBOSE,
)


class ToonError(ValueError):
    pass


def _parse_scalar(tok: str):
    tok = tok.strip()
    if tok.startswith('"'):
        return json.loads(tok)
    if tok == "null":
        return None
    if tok == "true":
        return True
    if tok == "false":
        return False
    if _NUMLIKE_RE.match(tok):
        return json.loads(tok)
    return tok


def _split_row(row: str) -> list[str]:
    """Split on commas, respecting JSON-quoted segments."""
    parts, buf, in_q, esc = [], [], False, False
    for ch in row:
        if in_q:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_q = False
        elif ch == '"':
            in_q = True
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


class _Parser:
    def __init__(self, lines: list[tuple[int, str, str]]):
        self.lines = lines  # (indent, content, raw); blanks kept as (-1, "", "")
        self.pos = 0

    def peek(self):
        """Next non-blank line (blanks are only meaningful inside blocks)."""
        while self.pos < len(self.lines) and self.lines[self.pos][0] == -1:
            self.pos += 1
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def parse_object(self, indent: int) -> dict:
        obj: dict = {}
        while (cur := self.peek()) is not None and cur[0] == indent and not cur[1].startswith("- "):
            self.pos += 1
            key, value = self._parse_pair(cur[1], indent)
            obj[key] = value
        return obj

    def _parse_pair(self, content: str, indent: int):
        m = _HEAD_RE.match(content)
        if not m:
            raise ToonError(f"bad line: {content!r}")
        key = json.loads(m["key"]) if m["key"].startswith('"') else m["key"]
        rest = m["rest"]
        if m["n"] is not None:
            return key, self._parse_array(int(m["n"]), m["fields"], rest, indent)
        if rest is None or rest == "":
            return key, self.parse_object(indent + 1)
        if rest in ("|", ">"):
            # ">" is YAML folded-block syntax; hand-authored files use it —
            # accept it as a literal block rather than failing the whole doc
            return key, self._parse_block(indent + 1)
        return key, _parse_scalar(rest)

    def _parse_block(self, indent: int) -> str:
        # Consume raw lines (including blanks) while indentation stays inside
        # the block; trailing blanks belong to the document, not the block.
        strip = len(INDENT) * indent
        collected: list[str] = []
        while self.pos < len(self.lines):
            lvl, _, raw = self.lines[self.pos]
            if lvl == -1:
                collected.append("")
                self.pos += 1
                continue
            if lvl < indent:
                break
            collected.append(raw[strip:])
            self.pos += 1
        while collected and collected[-1] == "":
            collected.pop()
        return "\n".join(collected)

    def _parse_array(self, n: int, fields: str | None, rest: str | None, indent: int) -> list:
        if fields is not None:
            names = [f.strip() for f in fields.split(",") if f.strip()]
            rows = []
            while (cur := self.peek()) is not None and cur[0] == indent + 1 and len(rows) < n:
                self.pos += 1
                vals = [_parse_scalar(t) for t in _split_row(cur[1])]
                if len(vals) != len(names):
                    raise ToonError(f"tabular row has {len(vals)} values, expected {len(names)}: {cur[1]!r}")
                rows.append(dict(zip(names, vals)))
            return rows
        if rest:  # inline scalar array
            return [_parse_scalar(t) for t in _split_row(rest)]
        items = []
        while (cur := self.peek()) is not None and cur[0] == indent + 1 and cur[1].startswith("- ") and len(items) < n:
            self.pos += 1
            head = cur[1][2:]
            if _HEAD_RE.match(head) and (":" in head):
                k, v = self._parse_pair(head, indent + 1)
                item = {k: v}
                item.update(self.parse_object(indent + 2))
                items.append(item)
            else:
                items.append(_parse_scalar(head))
        return items


def decode(text: str) -> dict:
    lines: list[tuple[int, str, str]] = []
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append((-1, "", ""))  # blanks matter inside block strings
            continue
        stripped = raw.lstrip(" ")
        spaces = len(raw) - len(stripped)
        # Odd indent can only come from free text inside a block string; the
        # floor level keeps it inside the block, and blocks read `raw` anyway.
        lines.append((spaces // len(INDENT), stripped.rstrip(), raw))
    parser = _Parser(lines)
    obj = parser.parse_object(0)
    if parser.peek() is not None:
        raise ToonError(f"unparsed content from line: {parser.lines[parser.pos]!r}")
    return obj
