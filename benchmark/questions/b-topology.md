# B — Repository topology

Tests whether the captain knows what each of the four ingested repos *is* and how they relate to one another, rather than treating the workspace as one undifferentiated pile of Markdown.
A passing answer names the right repo for each role and grounds the relationship in a cited line from the corpus; a plausible relationship asserted without a citation, or a repo mis-identified, fails.

---

### B1 — what are the four repos

> what are the four repositories in this workspace, and what is each one?

**Gold.** Four repos, each self-described in its own README:
- `cloudnative-pg` — "**CloudNativePG (CNPG)** is an open-source platform designed to seamlessly manage [PostgreSQL] databases in Kubernetes environments. It covers the entire operational lifecycle—from deployment to ongoing maintenance—through its core component, the CloudNativePG operator." (`cloudnative-pg/README.md:15`)
- `etcd` — "etcd is a distributed reliable key-value store for the most critical data of a distributed system" (`etcd/README.md:21`), which "is written in Go and uses the [Raft][] consensus algorithm to manage a highly-available replicated log" (`etcd/README.md:28`).
- `kubernetes-enhancements` — "Enhancement tracking repo for Kubernetes releases. Owned by [SIG Architecture]." (`kubernetes-enhancements/README.md:18`)
- `postgres` — "This directory contains the source code distribution of the PostgreSQL database management system." (`postgres/README.md:4`)

**Required.**
- The four repositories are `cloudnative-pg`, `etcd`, `kubernetes-enhancements` and `postgres`.
  forms: the four directory names in any order; the four project names (CloudNativePG/CNPG, etcd, the Kubernetes enhancements/KEP repo, PostgreSQL). All four must appear. Not counted: three of four; naming a fifth repository that is not in the workspace.
  sources: `cloudnative-pg/README.md:15`; `etcd/README.md:21`; `kubernetes-enhancements/README.md:18`; `postgres/README.md:4`.
- Each is identified correctly: cloudnative-pg is a Kubernetes operator/platform for running PostgreSQL; etcd is a distributed key-value store built on Raft; kubernetes-enhancements is the enhancement-tracking (KEP) repo owned by SIG Architecture; postgres is the PostgreSQL source distribution.
  forms: any wording that gets each of the four roles right; quoting the READMEs verbatim; paraphrasing. Line-accurate citation is not required. Not counted: a role swapped between two repos; omitting the role of one repo.
  sources: the same four README lines above; `etcd/README.md:28` (etcd "is written in Go and uses the [Raft][] consensus algorithm to manage a highly-available replicated log").

**Forbidden.**
- Describing `kubernetes-enhancements` as the Kubernetes source tree, a fork of `kubernetes/kubernetes`, or the Kubernetes website/documentation repo.
- Describing `postgres` as a CloudNativePG fork or vendored copy rather than the upstream PostgreSQL source distribution.
- Describing `etcd` as a component of CloudNativePG or of PostgreSQL.

**Locator.** `cloudnative-pg/README.md:15`, `etcd/README.md:21`, `kubernetes-enhancements/README.md:18`, `postgres/README.md:4`

**Discriminates.** Baseline orientation. A captain that ingested the corpus can quote each README; one that did not will still get roughly the right answer from pretraining, so this question is cheap to pass and only fails on a badly broken ingest.

---

### B2 — CloudNativePG's relationship to PostgreSQL

> how does cloudnative-pg relate to the postgres repo in this workspace — does it reimplement replication or reuse it?

**Gold.** It reuses PostgreSQL's own replication; it does not reimplement it. `cloudnative-pg/docs/src/architecture.md:48-52`: "CloudNativePG relies on application-level replication, for a simple reason: the PostgreSQL database management system comes with robust and reliable built-in **physical replication** capabilities based on **Write Ahead Log (WAL) shipping**, which have been used in production by millions of users all over the world for over a decade." The operator ships PostgreSQL as a container image — `cloudnative-pg/docs/src/index.md:102`: "By default, this version of CloudNativePG deploys `ghcr.io/cloudnative-pg/postgresql:18.4-system-trixie`."

**Required.**
- CloudNativePG reuses PostgreSQL's own built-in physical replication; it does not reimplement replication.
  forms: "reuses"; "relies on PostgreSQL's native streaming replication / WAL shipping"; "application-level replication, meaning Postgres' own physical replication". Not counted: "it integrates with PostgreSQL" without saying whether replication is reimplemented; a bare "reuses" with no mechanism named.
  sources: `cloudnative-pg/docs/src/architecture.md:48-52` ("CloudNativePG relies on application-level replication, for a simple reason: the PostgreSQL database management system comes with robust and reliable built-in **physical replication** capabilities based on **Write Ahead Log (WAL) shipping**").
- The dependency direction is operator to engine: cnpg deploys upstream PostgreSQL as a container image rather than containing an engine of its own.
  forms: "the operator ships/deploys a PostgreSQL container image"; naming the default image tag; "cnpg sits on top of the same PostgreSQL that the `postgres` repo is the source of". A specific image tag is not required. Not counted: reversing the direction, i.e. PostgreSQL depending on cnpg.
  sources: `cloudnative-pg/docs/src/index.md:102` ("By default, this version of CloudNativePG deploys `ghcr.io/cloudnative-pg/postgresql:18.4-system-trixie`").

**Forbidden.**
- Stating that CloudNativePG implements its own replication protocol or its own storage-level / block-level / volume-level replication. `architecture.md:45-46` lists storage-level replication only as the alternative that was not chosen.
- Stating that the `postgres` directory in this workspace is a CloudNativePG-maintained fork or patch set.
- Stating that CloudNativePG patches or vendors PostgreSQL source in order to replicate.

**Locator.** `cloudnative-pg/docs/src/architecture.md:48`; `cloudnative-pg/docs/src/index.md:102`

**Discriminates.** Requires connecting two repos across a stated dependency direction (operator → upstream engine), and pinning it with the actual default image tag rather than a remembered major version.

**Trap.** Saying CloudNativePG implements its own storage-level replication. `architecture.md:44-45` lists *storage-level replication* only as the alternative it rejected.

---

### B3 — CloudNativePG's relationship to etcd

> cloudnative-pg and etcd are both in this workspace. Does CloudNativePG use etcd as its distributed configuration store?

**Gold.** No. CloudNativePG uses the Kubernetes API server as its single source of truth and deliberately avoids an external coordination tool. `cloudnative-pg/docs/src/faq.md:227-238`: "**What are the reasons behind CloudNativePG not relying on a failover management tool like Patroni, repmgr, or Stolon?** … we decided to take a different approach and directly extend the Kubernetes controller and rely on the Kubernetes API server to hold the status of a Postgres cluster, and use it as the only source of truth". `cloudnative-pg/docs/src/index.md:111` lists as a main feature: "Direct integration with the Kubernetes API server for High Availability, eliminating the need for external tools." No file under `cloudnative-pg/docs/src/*.md` names etcd as a dependency. etcd is in the workspace because it is the store *behind* the Kubernetes API server — `etcd/README.md:30` notes etcd "is frequently teamed with applications such as [Kubernetes]".

**Required.**
- No: CloudNativePG does not use etcd as its distributed configuration store. It uses the Kubernetes API server as the single source of truth.
  forms: "no, it uses the Kubernetes API server"; "no external DCS — Kubernetes-native leader election / API server state"; "it extends the Kubernetes controller instead". Both halves must be present: a bare "no" with no alternative named does not count, nor does naming the API server while leaving the etcd question open.
  sources: `cloudnative-pg/docs/src/faq.md:227-234`; `cloudnative-pg/docs/src/index.md:111-112` ("Direct integration with the Kubernetes API server for High Availability, eliminating the need for external tools").
- etcd's relation to CloudNativePG here is indirect: etcd is the store behind the Kubernetes API server, not a dependency cnpg declares or talks to.
  forms: "etcd sits underneath Kubernetes, so the relationship is transitive"; "no direct edge — etcd is in the workspace in its own right / as the Kubernetes backing store"; "cnpg talks to the API server, and what the API server persists into is not cnpg's concern". Not counted: asserting a direct cnpg-to-etcd edge, or leaving the presence of etcd unexplained while claiming no relationship at all exists.
  sources: `etcd/README.md:30` (etcd "is frequently teamed with applications such as [Kubernetes]"); the absence of any etcd dependency in `cloudnative-pg/docs/src/faq.md:227-239` and `cloudnative-pg/docs/src/index.md:109-112`.

**Forbidden.**
- Stating that CloudNativePG uses etcd (or Consul, or ZooKeeper) for leader election, failover coordination, or configuration storage.
- Citing any cloudnative-pg document as evidence that etcd is a cnpg dependency.
- Describing cnpg's HA as Patroni-shaped — an external DCS holding cluster state — which is what the cited FAQ entry rejects.

**Locator.** `cloudnative-pg/docs/src/faq.md:227`; `cloudnative-pg/docs/src/index.md:111`; `etcd/README.md:30`

**Discriminates.** Two co-located repos with no direct edge between them. A model pattern-matching "distributed Postgres HA + etcd in the same folder" will invent a dependency; the corpus says the opposite.

**Trap.** Answering "yes, for leader election" — a correct description of Patroni, not of CloudNativePG.

---

### B4 — what kind of repo is kubernetes-enhancements

> what kind of artefact does kubernetes-enhancements actually contain, and where does the etcd/Kubernetes coupling show up in this workspace?

**Gold.** `kubernetes-enhancements` is a decision-record repo, not a product repo: "Enhancement tracking repo for Kubernetes releases" (`README.md:18`), holding KEPs — "A Kubernetes Enhancement Proposal (KEP) is a way to propose, communicate and coordinate on new efforts for the Kubernetes project" (`keps/README.md:3`). Each proposal is a directory under `keps/<sig>/` with a `README.md` narrative plus a machine-readable `kep.yaml`; there are 656 `kep.yaml` files at the pinned commit. The etcd↔Kubernetes coupling is documented on the etcd side, in `etcd/Documentation/contributor-guide/bump_etcd_version_k8s.md` — "This guide will walk through the update of etcd in Kubernetes to a new version (`kubernetes/kubernetes` repository)", noting "Currently we bump etcd v3.5.x for K8s release-1.33 and lower versions, and we bump etcd v3.6.x for K8s release-1.34 and higher versions."

**Required.**
- `kubernetes-enhancements` holds proposals / decision records (KEPs), not product source code: one directory per KEP under `keps/<sig>/`, each with a narrative `README.md` and a machine-readable `kep.yaml`.
  forms: "enhancement tracking repo"; "KEPs / Kubernetes Enhancement Proposals"; "design and decision records, not implementation"; describing the per-KEP `README.md` + `kep.yaml` pair. The file count (656 `kep.yaml` at this pin) is not required. Not counted: "Kubernetes documentation" with no mention of proposals; calling it a code repository.
  sources: `kubernetes-enhancements/README.md:18` ("Enhancement tracking repo for Kubernetes releases. Owned by [SIG Architecture]"); `kubernetes-enhancements/keps/README.md:3` ("A Kubernetes Enhancement Proposal (KEP) is a way to propose, communicate and coordinate on new efforts for the Kubernetes project"); any KEP directory, e.g. `kubernetes-enhancements/keps/sig-node/2221-remove-dockershim/kep.yaml`.
- The etcd/Kubernetes coupling is documented on the **etcd** side, in `etcd/Documentation/contributor-guide/bump_etcd_version_k8s.md`.
  forms: naming that file; "etcd's contributor guide carries the procedure for bumping etcd's version in `kubernetes/kubernetes`"; quoting the v3.5.x / v3.6.x to Kubernetes release mapping. Naming the file is the load-bearing part; quoting the mapping is optional corroboration. Not counted: asserting a coupling exists with no file in this corpus named.
  sources: `etcd/Documentation/contributor-guide/bump_etcd_version_k8s.md:1` ("# Bump etcd Version in Kubernetes"); same file :3 ("This guide will walk through the update of etcd in Kubernetes to a new version (`kubernetes/kubernetes` repository)"); same file :5 ("Currently we bump etcd v3.5.x for K8s release-1.33 and lower versions, and we bump etcd v3.6.x for K8s release-1.34 and higher versions"); graph entity `corpus-etcd-documentation-contributor-guide`.

**Forbidden.**
- Claiming a KEP in `kubernetes-enhancements` records the etcd-version-to-Kubernetes-release mapping. No KEP at this pin does.
- Citing `etcd/README.md` as the location of the version-bump coupling.
- Describing `kubernetes-enhancements` as containing the implementation of the enhancements it describes, or as the place a feature's code lands.

**Locator.** `kubernetes-enhancements/README.md:18`; `kubernetes-enhancements/keps/README.md:3`; `etcd/Documentation/contributor-guide/bump_etcd_version_k8s.md:1-5`

**Discriminates.** The cross-repo edge lives in an obscure contributor guide, not in either README. Only an ingest that walked `etcd/Documentation/` will find it.

**Trap.** Looking for the coupling in `kubernetes-enhancements` (there is no etcd-version KEP that states the release mapping) or in `etcd/README.md`.
