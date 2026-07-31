"""ADR adapter: docs/adr/*.md → adr entities + events.

Understands MADR-ish markdown: title from the first heading, optional
`Status:`/`Date:`/`Superseded by:` lines or YAML frontmatter. The ADR id
is derived from the filename (adr-<slug> convention enforced).

Metadata often lives in a sibling YAML file instead of inside the
markdown (`<stem>.yaml`, or `kep.yaml`/`metadata.yaml`/… next to a
`README.md`) — the shape used by k8s KEPs and similar corpora. Those
files carry the curated title, status and supersession edges, so they are
merged in; see `_sibling_path` and `_sibling_meta`.

A sibling tracker is edited in place: its `status` is the value *today*,
not the value on `creation-date`. Folding it into the creation-dated event
makes `--as-of` assert a status the graph cannot know held then, so the
mutable half is split out into its own event dated when the status was
actually observed; see `_status_event`. Most trackers carry no
`last-updated` at all (423 of 656 on the reference corpus), so that date
is recovered from the history of the tracker file rather than asserted as
today, which is never when the change happened; see `_git_status_date`.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..models import Event, Ref, slugify
from .base import Adapter

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
FIELD_RES = {
    "status": re.compile(r"^(?:[-*]\s*)?status\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "date": re.compile(r"^(?:[-*]\s*)?date\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "supersedes": re.compile(r"^(?:[-*]\s*)?supersedes\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
STATUS_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?status\s*[:–]", re.IGNORECASE)
ADR_REF_RE = re.compile(r"\badr-[a-z0-9][a-z0-9-]*[a-z0-9]\b", re.IGNORECASE)

PRIMARY_DOCS = {"readme.md", "index.md"}
SIBLING_STEMS = ("kep", "metadata", "adr", "meta")
SIBLING_EXTS = (".yaml", ".yml")
# placeholders these corpora use instead of leaving the field empty
JUNK_REFS = {"n/a", "na", "none", "nil", "tbd", "todo", "unknown", "-", "?"}
STATUS_MAP = {
    "provisional": "active", "implementable": "active", "implemented": "active",
    "active": "active", "accepted": "active", "approved": "active",
    "superseded": "superseded", "replaced": "superseded", "deprecated": "superseded",
    "withdrawn": "dropped", "rejected": "dropped", "deferred": "dropped",
}


class _DateSafeLoader(yaml.SafeLoader):
    """SafeLoader that keeps dates as strings.

    A typo'd `creation-date: 2024-19-31` otherwise raises and costs us the
    whole file, and we want the raw string anyway."""


_DateSafeLoader.yaml_implicit_resolvers = {
    ch: [(tag, rex) for tag, rex in rs if tag != "tag:yaml.org,2002:timestamp"]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class AdrAdapter(Adapter):
    name = "adr"

    def collect(self) -> list[Event]:
        base = (self.root / self.source.path).resolve()
        seen: dict = self.state.setdefault("seen", {})
        events: list[Event] = []
        if not base.is_dir():
            return events
        for path in self.source_files(base):
            sib = _sibling_path(path)
            if path.name.lower() == "readme.md" and sib is None:
                continue  # a bare README is a directory index, not a decision
            rel = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
            sib_bytes = sib.read_bytes() if sib is not None else b""
            digest = hashlib.sha1(path.read_bytes() + sib_bytes).hexdigest()[:12]
            if seen.get(rel) == digest:  # content cursor: moves/re-checkouts don't refire
                continue
            evs = self._parse(path, rel, sib)
            if evs:
                events.extend(evs)
                seen[rel] = digest
        return events

    def _parse(self, path, rel: str, sib: Path | None = None) -> list[Event]:
        text = path.read_text(errors="replace")
        sib_text = sib.read_text(errors="replace") if sib is not None else ""
        title, meta = _extract_meta(text)
        sib_meta = _sibling_meta(sib_text) if sib is not None else {}
        # Precedence: the markdown is specific to this document, so it wins on
        # conflict and the sibling only fills gaps. Exception: title and status
        # come from the sibling whenever it has them — in sibling-metadata
        # corpora (KEPs) that file is the curated one and the prose carries
        # neither.
        meta = {**sib_meta, **{k: v for k, v in meta.items() if v not in (None, "")}}
        for key in ("title", "status"):
            if sib_meta.get(key):
                meta[key] = sib_meta[key]
        title = str(meta.get("title") or title).strip()
        if not title:
            return []
        # README/index is the directory's document: its id comes from the
        # directory, which is also what inbound references point at.
        stem = path.parent.name if path.name.lower() in PRIMARY_DOCS else path.stem
        adr_id = _adr_id(stem, title)
        notes: list[str] = []
        raw_date = str(meta.get("date") or "").strip()
        date = raw_date if _is_calendar_date(raw_date) else ""
        if raw_date and not date:
            # upstream typos exist (`creation-date: 2023-14-05`); one bad file
            # must never abort the corpus, so fall back and say so
            notes.append(f"declared date {raw_date} is not a calendar date; dated from file mtime")
        date = date or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        raw_status = str(meta.get("status") or "accepted").strip()
        status = _map_status(raw_status)

        supersedes = [s.lower() for s in ADR_REF_RE.findall(str(meta.get("supersedes") or ""))]
        for rid in _ref_ids(meta.get("replaces"), adr_id):
            if rid not in supersedes:
                supersedes.append(rid)
        superseded_by = _ref_ids(meta.get("superseded-by"), adr_id)
        if superseded_by:
            status = "superseded"
        see_also = _ref_ids(meta.get("see-also"), adr_id)
        # Mutable state lives in the tracker, not in the dated record: split it
        # off so replaying to a past date reports "not recorded" instead of
        # back-dating today's value.
        mutable = bool(sib_meta.get("status") or superseded_by)

        summary = _first_paragraph(text)
        # event id is content-derived, not date-derived: moving/re-checking-out
        # the file (new mtime) must not mint a duplicate event; editing the
        # content legitimately does
        content_hash = hashlib.sha1((text + sib_text).encode()).hexdigest()[:8]
        # Trust hierarchy: real ADRs are decisions (high trust); other docs
        # are claims about the code and rot silently — low trust, verify in
        # code before relying on them.
        is_real_adr = any(p in ("adr", "adrs", "decisions") for p in Path(rel).parts) \
            or _adr_id(stem, title).startswith("adr-") and re.match(r"^(adr|\d{3,5})-", stem)
        entity = {
            "id": adr_id, "type": "adr", "name": title,
            "description": summary or title,
        }
        if mutable:
            # A pointer, never a claim of absence. This note used to read
            # "status on <date> not recorded upstream", which is true of the
            # FILE and false of the REPO: git has every prior value. Measured on
            # the reference corpus, an arm ran `git log --before` and `git show`,
            # saw the historical value on its screen, and then wrote "not
            # recorded" anyway because this key fact told it there was nothing to
            # find. An assertion of absence outranks evidence the reader
            # gathered themselves, so it must not be made unless it is true.
            notes.append(f"status on {date} is not carried in the source file, "
                         "which holds only its current value; earlier values are "
                         "in the file's git history")
        else:
            entity["status"] = status
        if notes:
            entity["profile"] = {"key_facts": notes}
        relations = [{"source": adr_id, "predicate": "documented_in", "target": rel}]
        relations += [{"source": adr_id, "predicate": "supersedes", "target": t} for t in supersedes]
        relations += [{"source": t, "predicate": "supersedes", "target": adr_id} for t in superseded_by]
        relations += [{"source": adr_id, "predicate": "relates_to", "target": t} for t in see_also]
        event = Event(
            id=f"evt-adr-{slugify(title)}-{content_hash}",
            type="adr",
            ts=date,
            title=f"ADR: {title}",
            summary=summary,
            impact="medium",
            domains=_as_list(meta.get("domain")),
            actors=[a.lstrip("@") for a in _as_list(meta.get("authors")) + _as_list(meta.get("editor"))],
            trust="high" if is_real_adr else "low",
            source=self.name,
            refs=[Ref(kind="doc", id=rel), Ref(kind="adr", id=adr_id)],
            supersedes=supersedes,
            derived_entities=[entity],
            derived_relations=relations,
        )
        if not mutable:
            return [event]
        # Without `last-updated` the only dated evidence left is the commit that
        # wrote the status line into the file we just parsed.
        git_date, git_sha = "", ""
        if not _is_calendar_date(str(meta.get("last-updated") or "")):
            tracker, tracker_text = (sib, sib_text) if sib is not None else (path, text)
            git_date, git_sha = _git_status_date(tracker, _status_line(tracker_text, raw_status))
        return [event, _status_event(adr_id, title, date, raw_status, status,
                                     superseded_by, meta, sib, content_hash,
                                     event.trust, self.name, git_date, git_sha)]


def _status_event(adr_id: str, title: str, date: str, raw_status: str, status: str,
                  superseded_by: list[str], meta: dict, sib: Path | None,
                  content_hash: str, trust: str, source: str,
                  git_date: str = "", git_sha: str = "") -> Event:
    """The status observation, dated when it was observed rather than when the
    decision was taken. Precedence: `last-updated`, the upstream's own claim;
    else the commit that wrote the status line, cited so the date is checkable;
    else the ingest date, which is the only date we can then honestly assert."""
    updated = str(meta.get("last-updated") or "")
    if _is_calendar_date(updated):
        observed = updated
        origin = f"{sib.name}:last-updated" if sib is not None else "last-updated"
    elif git_date:
        observed, origin = git_date, f"commit {git_sha}"
    else:
        observed, origin = _today(), "as ingested; source has no dated history"
    if observed < date:
        # A status can't be observed before the decision it belongs to. The
        # rejected date must stop being cited along with it.
        observed, git_sha, origin = date, "", f"dated from creation; {origin} is earlier"
    label = raw_status or "superseded"
    fact = f"status={label}" + (f" ({sib.stem})" if sib is not None else "") + \
        f" as of {observed} ({origin})"
    entity = {"id": adr_id, "type": "adr", "name": title, "status": status,
              "profile": {"key_facts": [fact]}}
    if superseded_by:
        entity["superseded_by"] = superseded_by[0]
    return Event(
        id=f"evt-adr-status-{slugify(title)}-{content_hash}",
        type="adr", ts=observed,
        title=f"ADR status as of {observed}: {title} = {label}",
        summary=f"Observed status of {adr_id}. The source records only its current "
                "value, so no status is asserted for any earlier date.",
        impact="low", trust=trust, source=source,
        refs=[Ref(kind="adr", id=adr_id)] + ([Ref(kind="commit", id=git_sha)] if git_sha else []),
        derived_entities=[entity],
    )


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _status_line(text: str, raw_status: str) -> str:
    """The verbatim line declaring the status, for use as a pickaxe needle. The
    whole line, because the value alone (`implemented`) also matches prose."""
    for line in text.splitlines():
        if raw_status and raw_status in line and STATUS_LINE_RE.match(line):
            return line.strip()
    return ""


def _git_status_date(path: Path, needle: str) -> tuple[str, str]:
    """(date, sha) of the commit that last changed `needle` in `path`.

    The tracker's own status line is dated by the commit that wrote it, which is
    the date the status actually changed. Returns ("", "") whenever git cannot
    answer: not a repository, no matching commit, or a blobless clone where the
    pickaxe has no content to search. Never raises, never prompts, never fetches
    (a lazy fetch here would turn one ingest into a clone)."""
    if not needle:
        return "", ""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}
    try:
        out = subprocess.run(
            ["git", "-C", str(path.parent), "log", "-1", "--date=short",
             "--format=%H%x1f%ad", f"-S{needle}", "--", path.name],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if out.returncode != 0:
        return "", ""
    sha, _, date = out.stdout.strip().partition("\x1f")
    return (date, sha[:12]) if _is_calendar_date(date) else ("", "")


def _is_calendar_date(value: str) -> bool:
    """`\\d{4}-\\d{2}-\\d{2}` is not enough: real corpora carry `2023-14-05`."""
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _extract_meta(text: str) -> tuple[str, dict]:
    meta: dict = {}
    fm = FRONTMATTER_RE.match(text)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip("\"'")
        text = text[fm.end():]
    for key, rex in FIELD_RES.items():
        if key not in meta:
            m = rex.search(text)
            if m:
                meta[key] = m.group(1).strip()
    if "date" in meta:
        m = DATE_RE.search(meta["date"])
        meta["date"] = m.group(0) if m else ""
    heading = HEADING_RE.search(text)
    title = meta.get("title") or (heading.group(1).strip() if heading else "")
    title = re.sub(r"^(?:ADR[-\s]?\d*\s*[:.\-]\s*)", "", title, flags=re.IGNORECASE)
    return title, meta


def _sibling_path(path: Path) -> Path | None:
    """The metadata file belonging to `path`: same stem first, then a generic
    directory-level one — but a generic sibling only belongs to the directory's
    primary document, never to five unrelated notes in the same folder."""
    for ext in SIBLING_EXTS:
        cand = path.with_suffix(ext)
        if cand.is_file():
            return cand
    if path.name.lower() not in PRIMARY_DOCS and \
            len([p for p in path.parent.glob("*.md") if p.is_file()]) != 1:
        return None
    for stem in SIBLING_STEMS:
        for ext in SIBLING_EXTS:
            cand = path.parent / f"{stem}{ext}"
            if cand.is_file():
                return cand
    return None


def _sibling_meta(text: str) -> dict:
    """Sibling YAML → this adapter's field names. A malformed file yields
    nothing and the ingest carries on with the markdown alone."""
    try:
        data = yaml.load(text, _DateSafeLoader)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    meta: dict = {}
    for src, dst in (("title", "title"), ("status", "status"), ("owning-sig", "domain"),
                     ("replaces", "replaces"), ("superseded-by", "superseded-by"),
                     ("see-also", "see-also"), ("authors", "authors"), ("editor", "editor")):
        if data.get(src) not in (None, "", [], {}):
            meta[dst] = data[src]
    for key in ("creation-date", "date"):
        m = DATE_RE.search(str(data.get(key) or ""))
        if m:
            meta["date"] = m.group(0)
            break
    m = DATE_RE.search(str(data.get("last-updated") or ""))
    if m:
        meta["last-updated"] = m.group(0)
    return meta


def _as_list(value) -> list[str]:
    """Sibling values come as scalar, list, or empty/null."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(v).strip() for v in items if v is not None and str(v).strip()]


def _ref_ids(value, self_id: str = "") -> list[str]:
    out: list[str] = []
    for raw in _as_list(value):
        rid = _ref_adr_id(raw)
        if rid and rid != self_id and rid not in out:
            out.append(rid)
    return out


def _ref_adr_id(ref: str) -> str:
    """Normalize an upstream reference (`/keps/sig-node/2221-x`, `0001-x.md`,
    a URL, a bare number) to this repo's adr id. Returns "" when it cannot be
    resolved to a sane id — a dangling guess is worse than a missing edge."""
    ref = ref.strip().strip("\"'()<>").rstrip("/")
    if not ref or ref.lower() in JUNK_REFS:
        return ""
    if "://" in ref:
        m = re.search(r"/(?:keps|adr|adrs|decisions)/(.+)$", ref)
        if not m:  # a PR/design-doc link, not a decision id
            return ""
        ref = m.group(1)
    parts = [p for p in ref.split("/") if p not in ("", ".", "..")]
    while parts and parts[-1].lower() in ("readme.md", "index.md", "readme", "index"):
        parts.pop()
    if not parts:
        return ""
    stem = re.sub(r"\.(md|markdown|ya?ml)$", "", parts[-1], flags=re.IGNORECASE)
    if re.fullmatch(r"(?:kep[-\s_]?)?\d+", stem, flags=re.IGNORECASE):
        return ""  # a bare number carries no slug; resolving it would be a guess
    return _stem_adr_id(stem) or (f"adr-{slugify(stem)}" if slugify(stem) else "")


def _map_status(raw: str) -> str:
    return STATUS_MAP.get(raw.strip().lower(), "active")


def _stem_adr_id(stem: str) -> str:
    """adr-<slug> from a filename/directory stem, or "" if it carries none."""
    slug = slugify(stem)
    if slug.startswith("adr-"):
        return slug
    m = re.match(r"^(\d{3,5})-(.+)$", slug)
    return f"adr-{m.group(2)}" if m else ""


def _adr_id(stem: str, title: str) -> str:
    return _stem_adr_id(stem) or f"adr-{slugify(title)}"


def _first_paragraph(text: str) -> str:
    text = FRONTMATTER_RE.sub("", text)
    for block in text.split("\n\n"):
        block = block.strip()
        if block and not block.startswith("#") and len(block) > 40:
            return re.sub(r"\s+", " ", block)[:300]
    return ""
