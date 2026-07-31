# A — Where does knowledge live

Tests whether the captain can point at the *file* that holds a piece of knowledge, using the source graph alone — no reasoning about status, time, or supersession required.
A passing answer names the correct path (relative to `tsubasa-benchmark/`) and cites text that actually appears in it; naming a plausible-but-wrong path, or answering from general knowledge of the upstream project without a citation, fails.

---

### A1 — etcd v3.5 data inconsistency postmortem

> where in this corpus is the etcd v3.5 data inconsistency incident written up?

**Gold.** `etcd/Documentation/postmortems/v3.5-data-inconsistency.md` — the only file under `etcd/Documentation/postmortems/`. Header table: `| Authors | serathius@ |`, `| Date | 2022-04-20 |`, `| Status | published |`. Section headings are `Summary`, `Background`, `Root cause`, `Trigger`, `Detection`, `Impact`, `Lessons learned`, `Action items`, `Timeline`.

**Required.**
- The write-up is `etcd/Documentation/postmortems/v3.5-data-inconsistency.md`, the only file under `etcd/Documentation/postmortems/`.
  forms: the full path; "the postmortem under `etcd/Documentation/postmortems/`"; the bare filename `v3.5-data-inconsistency.md`. Not counted: "in the etcd repo's documentation" with no file identified; recounting the incident from general knowledge with no path in this corpus given.
  sources: `etcd/Documentation/postmortems/v3.5-data-inconsistency.md:1` (title `# v3.5 data inconsistency postmortem`); graph entity `corpus-etcd-documentation-postmortems` (key facts `glob: etcd/Documentation/postmortems/**/*.md`, `files: 1`); graph entity `inc-20220420-v3-5-data-inconsistency-postmortem`.

**Forbidden.**
- Naming a file under `etcd/CHANGELOG/` or `etcd/tests/robustness` as where the incident is written up.
- Giving only an upstream URL (blog post, GitHub issue, mailing list) in place of a path in this corpus.
- Stating that this corpus contains no postmortem or no write-up of the incident.

**Locator.** `etcd/Documentation/postmortems/v3.5-data-inconsistency.md`

**Discriminates.** The document is the corpus's only postmortem; a captain that has ingested `Documentation/` finds it immediately. A model without the graph will describe the incident from memory and cannot give a path.

**Trap.** Pointing at `etcd/CHANGELOG/` or at an `etcd/tests/robustness` file. The incident narrative lives only in the postmortem.

---

### A2 — serializable snapshot isolation design

> which file documents PostgreSQL's serializable snapshot isolation and predicate locking design?

**Gold.** `postgres/src/backend/storage/lmgr/README-SSI`. Opens: "Serializable Snapshot Isolation (SSI) and Predicate Locking". It explains the placement: "This code is in the lmgr directory because about 90% of it is an implementation of predicate locking, which is required for SSI, rather than being directly related to SSI itself."

**Required.**
- The file is `postgres/src/backend/storage/lmgr/README-SSI`.
  forms: the full path; "`README-SSI`, under `src/backend/storage/lmgr`"; the bare filename `README-SSI` with the lmgr directory named. Not counted: naming the `lmgr` directory with no filename; naming `predicate.c` (the implementation) as the design document.
  sources: `postgres/src/backend/storage/lmgr/README-SSI:1` (the file names itself on its first line); `postgres/src/backend/storage/lmgr/README-SSI:3` ("Serializable Snapshot Isolation (SSI) and Predicate Locking"); `postgres/src/backend/storage/lmgr/README-SSI:6-8` (the stated reason for the placement).

**Forbidden.**
- Naming `postgres/src/backend/storage/lmgr/README` as the SSI design document. That file exists but covers the lock manager.
- Naming `postgres/src/backend/access/transam/README` or any file under `access/` as the SSI design document.
- Stating that this corpus does not document the SSI design.

**Locator.** `postgres/src/backend/storage/lmgr/README-SSI`

**Discriminates.** The path is counter-intuitive (SSI documented under the lock manager, not under `access/transam`), so it rewards having actually indexed the tree.

**Trap.** Answering `src/backend/access/transam/README` or `src/backend/storage/lmgr/README` (the latter exists but covers the lock manager, not SSI).

---

### A3 — heap-only tuples

> where is the HOT (heap-only tuples) design written down in postgres?

**Gold.** `postgres/src/backend/access/heap/README.HOT`, titled "Heap Only Tuples (HOT)". First paragraph: "The Heap Only Tuple (HOT) feature eliminates redundant index entries and allows the re-use of space taken by DELETEd or obsoleted UPDATEd tuples without performing a table-wide vacuum."

**Required.**
- The file is `postgres/src/backend/access/heap/README.HOT`.
  forms: the full path; "`README.HOT` in `src/backend/access/heap`"; the bare filename `README.HOT` with the heap access directory named. Not counted: "the heap access method directory" with no filename; `README.tuplock`.
  sources: `postgres/src/backend/access/heap/README.HOT:1` (the file names itself on its first line); `postgres/src/backend/access/heap/README.HOT:3` ("Heap Only Tuples (HOT)"); `postgres/src/backend/access/heap/README.HOT:6-9` (the opening paragraph).

**Forbidden.**
- Naming `postgres/src/backend/access/heap/README`. No such file exists at this pin; `access/heap/` holds only the suffixed `README.HOT` and `README.tuplock`.
- Naming `README.tuplock` as the HOT design document. It covers tuple locking.
- Stating that HOT is documented only in the user manual, or that no design document for it exists in this tree.

**Locator.** `postgres/src/backend/access/heap/README.HOT`

**Discriminates.** `access/heap/` holds two suffixed READMEs (`README.HOT`, `README.tuplock`) and no plain `README`; the answer requires the exact filename.

**Trap.** Answering `postgres/src/backend/access/heap/README` — that file does not exist.

---

### A4 — where a KEP's machine-readable status lives

> where is the current status of a Kubernetes enhancement recorded, and where are the legal values defined?

**Gold.** Per-KEP status lives in the `status:` field of `keps/<sig>/<number>-<slug>/kep.yaml`; there are 656 such files in the corpus. The legal values are defined in `kubernetes-enhancements/keps/sig-architecture/0000-kep-process/README.md`: line 151 says the status "Must be one of `provisional`, `implementable`, `implemented`, `deferred`, `rejected`, `withdrawn`, or `replaced`", and lines 209-218 define each one (e.g. "`withdrawn`: The authors have withdrawn the KEP.", "`replaced`: The KEP has been replaced by a new KEP.").

**Required.**
- A KEP's current status is the `status:` field of its own `kep.yaml`, one per KEP directory under `keps/<sig>/<number>-<slug>/`.
  forms: "the `status:` field in each KEP's `kep.yaml`"; naming the `kep.yaml` path pattern; naming one concrete `kep.yaml` as the example. Not counted: "in the KEP's README.md"; "in the enhancements issue tracker or release milestone"; "in the KEP" with no file named.
  sources: `kubernetes-enhancements/keps/sig-node/2221-remove-dockershim/kep.yaml:8`; `kubernetes-enhancements/keps/sig-network/536-topology-aware-routing/kep.yaml:15`; `kubernetes-enhancements/keps/sig-auth/3299-kms-v2-improvements/kep.yaml:9`.
- The legal values are defined in the KEP process document, and are `provisional`, `implementable`, `implemented`, `deferred`, `rejected`, `withdrawn`, `replaced`.
  forms: naming `keps/sig-architecture/0000-kep-process/README.md` as the definition site, with or without enumerating the seven values; enumerating the seven values with that file cited. Either the file or the value list may lead, but both must be present. Also counted, alongside the README: citing `api/proposal.go:65-74` (the `Status` constants and `ValidStatuses`) or `keps/NNNN-kep-template/kep.yaml:9` as corroboration, since both carry the same seven values and agree with it. Not counted: listing values with no defining file named; naming the file but giving a different value set; naming only a derived location without the README.
  sources: `kubernetes-enhancements/keps/sig-architecture/0000-kep-process/README.md:151` ("Must be one of `provisional`, `implementable`, `implemented`, `deferred`, `rejected`, `withdrawn`, or `replaced`"); same file :209-218 (one line defining each value).

**Forbidden.**
- Naming a derived location as the definition site *while never citing* `0000-kep-process/README.md` at all: a JSON/YAML schema, an OWNERS file, the KEP template, `api/proposal.go`, or upstream Kubernetes documentation offered as the sole authority. Citing any of those *alongside* the README is supported and is not a failure: `api/proposal.go:65-74` and `NNNN-kep-template/kep.yaml:9` both carry the same seven values, so an answer that cites them too is more complete, not less correct.
  Note for graders: reporting that the README defers to the template is CORRECT, not a demotion. `0000-kep-process/README.md:138-141` says in the source, verbatim, "While this defines the metadata schema for now, these things tend to evolve. The KEP template is the authoritative definition of things like the metadata schema." An answer that cites the README's seven values and also reports this deference is describing the record accurately and must score correct. Only an answer that never reaches the README fails here.
- Presenting `superseded`, `removed` or `implemented (beta)` as members of the defined vocabulary. They occur as literal `status:` values in this corpus but are absent from the list at `0000-kep-process/README.md:151`; reporting them as out-of-vocabulary values found in the data is supported.
- Claiming KEP status is tracked only in GitHub issues, labels or release milestones and not in the repository.

**Locator.** `kubernetes-enhancements/keps/sig-architecture/0000-kep-process/README.md` (lines 151, 209-218); any `kep.yaml`

**Discriminates.** Separates "I know KEPs have statuses" from "I know which file is authoritative and what the closed vocabulary is". It also sets up category C — several real `kep.yaml` files carry values outside this list.

---

### A5 — why CloudNativePG has no Patroni

> where does this corpus explain why CloudNativePG doesn't use Patroni, repmgr or Stolon?

**Gold.** `cloudnative-pg/docs/src/faq.md`, lines 227-238, under the question "**What are the reasons behind CloudNativePG not relying on a failover management tool like Patroni, repmgr, or Stolon?**". The answer: "we decided to take a different approach and directly extend the Kubernetes controller and rely on the Kubernetes API server to hold the status of a Postgres cluster, and use it as the only source of truth". The design consequence is described in `cloudnative-pg/docs/src/operator_capability_levels.md` line 110 onward, "### Self-contained instance manager": "Instead of relying on an external tool to coordinate PostgreSQL instances in the Kubernetes cluster pods, such as Patroni or Stolon, the operator injects the operator executable inside each pod, in a file named `/controller/manager`."

**Required.**
- The explanation lives in `cloudnative-pg/docs/src/faq.md`, in the FAQ entry that names Patroni, repmgr and Stolon.
  forms: the path, with or without line numbers; "the CloudNativePG FAQ"; quoting the entry's question or its answer. Not counted: naming some cloudnative-pg doc without identifying `faq.md`; giving the rationale with no file in this corpus cited.
  sources: `cloudnative-pg/docs/src/faq.md:227-228` (the question "What are the reasons behind CloudNativePG not relying on a failover management tool like Patroni, repmgr, or Stolon?"); `cloudnative-pg/docs/src/faq.md:230-234` (the answer).
- The recorded reason is that CloudNativePG instead extends the Kubernetes controller and uses the Kubernetes API server as the only source of truth for cluster status.
  forms: "the Kubernetes API server is the single source of truth"; "they extended the Kubernetes controller directly instead of bolting on an external tool"; quoting the sentence. Not counted: naming the file but giving no reason at all.
  sources: `cloudnative-pg/docs/src/faq.md:231-234`; corroborated by `cloudnative-pg/docs/src/operator_capability_levels.md:110-116` ("### Self-contained instance manager": "Instead of relying on an external tool ... such as Patroni or Stolon, the operator injects the operator executable inside each pod, in a file named `/controller/manager`"). Citing the capability-levels section alongside `faq.md` counts.

**Forbidden.**
- Citing `cloudnative-pg/docs/src/failover.md` or `cloudnative-pg/docs/src/failure_modes.md` as where the rationale is stated. Both describe mechanism; neither carries this rationale.
- Giving a reason the corpus does not state — licensing, Patroni's own dependence on an external DCS, benchmark results — as the recorded rationale.
- Claiming the corpus never explains the decision, only the mechanism.

**Locator.** `cloudnative-pg/docs/src/faq.md:227`; `cloudnative-pg/docs/src/operator_capability_levels.md:110`

**Discriminates.** Rationale for a *negative* design decision is exactly the kind of thing that is only in prose, in one place, and never in code.

**Trap.** Citing `cloudnative-pg/docs/src/failover.md` or `failure_modes.md` — both describe the mechanism but neither gives the rationale.

---

### A6 — asynchronous and direct I/O

> where is PostgreSQL's asynchronous / direct I/O subsystem designed?

**Gold.** `postgres/src/backend/storage/aio/README.md` — titled "# Asynchronous & Direct IO", with sections "## Motivation", "### Why Asynchronous IO", "### Why Direct / unbuffered IO". It states: "Until the introduction of asynchronous IO postgres relied on the operating system to hide the cost of synchronous IO from postgres."

**Required.**
- The file is `postgres/src/backend/storage/aio/README.md`.
  forms: the full path; "`README.md` under `src/backend/storage/aio`"; "storage/aio/README.md". Not counted: naming the `aio` directory with no filename; naming a source file (`aio.c`, `method_io_uring.c`) as the design document; giving the path without the `.md` suffix, since `storage/aio/README` does not exist.
  sources: `postgres/src/backend/storage/aio/README.md:1` ("# Asynchronous & Direct IO"); same file :3-5 ("## Motivation", "### Why Asynchronous IO"), :18 ("### Why Direct / unbuffered IO"), :7-8 ("Until the introduction of asynchronous IO postgres relied on the operating system to hide the cost of synchronous IO from postgres").

**Forbidden.**
- Naming `postgres/src/backend/storage/smgr/README` or `postgres/src/backend/storage/buffer/README` as the async/direct I/O design document.
- Stating that PostgreSQL has no asynchronous I/O subsystem, or that this tree carries no design document for it.

**Locator.** `postgres/src/backend/storage/aio/README.md`

**Discriminates.** This is the only Markdown-suffixed design README in the postgres backend tree (the rest are extensionless `README`), and it is recent enough that a model is likely to guess a stale location.

**Trap.** Answering `postgres/src/backend/storage/smgr/README` or `postgres/src/backend/storage/buffer/README`.
