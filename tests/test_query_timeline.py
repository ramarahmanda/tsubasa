"""`tsubasa query --timeline`: sequence, not snapshot.

Two measured benchmark failures are the specification here. "Has this been
tried before?" needs `added -> reverted`, a two-point sequence the snapshot
cannot express: it says "added". "What is the status of X" where X was
superseded needs the two facts ordered, not both present. So: the resulting
state leads, the evidence runs forward, and every row keeps its citation.

Graphs are synthetic because the reference corpus predates the `reverts`
predicate; shapes mirror what gitlog.py and adr.py actually emit.
"""

from tsubasa import cli
from tsubasa.graph import query as query_mod
from tsubasa.models import Event, Ref
from tsubasa.storage import Store

ADD_SHA = "b860848232"
REVERT_SHA = "5721e5453e"
REASON = "breaks pg_upgrade control file"


def store_with(tmp_path, *events) -> Store:
    store = Store(tmp_path)
    for ev in events:
        store.append_event(ev)
    return store


def added_event() -> Event:
    """What a commit that introduces a feature leaves behind."""
    return Event(
        id="evt-20240802-postgres-redo-lsn", type="pr_merged", ts="2024-08-02",
        title="postgres: add redo LSN in pgstats files", impact="medium", source="git",
        refs=[Ref(kind="commit", id=ADD_SHA)],
        derived_entities=[{"id": "feat-redo-lsn-pgstats", "type": "feature",
                          "name": "redo LSN in pgstats files"}],
        derived_relations=[{"source": "svc-postgres", "predicate": "changed_by", "target": ADD_SHA}],
    )


def revert_event() -> Event:
    """gitlog._reverts: one decision event, git's stated reason, a `reverts` edge."""
    return Event(
        id="evt-20250317-postgres-revert-5721e545", type="decision", ts="2025-03-17",
        title="postgres: reverted add redo LSN in pgstats files",
        summary=f"Reverts {ADD_SHA}. {REASON}", impact="medium", source="git",
        refs=[Ref(kind="commit", id=REVERT_SHA), Ref(kind="commit", id=ADD_SHA)],
        derived_relations=[{"source": "svc-postgres", "predicate": "changed_by", "target": REVERT_SHA},
                           {"source": REVERT_SHA, "predicate": "reverts", "target": ADD_SHA}],
    )


def adr_event(adr_id: str, title: str, ts: str, supersedes: list[str] | None = None) -> Event:
    supersedes = supersedes or []
    return Event(
        id=f"evt-adr-{adr_id}", type="adr", ts=ts, title=f"ADR: {title}",
        impact="medium", trust="high", source="adr",
        refs=[Ref(kind="adr", id=adr_id)],
        supersedes=supersedes,
        derived_entities=[{"id": adr_id, "type": "adr", "name": title}],
        derived_relations=[{"source": adr_id, "predicate": "supersedes", "target": t}
                           for t in supersedes],
    )


def status_event(adr_id: str, title: str, ts: str, label: str, status: str) -> Event:
    """adr._status_event: the dated observation, split off so it is visible."""
    return Event(
        id=f"evt-adr-status-{adr_id}-{ts}", type="adr", ts=ts,
        title=f"ADR status as of {ts}: {title} = {label}",
        source="adr", refs=[Ref(kind="adr", id=adr_id)],
        derived_entities=[{"id": adr_id, "type": "adr", "name": title, "status": status,
                           "profile": {"key_facts": [f"status={label} (kep) as of {ts}"]}}],
    )


def rows(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if ln.startswith("  ")]


# ------------------------------------------------------------------ add -> revert

def test_revert_lands_next_to_what_it_undid_with_the_reason(tmp_path):
    store = store_with(tmp_path, added_event(), revert_event())
    out = query_mod.timeline(store, "redo LSN in pgstats")

    body = rows(out)
    assert len(body) == 2
    assert body[0].startswith("2024-08-02  added")
    assert body[1].startswith("2025-03-17  REVERTED")
    assert REASON in body[1]                      # git's own words, not a summary of them
    assert body[0].endswith(ADD_SHA)              # citation per row
    assert body[1].endswith(REVERT_SHA)


def test_the_verdict_leads_and_says_not_present(tmp_path):
    store = store_with(tmp_path, added_event(), revert_event())
    out = query_mod.timeline(store, "redo LSN in pgstats")

    verdict = out.splitlines()[1]
    assert "NOT PRESENT" in verdict and "2025-03-17" in verdict
    assert REVERT_SHA in verdict
    # a reader who stops at the verdict must not be told the feature exists
    assert verdict.index("NOT PRESENT") < out.index("added")


def test_a_revert_of_something_else_is_not_pulled_in(tmp_path):
    other = revert_event()
    other.id = "evt-20250401-postgres-revert-deadbeef00"
    other.ts = "2025-04-01"
    other.title = "postgres: reverted an unrelated change"
    other.refs = [Ref(kind="commit", id="deadbeef00"), Ref(kind="commit", id="cafe12345678")]
    other.derived_relations = [{"source": "deadbeef00", "predicate": "reverts",
                               "target": "cafe12345678"}]
    store = store_with(tmp_path, added_event(), revert_event(), other)

    out = query_mod.timeline(store, "redo LSN in pgstats")
    assert "deadbeef00" not in out
    assert len(rows(out)) == 2


def test_as_of_before_the_revert_stops_at_the_add(tmp_path):
    store = store_with(tmp_path, added_event(), revert_event())
    out = query_mod.timeline(store, "redo LSN in pgstats", as_of="2024-12-31")

    assert REVERT_SHA not in out
    assert len(rows(out)) == 1
    assert "NOT PRESENT" not in out           # not yet true on that date
    assert "as of 2024-12-31" in out


# ------------------------------------------------------------------ supersession

def test_superseded_adr_is_ordered_not_just_present(tmp_path):
    store = store_with(
        tmp_path,
        adr_event("adr-coscheduling", "Coscheduling", "2018-07-03"),
        status_event("adr-coscheduling", "Coscheduling", "2019-01-03", "provisional", "active"),
        adr_event("adr-gang-scheduling", "Gang Scheduling", "2025-09-17",
                  supersedes=["adr-coscheduling"]),
    )
    out = query_mod.timeline(store, "coscheduling")

    verdict = out.splitlines()[1]
    assert verdict.startswith("adr-coscheduling: SUPERSEDED by adr-gang-scheduling (2025-09-17)")
    assert "evt-adr-adr-gang-scheduling" in verdict
    body = rows(out)
    assert [r.split()[0] for r in body] == ["2018-07-03", "2019-01-03", "2025-09-17"]
    assert "SUPERSEDED" in body[2] and "adr-gang-scheduling" in body[2]


def test_the_dated_status_event_is_marked_as_a_status_row(tmp_path):
    store = store_with(
        tmp_path,
        adr_event("adr-coscheduling", "Coscheduling", "2018-07-03"),
        status_event("adr-coscheduling", "Coscheduling", "2019-01-03", "provisional", "active"),
    )
    body = rows(query_mod.timeline(store, "coscheduling"))
    assert "added" in body[0]
    assert "status" in body[1] and "provisional" in body[1]


def test_a_real_status_transition_shows_both_ends(tmp_path):
    store = store_with(
        tmp_path,
        adr_event("adr-kustomize", "Kustomize", "2018-05-05"),
        status_event("adr-kustomize", "Kustomize", "2021-09-07", "withdrawn", "dropped"),
    )
    out = query_mod.timeline(store, "kustomize")
    assert "active -> dropped" in out
    assert out.splitlines()[1].startswith("adr-kustomize: DROPPED")


def test_the_verdict_cites_the_supersession_it_names(tmp_path):
    # two later decisions supersede the same ADR; the entity keeps the first, so
    # citing the most recent event would attribute the verdict to the wrong one
    store = store_with(
        tmp_path,
        adr_event("adr-coscheduling", "Coscheduling", "2018-07-03"),
        adr_event("adr-gang-scheduling", "Gang Scheduling", "2025-09-17",
                  supersedes=["adr-coscheduling"]),
        adr_event("adr-decouple-podgroup-api", "Decouple PodGroup API", "2026-01-23",
                  supersedes=["adr-coscheduling"]),
    )
    verdict = query_mod.timeline(store, "coscheduling").splitlines()[1]
    assert "by adr-gang-scheduling (2025-09-17)" in verdict
    assert verdict.endswith("[evt-adr-adr-gang-scheduling]")


def test_being_superseded_outranks_superseding_something_else(tmp_path):
    # adr.py puts both edges on one event: this ADR replaced an older one and its
    # own file already records a successor. The asked-about topic owns the row.
    ev = adr_event("adr-kustomize", "Kustomize", "2018-05-05", supersedes=["adr-kinflate"])
    ev.derived_relations.append({"source": "adr-kustomize-subcommand", "predicate": "supersedes",
                                 "target": "adr-kustomize"})
    body = rows(query_mod.timeline(store_with(tmp_path, ev), "kustomize"))
    assert body[0].startswith("2018-05-05  SUPERSEDED  by adr-kustomize-subcommand (at creation)")


def test_the_verdict_never_cites_another_entitys_event(tmp_path):
    # the last row of the sequence belongs to the successor, not to the topic
    store = store_with(
        tmp_path,
        adr_event("adr-coscheduling", "Coscheduling", "2018-07-03"),
        adr_event("adr-gang-scheduling", "Gang Scheduling", "2025-09-17",
                  supersedes=["adr-coscheduling"]),
        status_event("adr-gang-scheduling", "Gang Scheduling", "2026-07-29",
                     "implementable", "active"),
    )
    out = query_mod.timeline(store, "coscheduling")
    assert "evt-adr-status-adr-gang-scheduling" in rows(out)[-1]      # it is in the sequence
    assert "evt-adr-status-adr-gang-scheduling" not in out.splitlines()[1]  # not in the verdict


# ------------------------------------------------------------------ edges & shape

def test_one_event_says_so_instead_of_implying_a_sequence(tmp_path):
    store = store_with(tmp_path, adr_event("adr-lonely", "Lonely Decision", "2020-01-01"))
    out = query_mod.timeline(store, "lonely decision")

    assert "one recorded event for this topic, so there is no sequence" in out
    assert len(rows(out)) == 1
    assert "evt-adr-adr-lonely" in out  # the citation survives the special case


def test_a_repo_attached_revert_is_reached_even_when_entities_matched(tmp_path):
    """G3: a revert names the sha it undid but not the feature that sha belonged
    to, so the adapter derives only `svc-postgres` and no `feat-` entity can
    reach it. Five entities matching the topic must not hide it."""
    noise = [Event(id=f"evt-2024010{i}-feat-pgstats-{i}", type="decision", ts=f"2024-01-0{i}",
                   title=f"postgres: Cumulative statistics work {i}", source="distill",
                   refs=[Ref(kind="commit", id=f"aaaaaaaa000{i}")],
                   derived_entities=[{"id": f"feat-pgstats-{i}", "type": "feature",
                                      "name": f"pgstats redo LSN area {i}"}])
              for i in range(1, 6)]
    store = store_with(tmp_path, *noise, revert_event())

    out = query_mod.timeline(store, "redo LSN pgstats")
    assert any("REVERTED" in r for r in rows(out))
    assert REVERT_SHA in out
    # the topic is reported absent; no matched entity is falsely declared absent
    assert out.splitlines()[1].startswith("NOT PRESENT (reverted 2025-03-17)")
    assert not any("NOT PRESENT" in ln for ln in out.splitlines()[2:])


def test_a_chunk_digest_covering_a_reverted_commit_does_not_absent_its_entity(tmp_path):
    # the digest names dozens of commits and derives several entities; one revert
    # inside it makes none of them absent, and claiming so would be invention
    digest = Event(
        id="evt-20240823-postgres-pgstats-digest", type="decision", ts="2024-08-23",
        title="postgres: Cumulative statistics and observability", source="distill",
        refs=[Ref(kind="commit", id=ADD_SHA), Ref(kind="commit", id="ffff11112222"),
              Ref(kind="commit", id="eeee33334444")],
        derived_entities=[{"id": "feat-pgstats-pluggable", "type": "feature",
                           "name": "pluggable pgstats"}],
    )
    out = query_mod.timeline(store_with(tmp_path, digest, revert_event()), "pluggable pgstats")
    assert "feat-pgstats-pluggable: ACTIVE" in out
    assert "feat-pgstats-pluggable: NOT PRESENT" not in out


def test_the_revert_row_names_what_it_undid(tmp_path):
    # the reverted commit often has no event of its own, so the row is the only
    # place the subject appears
    rev = revert_event()
    rev.summary = f"Reverts {ADD_SHA}. This reverts commit {ADD_SHA}, that {REASON}"
    body = rows(query_mod.timeline(store_with(tmp_path, rev), "reverted redo LSN pgstats"))
    row = next(r for r in body if "REVERTED" in r)
    assert "add redo LSN in pgstats files" in row      # what
    assert "that " + REASON[:20] in row                # git's reason, boilerplate stripped
    assert "This reverts commit" not in row


def test_a_reverted_commit_that_never_became_an_entity_still_has_a_timeline(tmp_path):
    # the exact benchmark case: nothing distilled the commit into a feature, so
    # entity matching finds nothing and the commit subjects are the only handle
    add = added_event()
    add.derived_entities = []
    out = query_mod.timeline(store_with(tmp_path, add, revert_event()), "redo LSN in pgstats")

    assert out.splitlines()[1].startswith("NOT PRESENT (reverted 2025-03-17)")
    body = rows(out)
    assert body[0].startswith("2024-08-02  added") and body[0].endswith(ADD_SHA)
    assert body[1].startswith("2025-03-17  REVERTED")


def test_extra_query_words_do_not_unmatch_the_title(tmp_path):
    # benchmark G10: the query names the topic (skip, anti, wraparound) plus
    # wording of its own (autovacuum, freeze, aggressive). Strict AND failed
    # the whole match on the extra words; the discriminating standard must not.
    rev = Event(
        id="evt-20240730-postgres-revert-aaaa1111", type="decision", ts="2024-07-30",
        title="postgres: reverted Skip redundant anti-wraparound vacuums",
        summary="Reverts aaaa11112222. accidentally removed the freeze safety margin",
        source="git",
        refs=[Ref(kind="commit", id="bbbb33334444"), Ref(kind="commit", id="aaaa11112222")],
        derived_relations=[{"source": "bbbb33334444", "predicate": "reverts",
                            "target": "aaaa11112222"}],
    )
    out = query_mod.timeline(store_with(tmp_path, rev),
                             "autovacuum freeze aggressive anti-wraparound skip")
    assert out.splitlines()[1].startswith("NOT PRESENT (reverted 2024-07-30)")


def test_unrecorded_revert_reason_marked_on_the_verdict(tmp_path):
    # G11: retrieval delivered the revert, the record held no why, and a why
    # was invented anyway. The absence must arrive as data on the verdict line.
    rev = revert_event()
    rev.summary = f"Reverts {ADD_SHA}."  # sha boilerplate only: no rationale
    out = query_mod.timeline(store_with(tmp_path, added_event(), rev),
                             "redo LSN in pgstats")
    assert "reason: not recorded" in out.splitlines()[1]  # the headline verdict
    assert any("REVERTED" in r and "reason: not recorded" in r for r in rows(out))


def test_recorded_revert_reason_is_not_marked(tmp_path):
    # the canonical fixture reason ("breaks pg_upgrade control file") is short
    # but real; it must count as recorded
    out = query_mod.timeline(store_with(tmp_path, added_event(), revert_event()),
                             "redo LSN in pgstats")
    assert "reason: not recorded" not in out


def test_common_stem_overlap_alone_does_not_seed_the_timeline(tmp_path):
    # the strict-AND rule's legitimate worry, kept as the discriminating
    # requirement instead of the AND: common stems fire on nearly any
    # wording, so overlap on them alone is a different topic's timeline
    evs = [Event(id=f"evt-noise-{i}", type="note", ts=f"2026-06-{i + 1:02d}",
                 title=f"Fix buffer error return path {i}") for i in range(4)]
    out = query_mod.timeline(store_with(tmp_path, *evs), "buffer error handling")
    assert out == "(no knowledge found)"


def test_single_discriminating_token_seeds(tmp_path):
    ev = Event(id="evt-wal", type="note", ts="2026-07-01",
               title="Get rid of WALBufMappingLock")
    out = query_mod.timeline(store_with(tmp_path, ev), "walbufmappinglock")
    assert "Get rid of WALBufMappingLock" in out


def test_nothing_matched_is_stated_plainly(tmp_path):
    store = store_with(tmp_path, adr_event("adr-lonely", "Lonely Decision", "2020-01-01"))
    assert query_mod.timeline(store, "unrelated question about billing") == "(no knowledge found)"


def digest_event(ts: str, title: str, touches: str) -> Event:
    """A `study` chunk digest: one event naming many entities at once, which is
    why a focused topic accumulates dozens that only mention it."""
    return Event(
        id=f"evt-{ts.replace('-', '')}-digest-{touches}", type="decision", ts=ts,
        title=f"kubernetes-enhancements: {title}", source="distill",
        refs=[Ref(kind="entity", id=touches)],
        derived_relations=[{"source": touches, "predicate": "relates_to", "target": "adr-other"}],
    )


def test_rows_stay_ascending_when_long(tmp_path):
    evs = [adr_event("adr-hub", "Hub", "2010-01-01")]
    evs += [status_event("adr-hub", "Hub", f"20{y:02d}-06-01", "implementable", "active")
            for y in range(11, 70)]
    body = rows(query_mod.timeline(store_with(tmp_path, *evs), "hub"))

    dates = [r.split()[0] for r in body if not r.startswith("...")]
    assert dates == sorted(dates)
    assert dates[0] == "2010-01-01" and dates[-1] == "2069-06-01"


def test_a_transition_is_never_elided_however_much_context_there_is(tmp_path):
    # the bug this ranks for: recency-only truncation dropped the SUPERSEDED row
    # and kept thirty chunk digests, so the sequence lost the one thing it exists
    # to show
    evs = [adr_event("adr-declarative-validation", "Declarative Validation", "2023-08-20")]
    evs += [digest_event(f"2024-{m:02d}-05", f"Unrelated Hardening Wave {m}",
                         "adr-declarative-validation") for m in range(1, 13)]
    evs += [digest_event(f"2025-{m:02d}-05", f"Unrelated Graduation Wave {m}",
                         "adr-declarative-validation") for m in range(1, 13)]
    evs.append(status_event("adr-declarative-validation", "Declarative Validation",
                            "2026-02-13", "replaced", "superseded"))
    body = rows(query_mod.timeline(store_with(tmp_path, *evs), "declarative validation"))

    assert any("active -> superseded" in r for r in body)      # the transition survived
    assert any(r.startswith("2023-08-20  added") for r in body)
    assert sum(1 for r in body if "Unrelated" in r) == query_mod.MAX_CONTEXT
    note = next(r for r in body if r.startswith("..."))
    assert "context events omitted" in note and "no recorded state change" in note
    assert "20 earlier context events" in note                  # 24 digests, 4 kept


def test_context_is_dropped_oldest_first(tmp_path):
    evs = [adr_event("adr-hub", "Hub", "2010-01-01")]
    evs += [digest_event(f"20{y}-06-01", f"Wave {y}", "adr-hub") for y in range(11, 20)]
    body = rows(query_mod.timeline(store_with(tmp_path, *evs), "hub"))

    kept = [r.split()[0] for r in body if "Wave" in r]
    assert kept == ["2016-06-01", "2017-06-01", "2018-06-01", "2019-06-01"]
    assert body[1].startswith("...")   # the gap reads where it happened, not at the end


def test_caused_by_is_walked_into_the_sequence(tmp_path):
    incident = Event(
        id="evt-20240101-inc-outage", type="incident", ts="2024-01-01",
        title="Checkout outage", impact="high", source="manual",
        derived_entities=[{"id": "inc-checkout-outage", "type": "incident", "name": "Checkout outage"}],
    )
    cause = Event(
        id="evt-20240102-inc-cause", type="note", ts="2024-01-02",
        title="Root cause: connection pool exhausted", source="manual",
        derived_relations=[{"source": "inc-checkout-outage", "predicate": "caused_by",
                            "target": "svc-postgres"}],
    )
    out = query_mod.timeline(store_with(tmp_path, incident, cause), "checkout outage")
    assert "caused_by" in out and "svc-postgres" in out


# ------------------------------------------------------------------ cli

def test_the_flag_is_wired_and_composes_with_as_of(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm", "--no-detect", "--no-ingest"]) == 0
    for ev in (added_event(), revert_event()):
        Store(tmp_path).append_event(ev)

    assert cli.main(["query", "--timeline", "redo LSN in pgstats"]) == 0
    assert "NOT PRESENT" in capsys.readouterr().out

    assert cli.main(["query", "--timeline", "--as-of", "2024-12-31", "redo LSN in pgstats"]) == 0
    out = capsys.readouterr().out
    assert REVERT_SHA not in out and ADD_SHA in out
