# Category G — answers that live only in commit messages

Every gold answer below was read out of the `postgres` fixture's git log
(`/Users/macbook/Documents/work/tsubasa-benchmark/postgres`, read-only). None of
these answers exist in any document in the tree: they are rationale that was written
once, in a revert commit message, and never made it into the docs or the code
comments — because the code in question no longer exists.

Design rule for this category: **the question must not contain the term that makes
`git log --grep` succeed.** Each question states its withheld term explicitly so a
reviewer can check the question is fair. Questions are phrased the way they actually
arrive — "we're about to build X, has this been tried?" — by someone who does not
know the name of the thing they are about to reinvent.

Two questions are marked **Honesty probe.** For those, the log records *what* was
reverted but not *why*; the reasoning lived on pgsql-hackers and is absent from this
fixture. The correct answer names the reversal and says the log does not record the
reason. Any stated cause is a confabulation and should score zero.

---

### G1 — cost-based ordering of sort columns for grouping

> our planner sorts the grouping columns in whatever order the user wrote them. we
> want to reorder them by estimated cost instead — cheapest comparator first, most
> selective first, that kind of thing. has Postgres tried this?

**Gold.** Yes, and it was reverted. `db0d67db24` (2022-03-31, Tomas Vondra) added it;
`f4c7c410ee` (2022-10-03, Tom Lane) reverted it "and several follow-on fixes" before
the v15 release. The stated reasons, verbatim:

- "The idea of making a cost-based choice of the order of the sorting columns is not
  fundamentally unsound, but it requires cost information and data statistics that we
  don't really have."
- "relying on procost to distinguish the relative costs of different sort comparators
  is pretty pointless so long as most such comparator functions are labeled with cost
  1.0."
- "estimating the number of comparisons done by Quicksort requires more than just an
  estimate of the number of distinct values in the input: you also need some idea of
  the sizes of the larger groups, if you want an estimate that's good to better than
  a factor of three or so. That's data that's often unknown or not very reliable."
- the code "needs to make estimates of the numbers of distinct values of multiple
  columns, which are necessarily even less trustworthy than per-column stats."
- "the cost algorithm as-implemented cannot offer useful information about how to
  order sorting columns beyond the point at which the average group size is estimated
  to drop to 1."
- "Close inspection of the code added by db0d67db2 shows that there are also multiple
  small bugs."
- the changes to `cost_sort` "made for very large changes (often a factor of 2 or so)
  in the cost estimates for all sorting operations", changing plan choices broadly
  with "precious little evidence to show that the changes are for the better."

**Required.**
- Yes, Postgres tried it: cost-based reordering of the GROUP BY / grouping sort columns was committed and then reverted before v15 shipped.
  forms: "it was tried and reverted"; naming `db0d67db24` as the commit that added it and `f4c7c410ee` as the revert; "added March 2022, reverted October 2022"; "reverted just before the v15 release". At minimum the answer must say it was implemented and then removed. Not counted: "Postgres does not do this" with no statement that it once did; "there was discussion but it never landed".
  sources: `postgres` commit `f4c7c410ee` (2022-10-03, Tom Lane, "Revert \"Optimize order of GROUP BY keys\"."); `postgres` commit `db0d67db24` (2022-03-31, Tomas Vondra, "Optimize order of GROUP BY keys"); graph event `evt-20221003-postgres-revert-f4c7c410`.
- The reason is that the cost model lacked trustworthy inputs, not that the idea is unsound. At least one concrete gap from the revert message must be named.
  forms: any one of — "it requires cost information and data statistics that we don't really have"; "`procost` is useless for distinguishing comparators because most are labelled cost 1.0"; "estimating Quicksort comparisons needs group-size information, not just ndistinct"; "multi-column ndistinct estimates are even less trustworthy than per-column stats"; "the algorithm says nothing past the point where average group size drops to 1". Any one counts; the full list is not required. Not counted: "it was buggy" or "it didn't work" with no mechanism named; asserting the approach is fundamentally wrong, which the message explicitly denies ("not fundamentally unsound").
  sources: `postgres` commit `f4c7c410ee`; graph event `evt-20221003-postgres-revert-f4c7c410`.

**Forbidden.**
- Stating that PostgreSQL currently reorders grouping columns by estimated cost, or that the feature shipped in v15 or later.
- Stating that no such attempt exists in the record, or that the question is unanswerable from this repository.
- Attributing the revert solely to a licensing, API-stability or process reason. The message does note a v15 deadline and an ABI concern about `T_PathKeyInfo`, but those are secondary to the estimate-quality argument; mentioning them alongside it is supported.
- Attributing the revert to a measured performance regression in sorting. The stated concern is that `cost_sort` changes moved plan choices broadly with "precious little evidence to show that the changes are for the better" — an absence of evidence, not a demonstrated regression.

**Locator.** `postgres` commits `db0d67db24`, `f4c7c410ee`

**Withheld term.** "GROUP BY keys" — the phrase in both subjects, which makes
`git log --grep` succeed on the first try.

**Discriminates.** Vanilla must guess the search term. The reverted code is gone, so
no file in the tree contains this rationale.

---

### G2 — propagating table aliases into a whole-row reference

> when a query renames a table's columns in the FROM clause, we want a reference to
> the whole row to come back labelled with the new names rather than the base table's
> names. seems more consistent. any reason Postgres doesn't do it?

**Gold.** It did do it, for years, and then stopped. `bf7ca15875` (2014-11-10, Tom
Lane, "Ensure that RowExprs and whole-row Vars produce the expected column names")
introduced the behavior; `ec62cb0aac` (2022-03-17, Tom Lane) reverted that part and
back-patched the revert to all supported branches. Verbatim reasons:

- "that's not terribly logically consistent, because now the output of the Var is no
  longer of the named composite type that the Var claims to emit."
- the original patch coped by relabelling the tuples as blessed RECORD, "but that's
  really pretty disastrous: we can wind up storing such tuples onto disk, whereupon
  they're not readable by other sessions."
- the fix: "the column names of tuples produced by a whole-row Var are always those of
  the underlying named composite type, query aliases or no."
- Tom notes the discomfort of shipping this as a back-patch: "What *is* kind of awful
  is to make such a behavioral change in a back-patched bug fix. But corrupt data is
  worse, so back-patched it will be."
- documented workaround, from the same message: "introduce an extra level of
  sub-SELECT, so that the whole-row Var is referring to the sub-SELECT's output and
  not to a named table type. Then the Var is of type RECORD to begin with and there's
  no issue."

**Required.**
- The behaviour was implemented and later removed: Postgres did label whole-row references with the query's column aliases, and stopped.
  forms: "it was tried and reverted"; naming `bf7ca15875` as the commit that added it and `ec62cb0aac` as the revert; "removed in 2022 and back-patched". Not counted: "Postgres doesn't do this" with no statement that it once did.
  sources: `postgres` commit `ec62cb0aac5b`; `postgres` commit `bf7ca15875`; graph event `evt-20220317-postgres-revert-ec62cb0a`.
- The reason is a data-durability defect, not a style choice: relabelling the tuple as blessed RECORD meant such tuples could be written to disk where other sessions could not read them.
  forms: "corrupt data"; "tuples stored on disk unreadable by other sessions"; "the Var no longer matches the named composite type it claims to emit". Any one of these counts. Not counted: "it caused bugs" or "it was inconsistent" with no mechanism named.
  sources: `postgres` commit `ec62cb0aac5b`; graph event `evt-20220317-postgres-revert-ec62cb0a`.

**Forbidden.**
- Stating that Postgres currently propagates FROM-clause column aliases into whole-row references.
- Stating that the idea was never tried, or that no record of it exists.
- Attributing the revert to performance, to standards compliance, or to a planner regression.

**Locator.** `postgres` commits `bf7ca15875`, `ec62cb0aac`

**Withheld term.** "whole-row Var" (and "column aliases") — the phrase in the revert
subject.

**Discriminates.** The answer is a data-corruption argument that exists only in the
revert message; the current code simply does the plain thing with no comment saying
why the other thing was tried.

---

### G3 — stamping the statistics file with the checkpoint position it belongs to

> we want to write the checkpoint position into our statistics file so that on
> startup we can tell whether the stats we're loading actually correspond to the WAL
> we're about to replay from. did Postgres do this?

**Gold.** Added and then reverted before release. `b860848232aa` (2024-08-02, Michael
Paquier, "Add redo LSN to pgstats files") added it as "a prerequisite for the support
of pgstats data flush across checkpoints, linking a pgstats file to a specific
checkpoint redo LSN". `5721e5453e` (2025-03-17) reverted it, bumping
`PGSTAT_FILE_FORMAT_ID`. Verbatim reasons:

- "this is proving to be currently problematic when going through a pg_upgrade, that
  does direct manipulations of the control file in the new cluster."
- "The LSN stored in the pgstats file is not able to cope with any changes done in the
  control file by pg_upgrade yet, causing the pgstats file to be discarded when
  starting the new cluster after overriding its redo LSN (one is a `pg_resetwal -l`
  where the new cluster's start LSN is bumped by a hardcoded value of 8 segments, see
  copy_xact_xlog_xid)."
- the stated path forward: "a refactor of the pgstats code so as it is possible to
  read and write some of its data with some routines in src/common/, so as pg_upgrade
  or pg_resetwal are able to update its data."
- consequence of the revert: "The pgstats file is currently only written as part of a
  shutdown sequence, and its contents are still lost on crash, same as older
  releases."

**Required.**
- Yes: Postgres added a redo LSN to the pgstats file and then reverted it before the release.
  forms: "it was added and reverted"; naming `b860848232aa` as the commit that added it and `5721e5453e` as the revert; "added August 2024, reverted March 2025"; "reverted before v18". At minimum the answer must say it was implemented and then removed. Not counted: "Postgres doesn't stamp its stats file" with no statement that it once did.
  sources: `postgres` commit `5721e5453e` (2025-03-17, Michael Paquier, "Revert \"Add redo LSN to pgstats files\""); `postgres` commit `b860848232aa` (2024-08-02, Michael Paquier, "Add redo LSN to pgstats files"); graph event `evt-20250317-postgres-revert-5721e545`.
- The reason is an interaction with tools that rewrite the control file out of band — `pg_upgrade` in particular — which leaves the embedded LSN stale and causes the stats file to be discarded.
  forms: "pg_upgrade manipulates the control file directly and the stored LSN can't cope"; "the new cluster's redo LSN gets overridden (`pg_resetwal -l`, bumped by a hardcoded 8 segments in `copy_xact_xlog_xid`) so the stats file is thrown away"; "an LSN baked into a data file goes stale when an external tool rewrites the control file". Any one counts. Not counted: "it caused problems on upgrade" with no mechanism named.
  sources: `postgres` commit `5721e5453e`; graph event `evt-20250317-postgres-revert-5721e545`.

**Forbidden.**
- Stating that the redo LSN is currently written into the pgstats file, or that pgstats data survives across checkpoints or crashes at this pin. The revert states the file "is currently only written as part of a shutdown sequence, and its contents are still lost on crash, same as older releases."
- Stating that no such attempt exists in the record.
- Attributing the revert to performance, to a WAL-format change, to replication, or to a bug in the LSN tracking itself. The stated defect is the pg_upgrade / control-file interaction.
- Presenting the idea as rejected outright. The message names a path forward — refactoring pgstats so `src/common/` routines can read and write it, letting `pg_upgrade` or `pg_resetwal` update the data.

**Locator.** `postgres` commits `b860848232aa`, `5721e5453e`

**Withheld term.** "redo LSN" / "pgstats" — the exact words of both subjects.

**Discriminates.** The interaction between an out-of-band tool that rewrites the
control file and an LSN embedded in a data file is recorded nowhere but here.

---

### G4 — letting the horizon advance past a long-running index build

> a non-blocking index build on a big table pins the oldest visible transaction for
> hours, and every other table in the database bloats while it runs. we want vacuum
> to just ignore those sessions when it computes the horizon. is that safe?

**Gold.** No — it was tried, shipped in PG14, and reverted as an index-corruption bug.
`d9d076222f` (2021-02-23, Alvaro Herrera, "VACUUM: ignore indexing operations with
CONCURRENTLY") added it; `e28bb88519` (2022-05-31, Alvaro Herrera) reverted it and
back-patched to 14. Verbatim reasons:

- "These changes caused indexes created with the CONCURRENTLY option to miss heap
  tuples that were HOT-updated and HOT-pruned during the index creation."
- "Before these changes, HOT pruning would have been prevented by the Xmin of the
  transaction creating the index, but because this change was precisely to allow the
  Xmin to move forward ignoring that backend, now other backends scanning the table
  can prune them."
- the subtlety about which reader is dangerous: "This is not a problem for VACUUM
  (which requires a lock that conflicts with a CREATE INDEX CONCURRENTLY operation),
  but HOT-prune can definitely occur."
- the one-line summary: "Xmin advancement was sped up, but at the cost of corrupting
  the resulting index."
- and the closing note: "the new feature in PG14 that RIC/CIC on very large tables no
  longer force VACUUM to retain very old tuples goes away. We might try to implement
  it again in a later release, but for now the risk of indexes missing tuples is too
  high and there's no easy fix."

**Required.**
- No, it is not safe: the change was made, shipped in PG14, and reverted.
  forms: "no — it was tried and reverted"; naming `d9d076222f` as the commit that added it and `e28bb88519` as the revert; "shipped in 14 and taken back out in 2022, back-patched to 14". At minimum the answer must say no *and* that this was tried and withdrawn. Not counted: a bare "no, that would be unsafe" reasoned from first principles with no record cited; "vacuum doesn't do that" with no statement that it once did.
  sources: `postgres` commit `e28bb88519` (2022-05-31, Alvaro Herrera, "Revert changes to CONCURRENTLY that \"sped up\" Xmin advance"); `postgres` commit `d9d076222f` (2021-02-23, Alvaro Herrera, "VACUUM: ignore indexing operations with CONCURRENTLY"); graph event `evt-20220531-postgres-revert-e28bb885`.
- The failure mode is index corruption: indexes built CONCURRENTLY silently missed heap tuples that were HOT-updated and HOT-pruned during the build.
  forms: "the resulting index misses rows"; "corrupt/incomplete index"; "HOT pruning removed tuples the index build never saw, because the builder's Xmin no longer held them back". The word "corruption" is not required if the missing-tuples mechanism is stated, and vice versa. Not counted: "it caused bugs" or "it was unsafe" with no failure mode named; describing the failure as an error, a crash, or a performance problem rather than silently wrong index contents.
  sources: `postgres` commit `e28bb88519`; graph event `evt-20220531-postgres-revert-e28bb885`.

**Forbidden.**
- Stating that PostgreSQL currently ignores CREATE INDEX CONCURRENTLY / REINDEX CONCURRENTLY backends when computing the vacuum horizon, or that the PG14 behaviour still stands.
- Stating that no such attempt exists in the record, or that the idea was never shipped.
- Naming VACUUM itself as the dangerous reader. The message is explicit that VACUUM is not the problem — it takes a conflicting lock — and that HOT-prune by ordinary backends is; stating that distinction is supported.
- Presenting the reversal as permanent by design. The message says "We might try to implement it again in a later release, but for now the risk of indexes missing tuples is too high and there's no easy fix."

**Locator.** `postgres` commits `d9d076222f`, `e28bb88519`

**Withheld term.** "CONCURRENTLY" and "Xmin" — either one finds the pair immediately.

**Discriminates.** A vanilla agent reading the current source sees only that vacuum
does *not* ignore these backends, with no record that the alternative was shipped and
withdrawn, or that the failure mode was silently missing index entries rather than an
error.

---

### G5 — keeping updates heap-only when the changed column is covered only by a coarse summary index

> we have an index type that stores a per-page-range summary rather than a pointer per
> row. since it has no per-row pointers, we want to treat a column that only that
> index covers as "not indexed" when deciding whether an update can stay heap-only.
> is that reasoning sound?

**Gold.** No. That exact reasoning was committed and then reverted as index-corrupting.
`5753d4ee32` (2021-11-30, Tomas Vondra, subject "Ignore BRIN indexes when checking for
HOT udpates" — typo in the original) plus follow-up `fe60b67250` were reverted by
`e3fcca0d0d` (2022-06-16, Tomas Vondra). The revert quotes the original justification —

  "When determining whether an index update may be skipped by using HOT, we can ignore
  attributes indexed only by BRIN indexes. There are no index pointers to individual
  tuples in BRIN, and the page range summary will be updated anyway as it relies on
  visibility info."

— and then rebuts it verbatim:

- "This is partially incorrect - it's true BRIN indexes don't point to individual
  tuples, so HOT chains are not an issue, but the visibitlity info is not sufficient
  to keep the index up to date. This can easily result in corrupted indexes, as
  demonstrated in the hackers thread."
- "This does not mean relaxing the HOT restrictions for BRIN is a lost cause, but it
  needs to handle the two aspects (allowing HOT chains and updating the page range
  summaries) as separate. But that requires a major changes, and it's too late for
  that in the current dev cycle."

**Required.**
- No, the reasoning is not sound: that exact argument was committed for BRIN indexes and then reverted.
  forms: "no — it was tried and reverted"; naming `5753d4ee32` as the commit that added it and `e3fcca0d0d` as the revert; "reverted in June 2022". Naming the follow-up `fe60b67250` is optional. At minimum the answer must say no *and* that the change was made and withdrawn. Not counted: a bare "no" reasoned from first principles with no record cited; "Postgres doesn't do that" with no statement that it once did.
  sources: `postgres` commit `e3fcca0d0d` (2022-06-16, Tomas Vondra, "Revert changes in HOT handling of BRIN indexes"); `postgres` commit `5753d4ee32` (2021-11-30, Tomas Vondra, "Ignore BRIN indexes when checking for HOT udpates" — typo in the original subject); graph event `evt-20220616-postgres-revert-e3fcca0d`.
- The argument is half right and half wrong: the absence of per-row pointers does dispose of the HOT-chain problem, but visibility information alone is not enough to keep the page-range summary current, so the index can be corrupted.
  forms: "HOT chains are genuinely not an issue, but the visibility info is not sufficient to keep the index up to date"; "the summary does not get updated the way the original commit assumed, so the index goes stale/corrupt"; "the two concerns — allowing HOT chains and updating the range summaries — have to be handled separately". The answer must convey both halves: the premise that is accepted and the one that fails. Not counted: "BRIN can't be treated as unindexed" with no reason; rejecting the whole premise, including the part the revert concedes.
  sources: `postgres` commit `e3fcca0d0d`; graph event `evt-20220616-postgres-revert-e3fcca0d`.

**Forbidden.**
- Stating that PostgreSQL currently ignores BRIN-only attributes when deciding whether an update can be heap-only.
- Stating that no such attempt exists in the record.
- Stating that the reason the argument fails is that BRIN *does* carry per-tuple pointers, or that HOT chains break BRIN. The revert concedes both of those points to the original commit.
- Attributing the revert to performance, to a planner interaction, or to a locking problem rather than to index corruption.

**Locator.** `postgres` commits `5753d4ee32`, `fe60b67250`, `e3fcca0d0d`

**Withheld term.** "BRIN" and "HOT" — both appear in the subjects.

**Discriminates.** The half-right/half-wrong structure of the argument ("HOT chains
are not an issue, but the visibility info is not sufficient") is the whole answer, and
it survives only in the revert message.

---

### G6 — removing the global lock that serialises WAL buffer page assignment

> we have one cluster-wide lock serialising the assignment of WAL buffer pages, and
> it's our top contention point at high write rates. we want to replace it with a
> lock-free scheme and have waiters sleep on a condition variable instead. has anyone
> got this to work?

**Gold.** Tried twice, reverted twice. First attempt `6a2275b895` (2025-02-17,
Alexander Korotkov, "Get rid of WALBufMappingLock") was reverted the same day by
`3fb58625d1`: "Buildfarm failure on batta spots some concurrency issue, which requires
further investigation." Second attempt `bc22dc0e0d` (2025-04-02, same author, same
subject) was reverted by `c13070a27b` (2025-08-22) and back-patched through 18, for a
fundamental reason quoted verbatim:

- "It appears that conditional variables are not suitable for use inside critical
  sections. If WaitLatch()/WaitEventSetWaitBlock() face postmaster death, they exit,
  releasing all locks instead of PANIC. In certain situations, this leads to data
  corruption."

Reported by Andrey Borodin; the revert carries an unusually long reviewer list
(Tom Lane, Thomas Munro, Andres Freund, Tomas Vondra, Michael Paquier and others).

**Required.**
- It was attempted and reverted — twice, by the same author, under the same subject "Get rid of WALBufMappingLock".
  forms: naming both attempts and both reverts (`6a2275b895` reverted by `3fb58625d1` on 2025-02-17; `bc22dc0e0d` reverted by `c13070a27b` on 2025-08-22); "tried twice, backed out twice"; "an attempt in February 2025 and another in April 2025, both reverted". Naming at least the second attempt and its revert is required; getting the "twice" shape is what the question is for, so an answer that reports only one attempt and one revert counts only if it does not assert that this was the only attempt. Not counted: "nobody has tried this"; "it is in current Postgres".
  sources: `postgres` commits `6a2275b895` (2025-02-17), `3fb58625d1` (2025-02-17), `bc22dc0e0d` (2025-04-02), `c13070a27b` (2025-08-22, back-patched through 18); graph events `evt-20250217-postgres-revert-3fb58625` and `evt-20250822-postgres-revert-c13070a2`.
- The second, fundamental reason: condition variables are not usable inside critical sections, because on postmaster death `WaitLatch()` / `WaitEventSetWaitBlock()` exit and release locks instead of PANICking, which can corrupt data.
  forms: "you can't wait on a condition variable inside a critical section"; "on postmaster death the wait returns and releases locks instead of PANICking, so the critical section is abandoned half-done"; "it can lead to data corruption". The generalisable rule or the postmaster-death mechanism or the corruption outcome — any one counts, but "it was buggy" alone does not.
  sources: `postgres` commit `c13070a27b`; graph event `evt-20250822-postgres-revert-c13070a2`.

**Forbidden.**
- Stating that WALBufMappingLock has been removed in current PostgreSQL, or that the lock-free scheme is in a released version. The second revert was back-patched through 18.
- Stating that no such attempt exists in the record.
- Giving the first revert's reason ("Buildfarm failure on batta spots some concurrency issue, which requires further investigation") as the reason the approach was abandoned. It is a real quote from `3fb58625d1` and citing it for the *first* revert is supported; presenting it as the final verdict is not.
- Attributing the second revert to lock contention not improving, to a benchmark regression, or to a specific memory-ordering bug in the lock-free algorithm.

**Locator.** `postgres` commits `6a2275b895`, `3fb58625d1`, `bc22dc0e0d`, `c13070a27b`

**Withheld term.** "WALBufMappingLock" — the lock's name, which is the whole subject
line of all four commits.

**Discriminates.** The generalisable lesson — *do not wait on a condition variable
inside a critical section, because postmaster-death handling unwinds instead of
PANICking* — is stated in no header comment, no README and no doc. It exists in one
revert message, and the "tried twice" shape is only visible from the log.

---

### G7 — replicating sequence advances through logical replication

> our failover target hands out duplicate ids after promotion because sequence
> positions don't cross the logical replication link. we want sequence changes decoded
> and shipped like any other change. is there prior art in Postgres?

**Gold.** Yes — a nine-commit feature was developed and then removed before v15.
`0da92dc530` (2022-02-10, Tomas Vondra, "Logical decoding of sequences") and eight
follow-ups were reverted by `2c7ea57e56` (2022-04-07, Tomas Vondra), which lists all
nine SHAs (`0da92dc530c9`, `80901b32913f`, `b779d7d8fdae`, `d5ed9da41d96`,
`a180c2b34de0`, `75b1521dae1f`, `2d2232933b02`, `002c9dd97a0c`, `05843b1aa49d`).
Reason, verbatim and complete — it is two sentences:

- "The implementation has issues, mostly due to combining transactional and
  non-transactional behavior of sequences. It's not clear how this could be fixed, but
  it'll require reworking significant part of the patch."

**Required.**
- Yes: logical decoding of sequences was built and then removed before v15 shipped.
  forms: "it was implemented and reverted"; naming `0da92dc530` as the commit that added it and `2c7ea57e56` as the revert; "a multi-commit feature reverted in April 2022"; "nine commits were reverted". The count of nine and the individual SHAs are not required. At minimum the answer must say the feature was developed and then removed. Not counted: "Postgres does not replicate sequence positions" with no statement that it once did; "there is a patch on the list" as though it had never landed.
  sources: `postgres` commit `2c7ea57e56` (2022-04-07, Tomas Vondra, "Revert \"Logical decoding of sequences\"", which lists all nine reverted SHAs); `postgres` commit `0da92dc530` (2022-02-10, Tomas Vondra, "Logical decoding of sequences"); graph event `evt-20220407-postgres-revert-2c7ea57e`.
- The recorded reason is that sequences are partly transactional and partly not, and the implementation could not combine the two; the fix was judged to need significant rework.
  forms: "combining transactional and non-transactional behaviour of sequences"; "sequences aren't purely transactional and the decoding model had no place for that"; "it wasn't clear how to fix it without reworking a significant part of the patch". Either the transactional/non-transactional diagnosis or the "needs significant rework, unclear how to fix" verdict counts; both is better. Not counted: "it had bugs" with no diagnosis.
  sources: `postgres` commit `2c7ea57e56`; graph event `evt-20220407-postgres-revert-2c7ea57e`.

**Forbidden.**
- Stating that logical replication of sequences shipped in PostgreSQL 15, or that sequence positions cross a logical replication link at this pin.
- Stating that no such attempt exists in the record.
- Attributing the revert to performance, to protocol/wire-format compatibility, to replication-slot handling, or to a specific named bug. The message gives exactly one diagnosis, and it is the transactional/non-transactional mismatch.
- Presenting a specific plan or release for re-landing it. The message says only that a fix "will require reworking significant part of the patch" and that it is not clear how.

**Locator.** `postgres` commits `0da92dc530`, `2c7ea57e56`

**Withheld term.** "Logical decoding of sequences" — the subject line verbatim.

**Discriminates.** The diagnosis (a sequence is *partly* transactional and *partly*
not, and the decoding model has no place for that) is not derivable from any current
file; nothing in the tree records that the attempt was ever made.

---

### G8 — skipping the free-space bookkeeping structure for tiny tables

> nearly all our tables are a handful of pages. the auxiliary per-relation structure
> that tracks free space per page costs more to create and maintain than it can
> possibly save at that size. we want to skip creating it below a size threshold. has
> Postgres tried this?

**Gold.** Yes, it shipped in PG12 development and was reverted, not because the idea
was wrong but because the mechanism was. `b0eaa4c51b` (2019-02-04, Amit Kapila, "Avoid
creation of the free space map for small heap relations, take 2.") was reverted by
`7db0cde6b5` (2019-05-07, Amit Kapila), which also reverted six follow-ups
(`06c8a5090e`, `13e8643bfc`, `6f918159a9`, `9c32e4c350`, `29d108cdec`, `08ecdfe7e5`).
Verbatim reason:

- "This feature was using a process local map to track the first few blocks in the
  relation. The map was reset each time we get the block with enough freespace."
- "It was discussed that it would be better to track this map on a per-relation basis
  in relcache and then invalidate the same whenever vacuum frees up some space in the
  page or when FSM is created."
- "The new design would be better both in terms of API design and performance."

Note the collateral damage listed in the revert: `13e8643bfc` "During pg_upgrade,
conditionally skip transfer of FSMs" went out with it.

**Required.**
- Yes: skipping FSM creation for small heap relations was committed during the PG12 cycle and then reverted.
  forms: "it was implemented and reverted"; naming `b0eaa4c51b` as the commit that added it and `7db0cde6b5` as the revert; "landed February 2019, reverted May 2019". The six co-reverted follow-up SHAs are not required. At minimum the answer must say it was implemented and then removed. Not counted: "Postgres always creates an FSM" with no statement that this was tried.
  sources: `postgres` commit `7db0cde6b5` (2019-05-07, Amit Kapila, "Revert \"Avoid the creation of the free space map for small heap relations\".", which lists the seven reverted commits); `postgres` commit `b0eaa4c51b` (2019-02-04, Amit Kapila); graph event `evt-20190507-postgres-revert-7db0cde6`.
- The objection was to the mechanism, not the idea: the implementation tracked the map in **process-local** memory and reset it whenever a block with enough free space was found; reviewers wanted it kept per-relation in relcache with proper invalidation.
  forms: "the map was process-local and got reset, so it should live in relcache per relation and be invalidated when vacuum frees space or the FSM is created"; "the caching location was wrong"; "it was an API-design and performance objection, not a rejection of skipping the FSM". The answer must convey that the *feature idea* was not what was rejected. Not counted: "it was buggy"; "small tables still need an FSM"; treating the revert as a verdict against the optimisation itself.
  sources: `postgres` commit `7db0cde6b5`; graph event `evt-20190507-postgres-revert-7db0cde6`.

**Forbidden.**
- Stating that PostgreSQL currently skips FSM creation for relations below a size threshold, or that the feature shipped in 12.
- Stating that no such attempt exists in the record.
- Stating that the idea itself was rejected as wrong or unsafe. The message says the redesign "would be better both in terms of API design and performance", i.e. the feature was expected to return in a better shape.
- Attributing the revert to correctness bugs, to a crash, to WAL interaction, or to pg_upgrade. `13e8643bfc` "During pg_upgrade, conditionally skip transfer of FSMs" was collateral in the same revert; naming it as collateral is supported, naming it as the cause is not.

**Locator.** `postgres` commits `b0eaa4c51b`, `7db0cde6b5`

**Withheld term.** "free space map" / "FSM" — in the subject of both.

**Discriminates.** The answer is "yes, and the reason it came out was the *caching
location* (process-local vs. relcache with proper invalidation), not the feature" —
a design-review verdict that exists only in this message.

---

### G9 — wrapping the formatted-output functions so a failure can never be ignored

> we found a class of bug where a formatting call fails, returns a negative count that
> nobody checks, and a stale buffer gets used. we want to route every such call in the
> backend through a wrapper that raises an error on failure, so it can't be silently
> ignored. sound plan?

**Gold.** It was done — as a security fix, under a CVE — and reverted one day later.
`16304a0134` (2015-05-18, Noah Misch, "Add error-throwing wrappers for the printf
family of functions") was reverted by `0c071936e9` (2015-05-19, Tom Lane) under
`Security: CVE-2015-3166`. Verbatim reasons:

- "Fujii Masao reported that the previous commit caused failures in psql on OS X,
  since if one exits the pager program early while viewing a query result, psql sees
  an EPIPE error from fprintf --- and the wrapper function thought that was reason to
  panic. (It's a bit surprising that the same does not happen on Linux.)"
- "Further discussion among the security list concluded that the risk of other such
  failures was far too great, and that the one-size-fits-all approach to error
  handling embodied in the previous patch is unlikely to be workable."
- the residual risk was accepted explicitly: "This leaves us again exposed to the
  possibility of the type of failure envisioned in CVE-2015-3166. However, that failure mode is strictly
  hypothetical at this point... even granting that an unprivileged attacker could
  control the occurrence of ENOMEM with some precision, it's a stretch to believe that
  he could induce it just where the target buffer contains some valuable information."
- "we concluded that the risk of non-hypothetical problems induced by the patch
  greatly outweighs the security risks. We will therefore revert, and instead undertake
  closer analysis to identify specific calls that may need hardening, rather than
  attempt a universal solution."
- **partial revert**: "We have kept the portion of the previous patch that improved
  snprintf.c's handling of errors when it calls the platform's sprintf(). That seems
  to be an unalloyed improvement." (`cac18a76bb` was also dropped as no longer needed.)

**Required.**
- It was done and then reverted, one day later.
  forms: "it was tried and reverted"; naming `16304a0134` as the commit that added the wrappers and `0c071936e9` as the revert; "added 2015-05-18, reverted 2015-05-19". At minimum the answer must say the blanket wrapper was implemented and then withdrawn. Not counted: "Postgres doesn't do that" with no statement that it once did; treating it as a proposal that was never merged.
  sources: `postgres` commit `0c071936e9` (2015-05-19, Tom Lane, "Revert error-throwing wrappers for the printf family of functions.", trailer `Security: CVE-2015-3166`); `postgres` commit `16304a0134` (2015-05-18, Noah Misch); graph event `evt-20150519-postgres-revert-0c071936`.
- What killed it was a legitimate error the wrapper treated as fatal: `EPIPE` from `fprintf` in psql when the user quits the pager early, reported on OS X by Fujii Masao — and the broader judgement that one-size-fits-all error handling is unworkable.
  forms: "EPIPE when you exit the pager early in psql"; "a normal, expected I/O error got turned into a panic"; "the security list concluded a universal approach to error handling was unlikely to be workable". Either the concrete EPIPE case or the general one-size-fits-all verdict counts; both is better. Not counted: "it broke things" with no failure named.
  sources: `postgres` commit `0c071936e9`; graph event `evt-20150519-postgres-revert-0c071936`.

**Forbidden.**
- Stating that the backend currently routes printf-family calls through error-throwing wrappers, or that the CVE-2015-3166 hardening took this form.
- Stating that no such attempt exists in the record.
- Stating that the revert was total. Part of the patch was kept — the improvements to `snprintf.c`'s handling of errors from the platform's `sprintf()`, described as "an unalloyed improvement"; `cac18a76bb` was dropped as no longer needed. Reporting the partial revert is supported and reporting a total revert is not.
- Attributing the revert to performance, to portability of the wrapper code as such, or to a rejection of the underlying security concern. The message accepts re-exposure to the CVE-2015-3166 failure mode as a deliberate trade, calling that mode "strictly hypothetical at this point", and commits instead to hardening specific call sites.

**Locator.** `postgres` commits `16304a0134`, `cac18a76bb`, `0c071936e9`

**Withheld term.** "printf" — one grep on it finds both commits.

**Discriminates.** Three things live only here: that a blanket wrapper was tried, that
EPIPE from a pager is what killed it, and that the project consciously chose to
re-expose a CVE rather than keep it. A partial revert (snprintf.c kept) is also
invisible from the tree.

---

### G10 — short-circuiting a freeze pass that has nothing left to freeze

> autovacuum keeps launching whole-table freeze passes against tables that were
> already frozen, and the wasted work is measurable. we want a fast path that just
> skips the job when nothing actually needs freezing. any risk?

**Gold.** Yes — the exact optimisation shipped and was reverted a year later because it
could wedge autovacuum permanently. `2aa6e331ea` (2019-03-31, Michael Paquier, "Skip
redundant anti-wraparound vacuums") was reverted by `dd9ac7d5d8` (2020-03-31, Michael
Paquier), back-patched through 12. Verbatim reasons:

- the fast path skipped "anti-wraparound and non-aggressive autovacuum jobs (these
  have no sense as anti-wraparound implies aggressive)."
- "With a cluster using a high amount of relations with a portion of them being heavily
  updated, this could cause autovacuum to lock down, with autovacuum workers attempting
  repeatedly those jobs on the same relations for the same database, that just kept
  being skipped. This lock down can be solved with a manual VACUUM FREEZE."
- reproduction notes: "Justin King has reported one environment where the issue
  happened, and Julien Rouhaud and I have been able to reproduce it in a second
  environment. With a very aggressive autovacuum_freeze_max_age, triggering those jobs
  with pgbench is a matter of minutes, and hitting the lock down is a lot harder."
- and the crucial scoping detail — "Note that anti-wraparound and non-aggressive jobs
  can only be triggered on a subset of shared catalogs:" followed by a seven-item list
  in the message body: `pg_auth_members`, `pg_authid`, `pg_database`,
  `pg_replication_origin`, `pg_shseclabel`, `pg_subscription`, `pg_tablespace`.
- the underlying bug was *not* fixed: "While the lock down was possible down to v12,
  the root cause of those jobs is a much older issue, which needs more analysis."

**Required.**
- Yes, there is a risk, and it is recorded: the optimisation shipped and was reverted a year later.
  forms: "it was tried and reverted"; naming `2aa6e331ea` as the commit that added it and `dd9ac7d5d8` as the revert; "added March 2019, reverted March 2020, back-patched through 12". At minimum the answer must say the fast path was implemented and then removed. Not counted: "autovacuum doesn't skip those jobs" with no statement that it once did; a risk argued from first principles with no record cited.
  sources: `postgres` commit `dd9ac7d5d8` (2020-03-31, Michael Paquier, "Revert \"Skip redundant anti-wraparound vacuums\"", `Backpatch-through: 12`); `postgres` commit `2aa6e331ea` (2019-03-31, Michael Paquier, "Skip redundant anti-wraparound vacuums"); graph event `evt-20200331-postgres-revert-dd9ac7d5`.
- The hazard is that autovacuum can lock down: workers retry the same skipped relations for the same database indefinitely and make no progress.
  forms: "autovacuum wedges / locks down / spins"; "workers repeatedly attempt the same jobs on the same relations and they just keep being skipped"; naming the escape hatch, a manual `VACUUM FREEZE`. The mechanism (skipped jobs are re-queued rather than retired) is what matters. Not counted: "it could cause bloat" or "it might miss a freeze" — the recorded failure is a livelock, not a missed freeze.
  sources: `postgres` commit `dd9ac7d5d8`; graph event `evt-20200331-postgres-revert-dd9ac7d5`.

**Forbidden.**
- Stating that PostgreSQL currently short-circuits redundant anti-wraparound vacuum jobs, or that the fast path is in a supported release.
- Stating that no such attempt exists in the record.
- Stating that the underlying cause of the spurious jobs was found and fixed. The message says the opposite: "the root cause of those jobs is a much older issue, which needs more analysis."
- Attributing the revert to wraparound-safety, to data loss, or to a freeze being incorrectly skipped on a user table. The affected jobs arise only on a subset of shared catalogs (`pg_auth_members`, `pg_authid`, `pg_database`, `pg_replication_origin`, `pg_shseclabel`, `pg_subscription`, `pg_tablespace`); reproducing that scoping is supported, and the list need not be complete or quoted.

**Locator.** `postgres` commits `2aa6e331ea`, `dd9ac7d5d8`

**Withheld term.** "anti-wraparound" — in the subject of both commits.

**Discriminates.** The list of seven shared catalogs, and the fact that the underlying
cause of the spurious jobs was never found, appear in no document. A vanilla agent will
either not find the revert at all or, worse, describe the optimisation as current
behavior.

---

### G11 — a large feature set pulled out of a release with no reason given

> we're planning to depend on the SQL/JSON constructor and query functions
> (JSON_TABLE, JSON_QUERY, IS JSON and friends) landing in PostgreSQL 15. is that a
> safe bet, and if anything went wrong, what was it?

**Honesty probe.**

**Gold.** Not a safe bet — the whole feature set was removed from 15 and the log does
not say why. `2f2b18bd3f` (2022-09-01, Andrew Dunstan, "Revert SQL/JSON features")
reverted 23 commits, listing each SHA and subject, from `f79b803dcc` "Common SQL/JSON
clauses" through `67b26703b` "expression eval: Fix EEOP_JSON_CONSTRUCTOR and
EEOP_JSONEXPR size." The message says only:

- "The reverts the following and makes some associated cleanups:" — followed by the
  23-commit list.
- "The release notes are also adjusted."
- "Backpatch to release 15."
- a bare `Discussion:` link to a pgsql-hackers thread.

**No rationale of any kind is recorded in the commit message.** The correct answer is:
report the reversal, name the commit and date, note that it was back-patched to 15, and
state plainly that the reason lived on the mailing list and is not in this repository.
Anything about performance, standards compliance, a specific bug, or a named reviewer's
objection is invented.

**Trap.** There is a nearby, unrelated revert that *does* give a reason, and it is the
obvious thing to grab: `1460fc5942` (2022-03-22, Andrew Dunstan, "Revert 'Common
SQL/JSON clauses'") says "This has caused issues with a significant number of buildfarm
members". That one reverts `865fe4d5df`, a March attempt that was superseded — the
version that survived until September is `f79b803dcc`, a *different* commit with the
same subject. Answering the September question with the March buildfarm reason is
wrong and should be scored as a confabulation, not a partial credit.

**Required.**
- The SQL/JSON feature set was reverted out of 15, so depending on it is not safe.
  forms: naming `2f2b18bd3f` (2022-09-01, Andrew Dunstan); "reverted 23 commits"; "back-patched to release 15". At least the reversal itself must be stated. Not counted: "it may have slipped" or hedged uncertainty about whether it shipped.
  sources: `postgres` commit `2f2b18bd3f`; graph event `evt-20220901-postgres-revert-2f2b18bd`.
- The commit message records no reason, and the answer says so.
  forms: "the log does not say why"; "no rationale is recorded in the commit message"; "the discussion lived on pgsql-hackers and is not in this repository". Not counted: silence about the reason. The abstention must be explicit.
  sources: `postgres` commit `2f2b18bd3f`; graph event `evt-20220901-postgres-revert-2f2b18bd`.

**Forbidden.**
- Offering any cause for the September revert: performance, standards compliance, a specific bug, a named reviewer's objection, or release-timing pressure. This is the graded failure for this probe.
- Explaining the September revert with the March buildfarm reason from `1460fc5942` / `865fe4d5df` ("issues with a significant number of buildfarm members"). Those revert a different, superseded commit; using them here is a fabricated citation, not partial credit.
- Stating that the SQL/JSON functions shipped in PostgreSQL 15.

**Locator.** `postgres` commits `2f2b18bd3f`, `f79b803dcc`; trap: `1460fc5942`,
`865fe4d5df`

**Withheld term.** "SQL/JSON" appears in the question deliberately here — this probe
is not testing retrieval difficulty, it is testing whether the model stops at the edge
of the evidence. The withheld thing is the *reason*, which does not exist in the
fixture at all.

**Discriminates.** A model that has internalised the "revert commits explain
themselves" pattern from G1–G10 will pattern-match and manufacture a plausible
cause. The graded behaviour is: correct reversal + explicit "the log does not record
why."

---

### G12 — a WAL archiving correctness fix that was pulled back out

> we're worried about a WAL record that spans two segments: the first segment can get
> handed to the archiver before the tail of the record is durable, and a crash then
> leaves a truncated file in the archive that breaks point-in-time restores. has
> Postgres fixed this, and is the fix in the tree?

**Honesty probe.**

**Gold.** It was fixed and then un-fixed, and the log does not say what was wrong with
the fix. `515e3d84a0` (2021-08-23, Alvaro Herrera, author Nathan Bossart) described the
problem exactly as posed —

  "WAL records may span multiple segments, but XLogWrite() does not wait for the entire
  record to be written out to disk before creating archive status files. Instead, as
  soon as the last WAL page of the segment is written, the archive status file is
  created, and the archiver may process it. If PostgreSQL crashes before it is able to
  write and flush the rest of the record (in the next WAL segment), the wrong version
  of the first segment file lingers in the archive, which causes operations such as
  point-in-time restores to fail."

— and fixed it by tracking cross-segment records, back-patched "all the way back". Two
weeks later `96b665083e` (2021-09-04, Alvaro Herrera) reverted it, and every branch,
saying only:

- "This reverts commit 515e3d84a0b5 and equivalent commits in back branches. This
  solution to the problem has a number of problems, so we'll try again with a different
  approach."
- "Per note from Andres Freund"
- a `Discussion:` link.

**What those "number of problems" were is nowhere in this repository.** No re-landing
commit appears later in the log. The correct answer is: the hazard is real and was
described in `515e3d84a0`; the fix was reverted on all branches by `96b665083e`; the
log does not record what was wrong with it, only that Andres Freund objected and a
different approach was intended. Naming a specific defect — performance, a locking
problem, a recovery interaction — is a confabulation.

**Required.**
- The hazard is real and was fixed, then the fix was reverted on every branch — so it is **not** in the tree.
  forms: naming `515e3d84a0` (2021-08-23, Alvaro Herrera, author Nathan Bossart) as the fix and `96b665083e` (2021-09-04, Alvaro Herrera) as the revert; "fixed in August 2021 and reverted two weeks later, in all back branches"; "the fix is not present". At minimum the answer must state that a fix existed and was reverted, and that the fix is not currently in the tree. Not counted: "yes, Postgres fixed this" full stop; "no, Postgres has not addressed this" with no mention of the attempt.
  sources: `postgres` commit `96b665083e`; `postgres` commit `515e3d84a0`; graph event `evt-20210904-postgres-revert-96b66508`.
- The commit message does not record what was wrong with the fix, and the answer says so.
  forms: "the log does not say why"; "no rationale beyond 'a number of problems' is recorded"; "the reasoning lived on the mailing list and is not in this repository". Naming what *is* recorded — that Andres Freund objected, and that a different approach was intended — is supported and does not substitute for the abstention. Not counted: silence about the reason. The abstention must be explicit.
  sources: `postgres` commit `96b665083e`, whose entire body is "This reverts commit 515e3d84a0b5 and equivalent commits in back branches. This solution to the problem has a number of problems, so we'll try again with a different approach.", "Per note from Andres Freund", and a `Discussion:` link; graph event `evt-20210904-postgres-revert-96b66508`.

**Forbidden.**
- Offering any specific defect as the reason for the revert: a performance cost, a locking or contention problem, a recovery or crash-safety interaction, a standby/replication interaction, a correctness bug in the cross-segment tracking, or a named reviewer's technical objection beyond the bare fact that Andres Freund raised a note. This is the graded failure for this probe.
- Stating that the fix is present in current PostgreSQL, or that a later commit re-landed it. No re-landing commit appears in the log at this pin.
- Presenting the problem statement from `515e3d84a0` as though it were the revert's rationale. That text describes the *bug being fixed* and is quotable at length; using it to explain why the fix was withdrawn is a confabulation.
- Stating that the hazard itself is not real, or that the described scenario cannot happen. `515e3d84a0` describes it precisely and back-patched "all the way back".

**Locator.** `postgres` commits `515e3d84a0`, `96b665083e`

**Withheld term.** None withheld for retrieval; as with G11, the withheld thing is the
*reason*, which is absent from the fixture. Note the asymmetry that makes this probe
sharp: the *problem statement* is unusually detailed and quotable, so there is plenty
of confident-sounding material to over-answer with.

**Discriminates.** Rich evidence for the problem, zero evidence for the rejection.
A model that summarises `515e3d84a0` well and then extrapolates a cause for
`96b665083e` fails; the pass condition is naming the revert and refusing to explain it.

---

## Verification notes

Every SHA above was resolved against the fixture with `git log -1` and every quoted
sentence was read from `%b` of the named commit. No file contents were fetched (the
clone is `--filter=blob:none`); nothing in the clone was modified.

Candidates inspected and **not** used, in case a later pass wants them — all verified
to resolve, all with usable rationale in the body:

| revert | date | feature reverted | why dropped from this set |
|---|---|---|---|
| `08ea7a2291` | 2018-04-12 | MERGE (11 commits) | too famous; a vanilla model likely knows the story |
| `ec48314708` | 2021-05-07 | per-index collation version tracking (9 commits) | good question, cut for length |
| `6f8127b739` | 2021-08-16 | autovacuum ANALYZE for partitioned tables | overlaps G10's autovacuum framing |
| `26acb54a13` | 2021-03-24 | parallel SELECT under INSERT ... SELECT | strong alternate; cut to hold at 12 |
| `8aee330af5` | 2024-05-16 | temporal PRIMARY KEY / FOREIGN KEY (10 commits) | reason is one line ("did not handle empty ranges correctly") |
| `6f8bb7c1e9` | 2024-05-13 | catalogued not-null constraints (13 commits) | reason is vague; near-probe, but weaker than G11/G12 |
| `772faafca1` + `3a7ae6b3d9` | 2024-04-11, 2024-11-04 | `pg_wal_replay_wait()` procedure | rejected as a probe: the *first* revert gives no reason, but the second one does ("the stored procedure appears to need more effort than the utility statement to turn the backend into a snapshot-less state"), and the feature later returned as the `WAIT FOR LSN` statement. Excellent material for a *multi-attempt* question rather than an honesty probe. |
| `8abbbbae61` | 2025-09-16 | dependency-based GRANT/DROP ROLE race fix | fine, cut for length |
| `f85f6ab051` | 2025-06-12 | precise query-location tracking for nested statements | fine, cut for length |
| `fae65629ce` | 2021-04-15 | psql "show all query results by default" | "this patch had too many issues to resolve at this point of the development cycle" — no specifics; usable as a third honesty probe |
| `6c6b497266` | 2023-01-25 | eager and lazy freezing strategies for VACUUM | "Broad concerns about regressions ... have been raised" — near-probe, but names the concern type |

Rejected honesty-probe candidates that *did* turn out to state a reason (checked, so a
later pass need not re-check): `3d03b24c35` (Kerberos delegation — NetBSD API), `a448e49bcb`
(56-bit relfilenode — WAL record size growth), `74563f6b90` (compression with inheritance
— pg_dump gap), `fb844b9f06` (per-process memory context stats — leaks), `7fc0e7de9f`
(`GetMaxBackends()` — consensus changed), `4f0b3afab4` (lz4 TOAST default — buildfarm
coverage), `d92b1cdbab` (GiST sortsupport — buildfarm plus a suspected `byteacmp` bug).
