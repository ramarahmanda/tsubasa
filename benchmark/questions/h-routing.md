# h-routing — single-document routing precision

Tests whether the captain navigates the graph **straight to the one document that holds the answer**, instead of grepping the fixture and reassembling. Every answer here lives in exactly one file; several live in extensionless `README` design docs that a `*.md` search never sees.
Passing looks like: the named file under `**Expected routing.**` is opened first (or first among content files), the answer is quoted from it with `file:line`, and no other repo is consulted.

---

### H1 — half-dead btree pages

> when postgres deletes an empty btree leaf page, there's an intermediate state before the page is actually gone. what is it called, and why does deletion need two stages instead of one?

**Gold.** The page is marked **half-dead**. Deletion is two-stage because the two things that must be undone — the downlink from the parent and the side-links to the siblings — cannot be changed in one atomic step. `access/nbtree/README:247` — "Deleting a leaf page is a two-stage process. In the first stage, the page is unlinked from its parent, and marked as half-dead." Line 256 gives the reason the marker exists: it "causes any subsequent searches to ignore it and move right (or left, in a backwards scan). This leaves the tree in a similar state as during a page split: the page has no downlink pointing to it, but it's still linked to its siblings." Line 279: "In the second-stage, the half-dead leaf page is unlinked from its siblings." Two constraints are also stated: postgres deletes only pages "completely empty of items" and "we *never* delete the rightmost page on a tree level" (line ~236-241).

**Required.**
- The intermediate state is called **half-dead**.
  forms: "half-dead"; "the page is marked half-dead"; `half-dead`. Not counted: "dead", "deleted", "pending deletion", "recyclable" — all are distinct states in the same document.
  sources: `postgres/src/backend/access/nbtree/README:247-248` ("Deleting a leaf page is a two-stage process. In the first stage, the page is unlinked from its parent, and marked as half-dead.").
- Deletion needs two stages because the two things that must be undone — the downlink from the parent and the side-links to the siblings — cannot be changed atomically together; the half-dead marker makes searches skip the page and move right while the tree is in that in-between state.
  forms: "stage one removes the parent downlink, stage two unlinks the siblings"; "the parent and the siblings can't be updated in one atomic step"; "the marker leaves the tree in the same shape as a page split — no downlink, still side-linked — and searches move right past it". Either the two-stage split of work or the search-safety role of the marker counts; both is better. Not counted: "for concurrency" or "for crash safety" with neither the staged work nor the search behaviour described.
  sources: `postgres/src/backend/access/nbtree/README:247-259` (stage one and what the half-dead marker does); same file :279-282 (stage two, "the half-dead leaf page is unlinked from its siblings").

**Forbidden.**
- Naming a different marker for the intermediate state, in particular "dead" or "deleted" — in this README a deleted page is the *end* state of stage two, not the intermediate one.
- Stating that PostgreSQL deletes partly-full btree pages or merges them. The README states deletion is considered "only when it's become completely empty of items" (`:235-236`), and that merging partly-full pages was judged impractical.
- Stating that any page can be deleted. "we *never* delete the rightmost page on a tree level" (`:239-240`).
- Answering from a different repository or from general B-tree literature with no citation into `postgres/src/backend/access/nbtree/README`.

**Locator.** `postgres/src/backend/access/nbtree/README:236-300`

**Expected routing.** `postgres/src/backend/access/nbtree/README`

---

### H2 — buffer ring sizes

> we're seeing shared_buffers get blown out by big COPY loads. does postgres already protect the cache for bulk writes, and if so how big is the window it uses?

**Gold.** Yes — the buffer ring (bulk access) strategy. `storage/buffer/README:243` — "For bulk writes we use a ring size of 16MB (but not more than 1/8th of shared_buffers). Smaller sizes have been shown to result in the COPY blocking too often for WAL flushes." It applies to exactly two operations: "Currently this applies only to COPY IN and CREATE TABLE AS SELECT." Contrast values in the same section: sequential scans use a **256KB** ring ("small enough to fit in L2 cache", line 219) and VACUUM's ring size is "controlled by the `vacuum_buffer_usage_limit` GUC" (line 234). Note the degradation case, also stated there: in a scan that dirties every page, "the ring strategy effectively degrades to the normal strategy."

**Required.**
- Yes: PostgreSQL already protects the cache for bulk writes, via the buffer ring (bulk access) replacement strategy.
  forms: "the buffer ring strategy"; "bulk access / BAS_BULKWRITE ring"; "a small ring of buffers is reused instead of running the clock sweep over the whole cache". Not counted: "shared_buffers protects itself"; naming `vacuum_buffer_usage_limit` as the bulk-write mechanism.
  sources: `postgres/src/backend/storage/buffer/README:206-217` ("Buffer Ring Replacement Strategy"); same file :240-243.
- The bulk-write ring is **16MB**, capped at 1/8th of `shared_buffers`.
  forms: "16MB"; "16MB, or 1/8th of shared_buffers, whichever is smaller". The cap may be omitted; the 16MB figure may not. Not counted: 256KB (that is the sequential-scan ring, `:219`); "controlled by `vacuum_buffer_usage_limit`" (that is VACUUM's ring, `:233-234`); a ring size given with no unit or as a buffer count.
  sources: `postgres/src/backend/storage/buffer/README:242-244` ("For bulk writes we use a ring size of 16MB (but not more than 1/8th of shared_buffers). Smaller sizes have been shown to result in the COPY blocking too often for WAL flushes.").

**Forbidden.**
- Giving 256KB, or VACUUM's `vacuum_buffer_usage_limit`, as the size of the window used for bulk writes.
- Stating that the bulk-write strategy applies to bulk `UPDATE`/`DELETE`, or to writes generally. The README scopes it: "Currently this applies only to COPY IN and CREATE TABLE AS SELECT" (`:240-241`), and raises seqscan UPDATE/DELETE only as an open question.
- Stating that PostgreSQL has no protection for `shared_buffers` against large COPY loads, or that this must be handled by the operator.
- Answering from `cloudnative-pg` documentation or from PostgreSQL user-facing documentation with no citation into `postgres/src/backend/storage/buffer/README`.

**Locator.** `postgres/src/backend/storage/buffer/README:205-250` ("Buffer Ring Replacement Strategy")

**Expected routing.** `postgres/src/backend/storage/buffer/README`

---

### H3 — walsender shutdown ordering

> on shutdown of a postgres primary, do walsenders get killed with the other backends? our standby seems to miss the last WAL.

**Gold.** No — postmaster deliberately terminates walsenders **later** than regular backends, so standbys receive the shutdown checkpoint. `replication/README:44-49` — "At shutdown, postmaster handles walsender processes differently from regular backends. It waits for regular backends to die before writing the shutdown checkpoint and terminating pgarch and other auxiliary processes, but that's not desirable for walsenders, because we want the standby servers to receive all the WAL, including the shutdown checkpoint, before the primary is shut down. Therefore postmaster treats walsenders like the pgarch process, and instructs them to terminate at the `PM_WAIT_XLOG_ARCHIVAL` phase, after all regular backends have died and checkpointer has issued the shutdown checkpoint." The same doc explains the signalling: a walsender "marks itself as a walsender process in the PMSignal array", and if postmaster misclassifies it "it will just terminate the walsender earlier in the shutdown phase."

**Required.**
- No: walsenders are deliberately **not** killed with the regular backends. Postmaster terminates them later.
  forms: "no, they are terminated later"; "postmaster treats walsenders like pgarch, not like regular backends"; "they outlive the regular backends on shutdown". Not counted: "it depends on the shutdown mode"; "yes, but they finish sending first".
  sources: `postgres/src/backend/replication/README:42-49` ("At shutdown, postmaster handles walsender processes differently from regular backends... Therefore postmaster treats walsenders like the pgarch process").
- The reason is that standbys must receive all the WAL including the shutdown checkpoint, so walsenders are told to terminate at the `PM_WAIT_XLOG_ARCHIVAL` phase — after regular backends have died and the checkpointer has written the shutdown checkpoint.
  forms: "so the standby gets the shutdown checkpoint"; naming the `PM_WAIT_XLOG_ARCHIVAL` phase; "they are shut down after the shutdown checkpoint is issued". The phase name is not required if the ordering and its purpose are stated. Not counted: "to avoid data loss" with neither the ordering nor the shutdown checkpoint mentioned.
  sources: `postgres/src/backend/replication/README:44-49`.

**Forbidden.**
- Stating that walsenders are killed together with, or before, regular backends on a clean shutdown.
- Stating that the shutdown checkpoint is written before regular backends exit. The README has it the other way round: postmaster "waits for regular backends to die before writing the shutdown checkpoint".
- Attributing missing tail WAL to this ordering as though the ordering were the bug. The documented ordering is the mitigation.
- Answering from `cloudnative-pg` documentation or from PostgreSQL user-facing documentation with no citation into `postgres/src/backend/replication/README`.

**Locator.** `postgres/src/backend/replication/README:40-70` ("Walsender IPC")

**Expected routing.** `postgres/src/backend/replication/README`

---

### H4 — disk-full does not fail over

> the WAL volume on our primary filled up. will cloudnative-pg fail over to a replica when that happens?

**Gold.** No — deliberately not. `docs/src/instance_manager.md:455-459` — "If the disk containing the WALs becomes full and no more WAL segments can be stored, PostgreSQL will stop working. CloudNativePG correctly detects this issue by verifying that there is enough space to store the next WAL segment, and avoids triggering a failover, which could complicate recovery. That allows a human administrator to address the root cause." The documented remedy is two steps, in order: "1. Expand the storage size of the full PVC 2. Increase the size in the `Cluster` resource to the same value" — after which "the Pod will restart and the cluster will become healthy". The doc also warns detection "relies on a storage class that accurately reports disk size and usage", which "may not be the case" on Kind or `csi-driver-host-path`.

**Required.**
- No: CloudNativePG detects the full WAL volume and deliberately does **not** trigger a failover.
  forms: "no"; "it avoids failing over on purpose"; "the operator detects the condition and holds off". Both halves must be present: the answer must say no *and* that this is deliberate, not a detection gap. Not counted: "it depends on the storage class"; "no, because the primary is still Ready".
  sources: `cloudnative-pg/docs/src/instance_manager.md:454-457` ("CloudNativePG correctly detects this issue by verifying that there is enough space to store the next WAL segment, and avoids triggering a failover, which could complicate recovery.").
- The stated reason is that a failover would complicate recovery; the intent is to leave the cluster for a human administrator to fix.
  forms: "failing over would complicate recovery"; "it leaves it to a human administrator to address the root cause"; "a replica would hit the same problem, so promoting one does not help". Not counted: "for safety" with no reason given.
  sources: `cloudnative-pg/docs/src/instance_manager.md:457-459` ("...which could complicate recovery. That allows a human administrator to address the root cause.").

**Forbidden.**
- Stating that CloudNativePG fails over to a replica when the WAL volume fills, or that it promotes and fences the primary.
- Stating that the disk-full condition goes undetected. Detection is explicit; the caveat in the doc is only that it "relies on a storage class that accurately reports disk size and usage", which "may not be the case" on Kind or `csi-driver-host-path` (`:448-451`). Reporting that caveat is supported.
- Presenting a remedy that contradicts the documented one. The documented sequence is to expand the PVC and then raise the size in the `Cluster` resource to match, after which the pod restarts and the cluster becomes healthy (`:464-468`); prescribing a manual failover, a cluster rebuild, or a restore instead is wrong.
- Answering from `failure_modes.md` or `failover.md`. Neither contains this behaviour.

**Locator.** `cloudnative-pg/docs/src/instance_manager.md:430-470` ("Disk Full Failure")

**Expected routing.** `cloudnative-pg/docs/src/instance_manager.md` — the routing trap is that `failure_modes.md` and `failover.md` both sound like the right home for this, and neither contains it. `failure_modes.md` in fact says the specific failure scenarios were removed from that page.

---

### H5 — the released-lease TTL

> after a clean cnpg switchover, how long does a replica wait before it can take the primary role?

**Gold.** It does not wait out the full lease. On a clean shutdown the former primary **releases** the lease and writes a short TTL: `docs/src/failover.md:140` — "`releasedLeaseDurationSeconds` | `1` | TTL written when the primary releases the lease on a clean shutdown." The doc states the asymmetry explicitly: "After an abrupt primary loss (the previous primary did not release the lease), a candidate must observe the lease unchanged for a full `leaseDurationSeconds` before it may take over... After a clean switchover (the previous primary released the lease), there is no such wait; the candidate simply notices the released lease on its next poll, so the hand-over latency is bounded by `retryPeriodSeconds`." Defaults in the same table: `leaseDurationSeconds` 15, `renewDeadlineSeconds` 10, `retryPeriodSeconds` 2. Two webhook-enforced invariants are also stated: `leaseDurationSeconds` > `renewDeadlineSeconds`, and `renewDeadlineSeconds` > `retryPeriodSeconds` × 1.2.

**Required.**
- After a clean switchover the replica does **not** wait out the full lease. The outgoing primary releases the lease, and the candidate takes over on its next poll.
  forms: "it does not wait `leaseDurationSeconds`"; "the lease is released on clean shutdown, so there is no take-over wait"; "hand-over latency is bounded by `retryPeriodSeconds`" (default `2`). Not counted: "15 seconds" or any answer that applies the full `leaseDurationSeconds` wait to the clean-switchover case.
  sources: `cloudnative-pg/docs/src/failover.md:163-173` (the note distinguishing abrupt loss from clean switchover); `cloudnative-pg/docs/src/failover.md:139` (`retryPeriodSeconds`, default `2`).
- The lease TTL written on a clean release is `releasedLeaseDurationSeconds`, default `1` second.
  forms: naming `releasedLeaseDurationSeconds`; "a 1-second TTL is written on release"; "1s". Either the parameter name or the value counts; both is better. Not counted: `leaseDurationSeconds` (15) or `renewDeadlineSeconds` (10) given as the released-lease TTL.
  sources: `cloudnative-pg/docs/src/failover.md:140` ("`releasedLeaseDurationSeconds` | `1` | TTL written when the primary releases the lease on a clean shutdown.").

**Forbidden.**
- Giving `leaseDurationSeconds` (default `15`) as the wait after a clean switchover. That wait applies to the *abrupt* case, where the previous primary did not release the lease; stating that contrast is supported.
- Stating that CloudNativePG has no lease mechanism, or that promotion is immediate with no bound at all.
- Inventing a default. The documented defaults are `leaseDurationSeconds` 15, `renewDeadlineSeconds` 10, `retryPeriodSeconds` 2, `releasedLeaseDurationSeconds` 1 (`:137-140`).
- Answering from `instance_manager.md`, from Kubernetes leader-election documentation, or from Patroni's behaviour, with no citation into `cloudnative-pg/docs/src/failover.md`.

**Locator.** `cloudnative-pg/docs/src/failover.md:56-182` ("Safe primary election" → "Tuning the primary lease")

**Expected routing.** `cloudnative-pg/docs/src/failover.md`

---

### H6 — etcd exit code 143

> our etcd pods exit with code 143 on node drain and our alerting flags it as a crash. is 143 an etcd error?

**Gold.** No. 143 is SIGTERM re-raised, not an etcd failure path. `Documentation/contributor-guide/exit_codes.md` — "etcd server explicitly uses three exit codes: **0** (success), **1** (general errors), and **2** (argument errors). When terminated by signals (SIGTERM/SIGINT) on Linux/Unix systems, the exit code depends on the process type: PID 1 processes exit with code 0, while non-PID 1 processes re-raise the signal, resulting in exit codes 143 (SIGTERM) or 130 (SIGINT)." Line 25 gives the table row: "SIGTERM | 143 (128 + 15)". The doc also notes the platform caveat: "This behavior is specific to Linux platform. On other platforms, etcd typically returns exit code 0 if it exits without error, and 1 otherwise."

**Required.**
- No: 143 is not an etcd error code. etcd explicitly uses only 0, 1 and 2.
  forms: "no"; "etcd's own exit codes are 0 (success), 1 (general errors), 2 (argument errors)"; "143 is not in etcd's set". Not counted: "no, it's fine" with no statement of what etcd's own codes are.
  sources: `etcd/Documentation/contributor-guide/exit_codes.md:5` ("etcd server explicitly uses three exit codes: **0** (success), **1** (general errors), and **2** (argument errors)").
- 143 is SIGTERM re-raised: a non-PID-1 process re-raises the signal, and the kernel reports 128 + 15.
  forms: "128 + 15"; "SIGTERM re-raised by the signal handler"; "the expected code for a clean SIGTERM shutdown of a non-PID-1 etcd on Linux". The arithmetic need not be spelled out if SIGTERM is named as the cause. Not counted: "it's a container runtime thing" with SIGTERM never named; treating 143 as an etcd shutdown *failure*.
  sources: `etcd/Documentation/contributor-guide/exit_codes.md:5`; same file :20 (the handler "re-raises the signal to the process itself ... which results in the kernel setting the exit code to 128 + signal number"); same file :25 (table row "SIGTERM | 143 (128 + 15)").

**Forbidden.**
- Presenting 143 as an etcd error, crash, or failure path, or mapping it to a named etcd error condition.
- Stating that any etcd process terminated by SIGTERM exits 143. The doc distinguishes by process type: PID 1 processes exit 0, non-PID-1 processes re-raise (`:5`, `:14`). Reporting that distinction is supported.
- Stating that this behaviour is universal across platforms. "This behavior is specific to Linux platform. On other platforms, etcd typically returns exit code 0 if it exits without error, and 1 otherwise." (`:18`)
- Answering from general Unix/container knowledge with no citation into `etcd/Documentation/contributor-guide/exit_codes.md`.

**Locator.** `etcd/Documentation/contributor-guide/exit_codes.md:1-27`

**Expected routing.** `etcd/Documentation/contributor-guide/exit_codes.md` — buried under `contributor-guide/`, which reads like process documentation rather than operational reference.
