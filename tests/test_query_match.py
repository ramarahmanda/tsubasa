"""Word-boundary entity matching in `tsubasa query`."""

from tsubasa.graph.query import match_entities
from tsubasa.models import Entity


def ents(*specs) -> dict[str, Entity]:
    out = {}
    for spec in specs:
        eid, name, aliases = spec if len(spec) == 3 else (*spec, [])
        out[eid] = Entity(id=eid, type="adr", name=name, aliases=list(aliases))
    return out


def ids(entities, text, limit=5):
    return [e.id for e in match_entities(entities, text, limit=limit)]


def test_short_alias_does_not_match_inside_a_word():
    g = ents(
        ("corpus-etcd-etcdctl-doc", "etcd/etcdctl/doc", ["doc"]),
        ("adr-remove-dockershim", "Removing dockershim from kubelet"),
    )
    assert ids(g, "what is the status of the dockershim removal KEP?") == [
        "adr-remove-dockershim"
    ]


def test_token_matches_across_id_segments():
    g = ents(
        ("adr-remove-dockershim", "Removing dockershim from kubelet"),
        ("svc-postgres", "postgres"),
    )
    assert ids(g, "dockershim") == ["adr-remove-dockershim"]
    assert ids(g, "how do we run postgres?") == ["svc-postgres"]


def test_boundaries_include_punctuation_and_slashes():
    g = ents(("corpus-etcd-etcdctl-doc", "etcd/etcdctl/doc"))
    assert ids(g, "what lives under etcd/etcdctl/doc?") == ["corpus-etcd-etcdctl-doc"]
    assert ids(g, "etcdctl") == ["corpus-etcd-etcdctl-doc"]
    assert ids(g, "docker") == []


def test_fuller_overlap_outranks_thinner():
    g = ents(
        ("adr-node-swap", "node swap support"),
        ("adr-node-only", "node"),
    )
    assert ids(g, "node swap support")[0] == "adr-node-swap"


def test_rare_token_outranks_a_corpus_wide_one():
    # "kep" reaches every entity here, "dockershim" reaches one
    g = ents(
        ("adr-remove-dockershim", "Removing dockershim from kubelet", ["kep 2221"]),
        *[(f"adr-filler-{i}", "KEP Template") for i in range(20)],
    )
    assert ids(g, "status of the dockershim KEP", limit=1) == ["adr-remove-dockershim"]


def test_no_match_returns_empty():
    g = ents(("adr-remove-dockershim", "Removing dockershim from kubelet"))
    assert ids(g, "unrelated question about billing") == []
