# x-cross-repo — joins across two or more repos

Tests whether the captain **joins evidence across repos and resolves the near-miss**, rather than stopping at the first plausibly-related artifact in the second repo. Every question here has a decoy: a related-but-different document that a sloppy join reads as the answer.
Passing looks like: both (or all three) repos are opened, the decoy is named and explicitly ruled out, and the answer states the split — "yes on side A, no on side B" — rather than collapsing to one side.

---

### X1 — StatefulSet PVC resizing

> cnpg avoids StatefulSets partly because of PVC limitations. has Kubernetes fixed that since?

**Gold.** No. `cloudnative-pg/docs/src/controller.md:31-38` "PVC resizing" still states the limitation: "This is a well known limitation of `StatefulSet`: it does not support resizing PVCs. This is inconvenient for a database. Resizing volumes requires convoluted workarounds. In contrast, CloudNativePG leverages the configured storage class to manage the underlying PVCs directly, and can handle PVC resizing if the storage class supports it." No KEP in `kubernetes-enhancements` covers StatefulSet PVC **resize**. The closest is `keps/sig-apps/1847-autoremove-statefulset-pvcs/kep.yaml` — `title: Auto delete PVCs created by StatefulSet`, `status: implemented`, `stage: stable`, `latest-milestone: "v1.32"`, feature gate `StatefulSetAutoDeletePVC` — which is about **deletion lifecycle**, not resizing. The six StatefulSet KEPs at this pin (`3335-statefulset-slice`, `961-maxunavailable-for-statefulset`, `1847-autoremove-statefulset-pvcs`, `3541-add-recreate-strategy-to-statefulset`, `4017-pod-index-label`, `2599-minreadyseconds-for-statefulsets`) contain none for resize. Separately, generic PVC expansion *is* available to cnpg precisely because it manages PVCs directly — `docs/src/storage.md:252-272` "Volume expansion".

**Required.**
- No, Kubernetes has not fixed it: no KEP in `kubernetes-enhancements` at this pin covers StatefulSet PVC **resize**.
  forms: "no"; "there is no KEP for it"; "the limitation stands". Not counted: "yes, it was fixed"; "unclear / possibly" with no search of the KEP set reflected.
  sources: the six StatefulSet KEPs present at this pin — `keps/sig-apps/1847-autoremove-statefulset-pvcs`, `keps/sig-apps/2599-minreadyseconds-for-statefulsets`, `keps/sig-apps/3335-statefulset-slice`, `keps/sig-apps/3541-add-recreate-strategy-to-statefulset`, `keps/sig-apps/4017-pod-index-label`, `keps/sig-apps/961-maxunavailable-for-statefulset` — none of which is about resizing.
- cnpg's documentation still states the limitation as current.
  forms: quoting or paraphrasing the "PVC resizing" section; "cnpg still records this as a live reason it avoids StatefulSets". Not counted: asserting the limitation with no cnpg citation.
  sources: `cloudnative-pg/docs/src/controller.md:30-38` ("## PVC resizing" — "This is a well known limitation of `StatefulSet`: it does not support resizing PVCs... In contrast, CloudNativePG leverages the configured storage class to manage the underlying PVCs directly, and can handle PVC resizing if the storage class supports it.").
- KEP 1847 is named and ruled out: it covers PVC **deletion lifecycle**, not resizing.
  forms: naming KEP 1847 / "Auto delete PVCs created by StatefulSet" and saying it is about auto-deletion rather than resize; "the closest KEP is about deleting PVCs, which is a different problem". Ruling out the decoy is required for this question — an answer that says "no" without engaging the nearest candidate does not count.
  sources: `kubernetes-enhancements/keps/sig-apps/1847-autoremove-statefulset-pvcs/kep.yaml:1` (`title: Auto delete PVCs created by StatefulSet`), `:10` (`status: implemented`), `:21` (`stage: stable`), `:23` (`latest-milestone: "v1.32"`), `:31` (gate `StatefulSetAutoDeletePVC`).

**Forbidden.**
- Answering "yes, fixed" on the strength of KEP 1847 being `implemented` and stable in v1.32. That KEP is about auto-deleting PVCs.
- Presenting cnpg's own volume expansion as evidence that StatefulSet gained PVC resize. cnpg can expand volumes precisely because it manages PVCs directly instead of using a StatefulSet (`cloudnative-pg/docs/src/storage.md:252` "## Volume expansion"; `controller.md:36-38`); citing it as cnpg capability is supported, citing it as a StatefulSet fix is not.
- Citing generic PVC-expansion KEPs ("Support for CSI volume resizing", "Recover from volume expansion failure") as evidence that the *StatefulSet* limitation was addressed.
- Answering from only one repository.

**Locator.** `cloudnative-pg/docs/src/controller.md:31-38` "PVC resizing"; `kubernetes-enhancements/keps/sig-apps/1847-autoremove-statefulset-pvcs/kep.yaml`; `cloudnative-pg/docs/src/storage.md:252-276`

**Repos required.** cloudnative-pg, kubernetes-enhancements

**Trap.** "StatefulSet + PVC + `status: implemented` + stable in v1.32" reads as "yes, fixed". The KEP covers auto-deletion, not resizing. A sloppy join also confuses cnpg's own volume-expansion support with StatefulSet gaining it.

---

### X2 — etcd learner mode via kubeadm

> can we use etcd learner mode when adding control plane nodes?

**Gold.** Split. **etcd supports learners; kubeadm does not drive them yet.** The etcd API surface is present at this pin: `etcd/Documentation/dev-guide/apispec/swagger/rpc.swagger.json` carries `isLearner` on member add and member list ("isLearner indicates if the added member is raft learner", "isLearner indicates if the member is raft learner") plus `MemberPromote` — "MemberPromote promotes a member from raft learner (non-voting) to raft voting member." The kubeadm side is not delivered: `keps/sig-cluster-lifecycle/kubeadm/3614-etcd-learner-mode/kep.yaml` — `title: Use etcd's learner mode in kubeadm`, `status: provisional`, `latest-milestone: "0.0"`, `stage: "beta"`, `last-updated: 2023-09-04`. So learner mode is usable by driving etcd directly, but `kubeadm join --control-plane` will not do it for you.

**Required.**
- etcd itself supports learners: the API exposes `isLearner` on member add and member list, and `MemberPromote` to turn a learner into a voting member.
  forms: "etcd supports raft learners"; naming `isLearner` and/or `MemberPromote`; "you can add a non-voting member and promote it". Not counted: "etcd has learner mode" with no API surface or document cited.
  sources: `etcd/Documentation/dev-guide/apispec/swagger/rpc.swagger.json:663` ("MemberPromote promotes a member from raft learner (non-voting) to raft voting member."); same file :2424 ("isLearner indicates if the added member is raft learner"), :2408 and :2846 ("isLearner indicates if the member is raft learner").
- kubeadm does not drive learners: the KEP for it is still `provisional` and has no delivery milestone.
  forms: "kubeadm doesn't do this yet"; naming KEP 3614 "Use etcd's learner mode in kubeadm" and its `status: provisional`; "`latest-milestone: 0.0`, so nothing has shipped". Not counted: "kubeadm may support it" with no status given.
  sources: `kubernetes-enhancements/keps/sig-cluster-lifecycle/kubeadm/3614-etcd-learner-mode/kep.yaml:1` (`title: Use etcd's learner mode in kubeadm`), `:8` (`status: provisional`), `:10` (`last-updated: 2023-09-04`), `:16` (`latest-milestone: "0.0"`).
- The answer states the split rather than collapsing to one side: yes if you drive etcd directly, no if you expect `kubeadm join --control-plane` to do it.
  forms: "yes on the etcd side, no on the kubeadm side"; "usable by talking to etcd yourself; kubeadm won't do it for you". Not counted: a flat "yes" or a flat "no".
  sources: the two sets of sources above, taken together.

**Forbidden.**
- Reading `stage: "beta"` in `3614-etcd-learner-mode/kep.yaml:17` as the achieved maturity. It is the target stage in the current dev cycle; `status: provisional` and `latest-milestone: "0.0"` are the fields that record what has actually happened.
- Presenting the presence of `isLearner` in etcd's API as evidence that kubeadm uses learner mode.
- Stating that etcd does not support learner mode, or that promotion requires an out-of-band procedure not in the API.
- Answering from only one repository.

**Locator.** `etcd/Documentation/dev-guide/apispec/swagger/rpc.swagger.json` (`isLearner`, `MemberPromote`); `kubernetes-enhancements/keps/sig-cluster-lifecycle/kubeadm/3614-etcd-learner-mode/kep.yaml`

**Repos required.** etcd, kubernetes-enhancements

**Trap.** Two decoys pointing opposite ways. `stage: "beta"` in the kep.yaml is the *target* stage, not the achieved one — `status: provisional` and `latest-milestone: "0.0"` are the fields that matter. And finding `isLearner` in etcd's API is not evidence that kubeadm uses it.

---

### X3 — etcd v3.5 inconsistency, blast radius on cnpg

> the etcd v3.5 data inconsistency: what is the blast radius for our CloudNativePG clusters?

**Gold.** Bounded — **control-plane state, not database contents.** The etcd bug corrupts the etcd DB relative to its WAL: `etcd/Documentation/postmortems/v3.5-data-inconsistency.md` — "Code refactor in v3.5.0 resulted in consistent index not being saved atomically. Independent crash could lead to committed transactions are not reflected on all the members." What is stored in etcd is Kubernetes API objects, which for cnpg means the `Cluster`, `Backup`, `Pooler`, `FailoverQuorum` CRs and the operator's view of them. PostgreSQL data is not in etcd: `cloudnative-pg/docs/src/storage.md:16-23,49-53` — "CloudNativePG doesn't use `StatefulSet` for managing data persistence. Rather, it manages PVCs directly," and the storage discussion is entirely about persistent volumes. The degraded mode is also documented: `cloudnative-pg/docs/src/faq.md:220-225` — "an outage of the operator does not necessarily imply a PostgreSQL database outage; it's like running a database without a DBA or system administrator," with the instance manager in each pod still keeping the server up, archiving WAL and exporting metrics. Also worth stating: the postmortem's own Impact section says "We are not aware any cases of users reporting a data corruption in production environment," and the trigger required "etcd crashing under high request load."

**Required.**
- The blast radius is control-plane state, not PostgreSQL data. What etcd holds is Kubernetes API objects; PostgreSQL data lives on persistent volumes that cnpg manages directly.
  forms: "your Postgres data is not in etcd"; "etcd holds the `Cluster`/`Backup`/`Pooler` CRs and the operator's view of them, not table data"; "the database is on PVCs, which are outside etcd". Both halves matter: naming what *is* at risk and stating that database contents are not. Not counted: "your data is safe" with no account of what etcd actually stores.
  sources: `etcd/Documentation/postmortems/v3.5-data-inconsistency.md:13` (the bug: "Code refactor in v3.5.0 resulted in consistent index not being saved atomically. Independent crash could lead to committed transactions are not reflected on all the members."); `cloudnative-pg/docs/src/storage.md:51-52` ("CloudNativePG doesn't use `StatefulSet` for managing data persistence. Rather, it manages PVCs directly").
- "Bounded", not "harmless": losing or reverting `Cluster` CRs can make the operator reconcile toward stale desired state, and the documented degraded mode is that PostgreSQL keeps serving while the operator is impaired.
  forms: "the operator's view of desired state can go stale"; quoting or paraphrasing "an outage of the operator does not necessarily imply a PostgreSQL database outage; it's like running a database without a DBA or system administrator"; "the instance manager in each pod keeps the server up, archives WAL and exports metrics". Not counted: declaring the impact nil.
  sources: `cloudnative-pg/docs/src/faq.md:223-225`; `cloudnative-pg/docs/src/faq.md:214-219` (the instance manager keeping the server up, including logging, metrics and continuous WAL archiving).

**Forbidden.**
- Stating that the etcd v3.5 inconsistency can corrupt PostgreSQL data, tables, or WAL.
- Stating that the impact is nil or that the bug is irrelevant to a cnpg deployment. The over-correction is as wrong as the panic.
- Overstating the observed impact. The postmortem records "We are not aware any cases of users reporting a data corruption in production environment" (`:78`) and that the trigger was "etcd crashing under high request load" (`:51`); reporting either is supported, asserting confirmed production data loss is not.
- Answering from only one repository.

**Locator.** `etcd/Documentation/postmortems/v3.5-data-inconsistency.md` (Summary, Trigger, Impact); `cloudnative-pg/docs/src/storage.md:16-53`; `cloudnative-pg/docs/src/faq.md:214-228`

**Repos required.** etcd, cloudnative-pg

**Trap.** "Data inconsistency" + "your database runs on Kubernetes" reads as "your Postgres data is at risk". It is not — but the *inverse* over-correction is also wrong: losing or reverting `Cluster` CRs can drive the operator to reconcile toward a stale desired state, so the answer is "bounded", not "harmless".

---

### X4 — corruption detection defaults, etcd's lesson applied to Postgres

> etcd's postmortem blames corruption detection being off by default. do our cnpg-managed Postgres clusters have the same gap?

**Gold.** Yes, by default — and it is **not** irreversible, which is the part most answers get wrong. cnpg leaves Postgres page checksums off: `cloudnative-pg/docs/src/cloudnative-pg.v1.md:363` — "`dataChecksums` _boolean_ | Whether the `-k` option should be passed to initdb, enabling checksums on data pages (**default: `false`**)", and `docs/src/bootstrap.md:263-266` — "When `dataChecksums` is set to `true`, CloudNativePG invokes the `-k` option in `initdb` to enable checksums on data pages and help detect corruption by the I/O system - that would otherwise be silent." That is the same shape as the etcd finding, `etcd/Documentation/postmortems/v3.5-data-inconsistency.md:92` — "No users enable data corruption detection as it is still an experimental feature introduced in v3.3" — which etcd remediated as a P0: "etcd detects data corruption by default ... DONE" (line 121). But Postgres at this pin is not initdb-only: `postgres/src/backend/storage/page/README:11-15` — "Current implementation requires this be enabled system-wide at initdb time, or by using the pg_checksums tool on an offline cluster. Checksums can also be enabled at runtime using `pg_enable_data_checksums()`, and disabled by using `pg_disable_data_checksums()`." Coverage caveat worth citing: page checksums and WAL CRCs are separate mechanisms — "the WAL checksum is a 32-bit CRC, whereas the page checksum is only 16-bits", and "WAL replay should not test the checksum of a full-page image."

**Required.**
- Yes, the same gap exists by default: cnpg leaves PostgreSQL data-page checksums **off**, because `dataChecksums` defaults to `false`.
  forms: "yes, checksums are off by default"; naming `dataChecksums` and its `false` default; "cnpg does not pass `-k` to initdb unless you ask". Not counted: "yes" with no setting named; claiming the default is `true`.
  sources: `cloudnative-pg/docs/src/cloudnative-pg.v1.md:363` ("`dataChecksums` _boolean_ | Whether the `-k` option should be passed to initdb, enabling checksums on data pages (default: `false`)"); `cloudnative-pg/docs/src/bootstrap.md:263-266`.
- It is **not** irreversible: at this pin PostgreSQL can enable checksums on an existing cluster at runtime, so the remedy is not a rebuild.
  forms: naming `pg_enable_data_checksums()`; "you can turn them on later, online"; "`pg_checksums` on an offline cluster, or the runtime functions". Naming either the runtime function or the offline `pg_checksums` route counts; asserting reversibility with neither named does not. Not counted: "you'd have to re-initdb"; "plan a rebuild / dump and restore".
  sources: `postgres/src/backend/storage/page/README:12-15` ("Current implementation requires this be enabled system-wide at initdb time, or by using the pg_checksums tool on an offline cluster. Checksums can also be enabled at runtime using pg_enable_data_checksums(), and disabled by using pg_disable_data_checksums().").

**Forbidden.**
- Stating that enabling data checksums requires rebuilding or re-initialising the cluster, or that it can only be set at `initdb` time. That is what cnpg's own documentation implies and what the third repository corrects. This is the graded failure for this question.
- Stating that cnpg enables page checksums by default.
- Presenting WAL CRCs as covering the same ground as page checksums. They are separate mechanisms: "the WAL checksum is a 32-bit CRC, whereas the page checksum is only 16-bits", and "WAL replay should not test the checksum of a full-page image" (`postgres/src/backend/storage/page/README:29,35-36`). Citing that distinction is supported.
- Misreporting the etcd side. The postmortem's finding is "No users enable data corruption detection as it is still an experimental feature introduced in v3.3" (`:92`), remediated as the P0 "etcd detects data corruption by default ... DONE" (`:121`); claiming etcd left it unfixed, or that detection was on by default in v3.5, is wrong.

**Locator.** `etcd/Documentation/postmortems/v3.5-data-inconsistency.md:92,121`; `cloudnative-pg/docs/src/cloudnative-pg.v1.md:363`; `cloudnative-pg/docs/src/bootstrap.md:243,263-266`; `postgres/src/backend/storage/page/README:5-36`

**Repos required.** etcd, cloudnative-pg, postgres

**Trap.** cnpg documents checksums *only* as an `initdb` bootstrap option, so a two-repo join (etcd + cnpg) lands on "yes, and you can never turn it on without rebuilding the cluster" — confidently wrong. The postgres design README is the third leg that changes the recommendation from "plan a rebuild" to "run `pg_enable_data_checksums()`". A join that stops at cnpg's API reference gets the diagnosis right and the remedy wrong.

---

### X5 — migrating etcd `--experimental-*` flags to feature gates

> we want to move our etcd config off `--experimental-*` flags onto `--feature-gates`. is that path settled?

**Gold.** Split. **etcd's own contributor guide already mandates feature gates for new work; the KEP that defines the mechanism is still `provisional`.** `etcd/Documentation/contributor-guide/features.md` instructs, for any new feature: "Add an Alpha feature gate" and "Any code changes or configuration flags related to the implementation of the feature must be gated with the feature gate e.g. `if cfg.ServerFeatureGate.Enabled(features.FeatureName)`", with graduation done by updating "the feature `PreRelease` stage in `server/features/etcd_features.go`". But `kubernetes-enhancements/keps/sig-etcd/4578-server-feature-gate/kep.yaml` — `title: Feature Gate in etcd` — is `status: provisional`. Its README states the problem being solved and the migration shape: existing flags suffer "no clear path to graduate flags, as removal of experimental prefix is a breaking change in command line," and migration of an existing `--experimental-feature-a` requires that "the `--experimental-feature-a` and `--feature-gates=FeatureA=true|false` flags coexist for at least 1 minor release" with "checks to make sure the two flags are not both set at start-up," and only then "deprecate the old `--experimental-feature-a` flag in the next minor release." Practical answer: new features, yes, use gates; wholesale migration of existing flags is a stated future goal ("migrate all existing `--experimental` feature flags to feature gate"), not a completed one — so pin config to whichever form the deployed etcd version actually accepts and expect a coexistence window.

**Required.**
- Not settled: the governing KEP, "Feature Gate in etcd" (KEP 4578, sig-etcd), is still `status: provisional`.
  forms: naming KEP 4578 and its `provisional` status; "the KEP that defines the mechanism has not reached `implementable`". Not counted: "it's a work in progress" with no status or KEP identified; reporting it as implemented or stable.
  sources: `kubernetes-enhancements/keps/sig-etcd/4578-server-feature-gate/kep.yaml:1` (`title: Feature Gate in etcd`), `:7` (`status: provisional`).
- Nevertheless etcd's own contributor guide already requires feature gates for **new** features, so the answer must not collapse to "don't use feature gates".
  forms: "new features must use a gate"; quoting "Add an Alpha feature gate" or the `cfg.ServerFeatureGate.Enabled(features.FeatureName)` requirement; "graduation is done by updating the `PreRelease` stage in `server/features/etcd_features.go`". Not counted: presenting the provisional KEP as meaning feature gates should be avoided.
  sources: `etcd/Documentation/contributor-guide/features.md:53` ("Add an Alpha [feature gate]"); same file :54 ("Any code changes or configuration flags related to the implementation of the feature must be gated with the feature gate e.g. `if cfg.ServerFeatureGate.Enabled(features.FeatureName)`"), :65 ("Update the feature `PreRelease` stage in `server/features/etcd_features.go`").
- Wholesale migration of existing `--experimental-*` flags is a stated goal, not a completed one, and the KEP prescribes a coexistence window.
  forms: "migrating all existing experimental flags is still a goal"; "the old flag and `--feature-gates` must coexist for at least one minor release, with a check that both are not set at start-up, before the old flag is deprecated"; "removing the `--experimental` prefix is a breaking change, which is why there is no clear graduation path today". Any one counts. Not counted: "migrate now, it's done".
  sources: `kubernetes-enhancements/keps/sig-etcd/4578-server-feature-gate/README.md:88` ("no clear path to graduate flags, as removal of experimental prefix is a breaking change in command line"), `:252` (coexist "for at least 1 minor release"), `:254` ("add checks to make sure the two flags are not both set at start-up"), `:103` and `:300` (migrating all existing `--experimental` flags listed as a goal).

**Forbidden.**
- Answering "yes, fully settled, migrate now" on the strength of `etcd/Documentation/contributor-guide/features.md` alone. It reads as ratified policy — it names the Go call and the source file — but the KEP that defines the mechanism is `provisional`.
- Answering "no, don't use feature gates" on the strength of `status: provisional` alone. That contradicts etcd's own requirement for new features.
- Stating that the `--experimental-*` flags have been removed, or that a release exists in which they no longer work.
- Answering from only one repository.

**Locator.** `etcd/Documentation/contributor-guide/features.md:54,65,74,83`; `kubernetes-enhancements/keps/sig-etcd/4578-server-feature-gate/kep.yaml:7`; `.../README.md:82-91,249-257,300-302`

**Repos required.** etcd, kubernetes-enhancements

**Trap.** etcd's `features.md` reads as settled, ratified policy — it names the exact Go call and source file — so a one-repo answer is "yes, fully settled, migrate now". The governing KEP has not even reached `implementable`. The mirror-image trap is reading `status: provisional` alone and answering "no, don't use feature gates," which contradicts etcd's own contributor requirements for new features.

---

### X6 — how fast is a dead node noticed

> our runbook says cnpg notices a dead node in about 40 seconds. still accurate on Kubernetes 1.32+?

**Gold.** No — budget **40 to 55 seconds**, and the 40s figure is stale. `cloudnative-pg/docs/src/failover.md:240-252` — the transition "is governed by `--node-monitor-grace-period` (default `40s` on Kubernetes 1.29-1.31, raised to `50s` in 1.32 and later): after that window the controller marks the node `Unknown` and, in the same monitoring pass, issues a patch per pod on that node to flip the `Ready` condition. In practice the operator observes the primary as unready about **40 to 55 seconds** after the node becomes unreachable (the grace period plus up to one `--node-monitor-period` poll, default `5s`)." Two further corrections the same section supplies: the `Ready` flip "is not subject to the rate limiters that throttle pod *eviction*" (`--node-eviction-rate` etc.), and pod eviction on the `node.kubernetes.io/unreachable` `NoExecute` taint (`300s` default) "does not hold up the operator's failover decision" — though full HA recovery is still "gated on the taint-based eviction actually deleting the pod." Add `.spec.failoverDelay` (default `0`) on top.

**Required.**
- No, the 40-second figure is stale. The budget is **40 to 55 seconds**.
  forms: "40 to 55 seconds"; "up to about 55s"; "roughly 50-55s on 1.32+". A range whose upper bound reaches the mid-50s counts. Not counted: "still about 40 seconds"; "about 50 seconds" given as an exact figure with no acknowledgement that 40s is now the low end; "5 minutes" (the pod-eviction timeout).
  sources: `cloudnative-pg/docs/src/failover.md:247-250` ("In practice the operator observes the primary as unready about **40 to 55 seconds** after the node becomes unreachable (the grace period plus up to one `--node-monitor-period` poll, default `5s`)").
- The reason is that `--node-monitor-grace-period` changed: default `40s` on Kubernetes 1.29-1.31, raised to `50s` in 1.32 and later.
  forms: naming `--node-monitor-grace-period` and the 40s-to-50s change at 1.32; "the Kubernetes default grace period went up in 1.32". Not counted: attributing the change to cnpg, or to `.spec.failoverDelay`, or giving a new figure with no cause.
  sources: `cloudnative-pg/docs/src/failover.md:243-246` ("With stock kube-controller-manager settings, the transition is governed by `--node-monitor-grace-period` (default `40s` on Kubernetes 1.29-1.31, raised to `50s` in 1.32 and later)").

**Forbidden.**
- Sourcing the current default from KEP-589 "Efficient Node Heartbeat". Its README states "Lack of NodeStatus update for `<node-monitor-grace-period>` (default: 40s)" (`kubernetes-enhancements/keps/sig-node/589-efficient-node-heartbeats/README.md:52`) and was authored in 2018; it is the stale number the question is about. No KEP at this pin records the 1.32 change.
- Giving `--pod-eviction-timeout` (KEP-589 README `:55`, default 5m) or the `node.kubernetes.io/unreachable` `NoExecute` toleration (`300s`) as the detection latency. Taint-based eviction "does not hold up the operator's failover decision"; noting that full HA recovery is still gated on the pod actually being deleted is supported.
- Stating that the `Ready`-condition flip is throttled by the node-eviction rate limiters. `failover.md:255-258` says it is not subject to `--node-eviction-rate`, `--secondary-node-eviction-rate` or `--unhealthy-zone-threshold`.
- Stating that `.spec.failoverDelay` is part of the detection window. It is additive on top, and its default is `0` (`cloudnative-pg/docs/src/failover.md:11`, `:252-253`).

**Locator.** `cloudnative-pg/docs/src/failover.md:236-270` ("Detection of node-level failures"), `failover.md:1-12` (`failoverDelay` default `0`); `kubernetes-enhancements/keps/sig-node/589-efficient-node-heartbeats/README.md:50-58`

**Repos required.** cloudnative-pg, kubernetes-enhancements

**Trap.** The only place in `kubernetes-enhancements` that documents this flag is KEP-589 "Efficient Node Heartbeat" (`status: implemented`, authored 2018), which says "Lack of NodeStatus update for `<node-monitor-grace-period>` (default: 40s) results in NodeController marking node as NotReady" and "Lack of NodeStatus updates for `<pod-eviction-timeout>` (default: 5m)". Both numbers are decoys: the grace period changed in 1.32 and `--pod-eviction-timeout` is not the mechanism cnpg reacts to. Going to "the Kubernetes repo" for a Kubernetes default gives the *older* answer than the downstream operator's docs. No KEP at this pin records the 1.32 change.

---

### X7 — huge pages for Postgres under cnpg

> we want `huge_pages` on for our cnpg Postgres clusters. is huge pages support in Kubernetes ready for this?

**Gold.** Kubernetes-side support is done; the blocker is elsewhere and is a **cnpg/Postgres/cgroup** problem, not a Kubernetes feature gap. Kubernetes: `keps/sig-node/1539-hugepages/kep.yaml` — `title: HugePages`, `status: implemented`, `stage: stable`, `milestone: alpha v1.18, beta v1.19, stable v1.22`; and `keps/sig-node/2053-downward-api-hugepages/kep.yaml` — `status: implementable`, `stage: stable`, `latest-milestone: "v1.27"`, gate `DownwardAPIHugePages`. The real hazard is documented only in cnpg: `cloudnative-pg/docs/src/troubleshooting.md:838-871` — "If your Cluster's initialization job crashes with a 'Bus error (core dumped) child process exited with exit code 135', you likely need to fix the Cluster hugepages settings. The reason is the incomplete support of hugepages in the cgroup v1 that should be fixed in v2," citing PostgreSQL "BUG #17757: Not honoring huge_pages setting during initdb causes DB crash in Kubernetes". The documented recipe sets both a memory request and a hugepages limit, e.g. `shared_buffers: "128MB"`, `requests.memory: "512Mi"`, `limits.hugepages-2Mi: "512Mi"`, with the warning: "you must have enough hugepages memory available to schedule every Pod in the Cluster."

**Required.**
- The Kubernetes side is done: HugePages support is `implemented` and stable.
  forms: naming KEP 1539 "HugePages", `status: implemented`, `stage: stable`, stable in v1.22; "Kubernetes support is GA and not the blocker". Naming the downward-API companion KEP 2053 is optional. Not counted: "Kubernetes support is still alpha/beta"; "there is no KEP for hugepages".
  sources: `kubernetes-enhancements/keps/sig-node/1539-hugepages/kep.yaml:1` (`title: HugePages`), `:24` (`status: implemented`), `:14` (`stage: stable`), `:17-20` (alpha v1.18, beta v1.19, stable v1.22); `kubernetes-enhancements/keps/sig-node/2053-downward-api-hugepages/kep.yaml:8` (`status: implementable`), `:22` (`stage: stable`), `:27` (`latest-milestone: "v1.27"`), `:38` (gate `DownwardAPIHugePages`).
- The real hazard is not a Kubernetes feature gap: on cgroup v1 nodes, hugepages support is incomplete and the cluster's `initdb` job crashes with "Bus error (core dumped) child process exited with exit code 135".
  forms: naming exit code 135 / the bus error; "cgroup v1's hugepages support is incomplete, fixed in v2"; citing PostgreSQL "BUG #17757". Any one counts; the answer must locate the blocker outside Kubernetes' feature status. Not counted: "there may be issues" with no failure mode named; blaming a Kubernetes feature gate.
  sources: `cloudnative-pg/docs/src/troubleshooting.md:839-846` ("If your Cluster's initialization job crashes with a 'Bus error (core dumped) child process exited with exit code 135', you likely need to fix the Cluster hugepages settings. The reason is the incomplete support of hugepages in the cgroup v1 that should be fixed in v2", citing PostgreSQL BUG #17757).
- The documented configuration sets a memory **request** alongside the hugepages limit, not a hugepages limit alone.
  forms: reproducing the recipe (`shared_buffers: "128MB"`, `requests.memory: "512Mi"`, `limits.hugepages-2Mi: "512Mi"`); "you need a normal memory request as well as the hugepages limit"; noting that every Pod in the Cluster must have hugepages memory available to be scheduled. Exact values are not required. Not counted: "just set `limits.hugepages-2Mi`".
  sources: `cloudnative-pg/docs/src/troubleshooting.md:857-867` (the YAML example), `:869-871` ("you must have enough hugepages memory available to schedule every Pod in the Cluster").

**Forbidden.**
- Answering "yes, fully supported, just set `limits.hugepages-2Mi`" from KEP-1539's `implemented`/stable status. That is the configuration that crashes `initdb` with exit code 135 on a cgroup v1 node.
- Stating that Kubernetes hugepages support is incomplete, alpha, or gated behind a feature gate that is off by default.
- Attributing the crash to a CloudNativePG bug, a PostgreSQL version incompatibility, or an operator defect rather than to cgroup v1's incomplete hugepages support.
- Answering from only one repository.

**Locator.** `kubernetes-enhancements/keps/sig-node/1539-hugepages/kep.yaml`; `kubernetes-enhancements/keps/sig-node/2053-downward-api-hugepages/kep.yaml`; `cloudnative-pg/docs/src/troubleshooting.md:838-871`

**Repos required.** kubernetes-enhancements, cloudnative-pg

**Trap.** KEP-1539 being `implemented`/stable since v1.22 makes the honest-looking answer "yes, fully supported, just set `limits.hugepages-2Mi`" — which is the configuration that crashes `initdb` with exit code 135 on a cgroup v1 node. The failure mode is recorded in a *troubleshooting* page in the other repo, not in any hugepages document, so a search anchored on "hugepages" never reaches it.
