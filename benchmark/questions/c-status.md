# C — Current status of a decision

Tests the anti-hallucination property: every answer is the literal `status:` string in a `kep.yaml` at the pinned commit, and every question is chosen so a model answering from pretraining will state a stale or invented status confidently.
A passing answer gives the exact recorded value, cites the `kep.yaml`, and does not hedge it into a narrative ("it's in beta and progressing") — hedging counts as a miss even when the direction is right.

All ten resolve against `kubernetes-enhancements/` at `e32008ea3ed16998fca89b72754bc7c598a07679`.

---

### C1 — dockershim removal

> what is the current status of removing dockershim from kubelet?

**Gold.** `implemented`. Title "Removing dockershim from kubelet", KEP 2221, owning-sig `sig-node`, `stage: stable`, `latest-milestone: "v1.24"`. The field changed from `implementable` to `implemented` in commit `9ddb2df4` on 2022-08-08.

**Required.**
- The recorded status of KEP 2221 is `implemented`.
  forms: the literal value `implemented`; `status: implemented`; "implemented (stable, v1.24)". Not counted: "done", "complete", "removal has landed" with the value never given.
  sources: `kubernetes-enhancements/keps/sig-node/2221-remove-dockershim/kep.yaml:8`; graph entity `adr-remove-dockershim`, key fact `status=implemented (kep)`; graph event `evt-adr-status-removing-dockershim-from-kubelet-9e1925d4`.

**Forbidden.**
- Giving the status as `deprecated`, `implementable`, "in progress" or "planned".
- Stating that dockershim removal is still pending or incomplete in this record.

**Locator.** `kubernetes-enhancements/keps/sig-node/2221-remove-dockershim/kep.yaml:8`

**Discriminates.** Vanilla may answer from pretraining; the raw status field is unambiguous.

**Trap.** Answering "deprecated" or "in progress" rather than the recorded status.

---

### C2 — dynamic resource allocation

> what is the current status of the dynamic resource allocation KEP?

**Gold.** `withdrawn`. Title "dynamic resource allocation" (lowercase in the file), KEP 3063, owning-sig `sig-node`, `stage: alpha`, `latest-milestone: "v1.32"`. It sat at `implementable` from 2022-06-23 until commit `be482d3e` on 2024-10-04 flipped it to `withdrawn`. The work continues under a *different* KEP: `keps/sig-node/4381-dra-structured-parameters/kep.yaml`, "DRA Structured Parameters", `status: implemented`, `stage: stable`.

**Required.**
- The recorded status of KEP 3063 is `withdrawn`.
  forms: the literal value `withdrawn`; `status: withdrawn`; "withdrawn in 1.32". Not counted: "superseded" or "replaced" alone, which are different recorded values elsewhere in this corpus.
  sources: `kubernetes-enhancements/keps/sig-node/3063-dynamic-resource-allocation/kep.yaml:9`; `kubernetes-enhancements/keps/sig-node/3063-dynamic-resource-allocation/README.md:159-160` ("In Kubernetes 1.32, this KEP has been **withdrawn** and all code related to it gets removed. #4381 continues."); graph entity `adr-dynamic-resource-allocation`, key fact `status=withdrawn (kep)`.

**Forbidden.**
- Giving KEP 3063's status as `alpha`, `beta`, `implementable` or `implemented`.
- Reporting KEP 4381's status (`implemented`, `stage: stable`) as the status of KEP 3063. Naming 4381 as the continuing work is supported (`keps/sig-node/4381-dra-structured-parameters/kep.yaml:9`, README.md:159-160) and is not forbidden.
- Stating that DRA work under KEP 3063 itself is ongoing.

**Locator.** `kubernetes-enhancements/keps/sig-node/3063-dynamic-resource-allocation/kep.yaml:9`

**Discriminates.** The single strongest anti-hallucination item in the set. Every model's training data describes DRA as an active, graduating feature, because it is — but *this* KEP number was withdrawn and the feature re-homed under KEP 4381. A captain reading the graph reports `withdrawn` for 3063 and can name 4381; a model answering from memory reports "alpha" or "beta" for 3063.

**Trap.** Answering `alpha`/`beta`/`implementable`, or conflating KEP 3063 with KEP 4381 and reporting 4381's status against 3063.

---

### C3 — dynamic kubelet configuration

> what is the status of the Dynamic Kubelet Configuration enhancement?

**Gold.** `removed`. Title "Dynamic Kubelet Configuration", KEP 281, owning-sig `sig-node`, `stage: removed`, `latest-milestone: "v1.26"`. The milestone block records `deprecated: "v1.22"` and `removed: "v1.24" # from kubelet, left on control plane for skew nodes support` with the trailing comment "# fully removed in v1.26". Status was `implemented (beta)` until commit `fa4113d9` on 2022-10-10.

**Required.**
- The recorded status of KEP 281 is `removed`.
  forms: the literal value `removed`; `status: removed`; "removed (stage: removed, v1.26)". Not counted: "gone", "no longer supported" with the value never given.
  sources: `kubernetes-enhancements/keps/sig-node/281-dynamic-kubelet-configuration/kep.yaml:7` (`stage: removed` at :22 corroborates); graph entity `adr-dynamic-kubelet-configuration`, key fact `status=removed (kep)`.

**Forbidden.**
- Giving the status as `deprecated`: that is a milestone key (`deprecated: "v1.22"`, kep.yaml:34), not the `status:` field.
- Giving the status as `implemented` or `implemented (beta)`: that was the value before commit `fa4113d9`, not the recorded one.
- Claiming the feature still ships in the kubelet. `removed: "v1.24" # from kubelet, left on control plane for skew nodes support` and `# fully removed in v1.26` (kep.yaml:35-36) are recorded, as is the beta-in-v1.11 / "Avoiding Permanent Beta" deprecation rationale (README.md:49,53-56); stating any of those is supported, not forbidden.

**Locator.** `kubernetes-enhancements/keps/sig-node/281-dynamic-kubelet-configuration/kep.yaml:7`

**Discriminates.** `removed` is not one of the seven values the KEP process defines (`0000-kep-process/README.md:151`). A captain quoting the file gets it right; a model normalising to the documented vocabulary gets it wrong.

**Trap.** Answering `deprecated` (that is the milestone, not the status) or `implemented`.

---

### C4 — discontiguous cluster CIDR

> what is the status of the MultiCIDRRangeAllocator / discontiguous cluster CIDR work?

**Gold.** `withdrawn`. Title "Enhanced NodeIPAM to support Discontiguous Cluster CIDR", KEP 2593, owning-sig `sig-network`, `stage: beta`, `latest-milestone: "v1.29"`, feature gate `MultiCIDRRangeAllocator` on `kube-controller-manager`. Status was `implementable` from 2021-09-09 until commit `19decda7` on 2023-09-29 set it to `withdrawn`.

**Required.**
- The recorded status of KEP 2593 is `withdrawn`.
  forms: the literal value `withdrawn`; `status: withdrawn`; "withdrawn, developed out of tree instead".
  sources: `kubernetes-enhancements/keps/sig-network/2593-multiple-cluster-cidrs/kep.yaml:7`; `kubernetes-enhancements/keps/sig-network/2593-multiple-cluster-cidrs/README.md:69` ("IMPORTANT: THIS KEP HAS BEEN WITHDRAWN AND THIS FEATURE WILL BE DEVELOPED OUT OF TREE"); graph entity `adr-multiple-cluster-cidrs`, key fact `status=withdrawn (kep)`.

**Forbidden.**
- Giving the status as `beta`: that is the `stage:` field (kep.yaml:16). Reporting `stage: beta` as the stage, alongside the status, is supported.
- Giving the status as `alpha`, `implementable`, or "alpha, targeted for GA".
- Claiming the in-tree `MultiCIDRRangeAllocator` work is still active or graduating.

**Locator.** `kubernetes-enhancements/keps/sig-network/2593-multiple-cluster-cidrs/kep.yaml:7`

**Discriminates.** `stage: beta` is still in the file, so a model skimming for maturity reports "beta". The `status:` field says the KEP was abandoned. The two fields disagree by design and the question asks for status.

**Trap.** Answering `beta` from the `stage:` field, or "alpha, targeted for GA".

---

### C5 — CSI migration for CephFS

> is the in-tree CephFS plugin still being migrated to CSI? what does the KEP say?

**Gold.** `withdrawn`. Title "In-tree Storage Plugin to CSI Migration - Ceph Cephfs", KEP 2924, owning-sig `sig-storage`, `stage: alpha`, `creation-date: 2022-01-27`, `last-updated: 2023-05-28`, `latest-milestone: "v1.28"`. Status was `implementable` until commit `40a9f8e5` on 2023-05-28.

**Required.**
- The recorded status of KEP 2924 is `withdrawn`, so the answer to "is it still being migrated" is no.
  forms: the literal value `withdrawn`; `status: withdrawn`; "no — the KEP was withdrawn". Both parts must be consistent: a bare "no" with no recorded value does not count, nor does the value with an answer that says migration continues.
  sources: `kubernetes-enhancements/keps/sig-storage/2924-csi-migration-cephfs/kep.yaml:17` (`last-updated: 2023-05-28` at :15); graph entity `adr-csi-migration-cephfs`, key fact `status=withdrawn (kep) as of 2023-05-28`.

**Forbidden.**
- Giving the status as `implemented`, `implementable`, or "GA in v1.2x" by analogy with the sibling CSI-migration KEPs.
- Claiming the CephFS migration is in progress, or that it reached beta or stable. Only `alpha: "v1.26"` is recorded (kep.yaml:32).
- Attributing a withdrawal reason to this KEP's README: the README records none.

**Locator.** `kubernetes-enhancements/keps/sig-storage/2924-csi-migration-cephfs/kep.yaml:17`

**Discriminates.** Its sibling CSI-migration KEPs in the same directory tree (1487 AWS, 1489 Cinder, 1490 Azuredisk, 1491 vSphere, 2589 Portworx) are all `implemented`/stable, so the family pattern predicts "done". CephFS is the one that was withdrawn.

**Trap.** Generalising from the sibling KEPs and answering `implemented` or "GA in 1.2x".

---

### C6 — removing kustomize from kubectl

> what happened to the proposal to deprecate and remove kustomize from kubectl?

**Gold.** `rejected`. Title "Deprecate and remove kustomize from kubectl", KEP 4706, owning-sig `sig-cli`, `creation-date: 2024-06-07`, `stage:` empty, `latest-milestone:` empty. It was `implementable` at creation and moved to `rejected` in commit `7ed06669` on 2024-11-28.

**Required.**
- The recorded status of KEP 4706 is `rejected`.
  forms: the literal value `rejected`; `status: rejected`; "the proposal was rejected / declined". Not counted: "it did not go ahead" with the value never given.
  sources: `kubernetes-enhancements/keps/sig-cli/4706-deprecate-and-remove-kustomize/kep.yaml:7`; graph entity `adr-deprecate-and-remove-kustomize`, key fact `status=rejected (kep)`.
- kustomize stays part of kubectl; the removal was not carried out.
  forms: "kustomize is retained in kubectl"; "the removal was declined"; quoting the README decision note.
  sources: `kubernetes-enhancements/keps/sig-cli/4706-deprecate-and-remove-kustomize/README.md:89-94` ("After a discussion in sig-architecture meeting on Oct 17th, 2024 ... it was decided not to pursue this topic further, and retain kustomize as part of kubectl ... widespread adoption of the tool by the community").

**Forbidden.**
- "Deprecated in 1.3x, removal planned/underway", or any claim that the deprecation stages (v1.31 warnings, v1.34 default-off, v1.36 removal) were adopted. They are the rejected proposal's plan (README.md:129-135, `KUBECTL_LEGACY_KUSTOMIZE` at README.md:161-162); describing them as the plan that was never executed is supported.
- Giving the status as `implementable`, `provisional` or `withdrawn`.

**Locator.** `kubernetes-enhancements/keps/sig-cli/4706-deprecate-and-remove-kustomize/kep.yaml:7`

**Discriminates.** Only three `kep.yaml` files in the corpus carry `status: rejected` (this one, `sig-cli/2229-kubectl-xdg-base-dir`, `sig-api-machinery/3037-client-go-alternative-services`). A model that has seen the proposal discussed will assume it is pending or landed.

**Trap.** Answering "deprecated in 1.3x, removal planned" — the removal was declined outright.

---

### C7 — declarative validation

> what is the status of the Declarative Validation KEP?

**Gold.** `superseded`. Title "Declarative Validation", KEP 4153, owning-sig `sig-api-machinery`, `stage: alpha`, `latest-milestone: "v1.29"`. It was `implementable` from 2023-09-28 until commit `6a5d411c` on 2025-02-13 set it to `superseded`.

**Required.**
- The recorded status of KEP 4153 is `superseded`, verbatim.
  forms: the literal value `superseded`; `status: superseded`. Not counted: `replaced`, which is the schema-legal neighbour and is not what the file says.
  sources: `kubernetes-enhancements/keps/sig-api-machinery/4153-declarative-validation/kep.yaml:7`; graph entity `adr-declarative-validation`, key fact `status=superseded (kep)`.

**Forbidden.**
- Giving the status as `replaced`, `deprecated` or `withdrawn`.
- Giving the status as `alpha`: that is the `stage:` field (kep.yaml:27).
- Reporting KEP 5073's status (`implemented`, `stage: stable`, `latest-milestone: "v1.36"`) as KEP 4153's. Naming 5073 as the successor is supported: it lists 4153 in `see-also` (`keps/sig-api-machinery/5073-declarative-validation-with-validation-gen/kep.yaml:18-19`).

**Locator.** `kubernetes-enhancements/keps/sig-api-machinery/4153-declarative-validation/kep.yaml:7`

**Discriminates.** `superseded` is not a legal KEP status — the process document (`0000-kep-process/README.md:151`) says the value must be one of `provisional`, `implementable`, `implemented`, `deferred`, `rejected`, `withdrawn`, `replaced`. It is the only occurrence of `superseded` in the corpus. A captain quoting the file reports it verbatim; a model normalising reports `replaced`.

**Trap.** Answering `replaced` (the schema-legal neighbour) or `alpha` (the `stage:` value).

---

### C8 — removing cgroup v1 support

> what is the status of removing cgroup v1 support from Kubernetes?

**Gold.** `implementable`. Title "Remove cgroup v1 support", KEP 5573, owning-sig `sig-node`, `creation-date: 2025-09-26`, `stage: beta`, `latest-milestone: "v1.35"`. The file has had one status value since it was created in commit `ebf755aa` on 2025-10-09.

**Required.**
- The recorded status of KEP 5573 is `implementable`.
  forms: the literal value `implementable`; `status: implementable`; "implementable, targeting beta in v1.35". Not counted: "planned", "approved but not started" with the value never given.
  sources: `kubernetes-enhancements/keps/sig-node/5573-remove-cgroup-v1/kep.yaml:8`; graph entity `adr-remove-cgroup-v1`, key fact `status=implementable (kep)`.

**Forbidden.**
- Giving the status as `implemented`, `provisional`, or "removed".
- Answering with KEP 4569 ("Move cgroup v1 in maintenance mode", `implemented`, stable in v1.31) as though it were the status of the removal work. Naming 4569 as the predecessor is supported: 5573 lists it in `see-also` (kep.yaml:21-23).
- Claiming cgroup v1 support has already been removed, or that a removal release is committed. `stage: beta` and `beta: "v1.35"` are recorded (kep.yaml:19,32); the README defers removal to no earlier than 1.38 with no timeline (README.md:90-91).

**Locator.** `kubernetes-enhancements/keps/sig-node/5573-remove-cgroup-v1/kep.yaml:8`

**Discriminates.** The KEP postdates most training corpora entirely. A model will either say it does not exist, or confuse it with the older cgroup v2 support KEP.

**Trap.** Answering `implemented`, or reporting the status of cgroup **v2** support instead.

---

### C9 — in-place update of pod resources

> what is the current status of in-place update of pod resources?

**Gold.** `implemented`. Title "In-place Update of Pod Resources", KEP 1287, owning-sig `sig-node`, `stage: "stable"`, `latest-milestone: "v1.35"`, `creation-date: 2018-11-06`. It was `implementable` for five years and only became `implemented` in commit `d47a8df4` on 2025-12-29.

**Required.**
- The recorded status of KEP 1287 is `implemented`.
  forms: the literal value `implemented`; `status: implemented`; "implemented, stable, GA in v1.35". Not counted: "GA" or "stable" alone with the `status:` value never given.
  sources: `kubernetes-enhancements/keps/sig-node/1287-in-place-update-pod-resources/kep.yaml:14` (`stage: "stable"` at :35, `stable: "v1.35"` at :42 corroborate); graph entity `adr-in-place-update-pod-resources`, key fact `status=implemented (kep)`.

**Forbidden.**
- Giving the current status as `alpha` (v1.27) or `beta` (v1.33): both are recorded milestones (kep.yaml:40-41), neither is the status.
- Giving the status as `implementable`, or claiming the feature has not graduated.

**Locator.** `kubernetes-enhancements/keps/sig-node/1287-in-place-update-pod-resources/kep.yaml:14`

**Discriminates.** This is the stale-pretraining case in the other direction: the feature was alpha for a long time and every model has memorised it as alpha or beta. The corpus records that it has since gone stable.

**Trap.** Answering `alpha` (v1.27) or `beta` (v1.33) — both were true, neither is current.

---

### C10 — node swap support

> what is the current status of node system swap support?

**Gold.** `implemented`. Title "Node system swap support", KEP 2400, owning-sig `sig-node`, `stage: stable`, `latest-milestone: "v1.34"`, `creation-date: 2021-04-06`. Status went `provisional` → `implementable` (2021-05-05) → `implemented` in commit `6f62c849` on 2025-07-08.

**Required.**
- The recorded status of KEP 2400 is `implemented`.
  forms: the literal value `implemented`; `status: implemented`; "implemented, stable in v1.34". Not counted: "GA" or "stable" alone with the `status:` value never given.
  sources: `kubernetes-enhancements/keps/sig-node/2400-node-swap/kep.yaml:13` (`stage: stable` at :25, `stable: "v1.34"` at :36 corroborate); graph entity `adr-node-swap`, key fact `status=implemented (kep)`.

**Forbidden.**
- Giving the current status as `beta`, or "beta since 1.30, GA target unknown". `beta: "v1.30"` is a recorded milestone (kep.yaml:35), not the status.
- Giving the status as `provisional` or `implementable`: both are superseded values from this KEP's history.
- Claiming swap support is not yet generally available.

**Locator.** `kubernetes-enhancements/keps/sig-node/2400-node-swap/kep.yaml:13`

**Discriminates.** Long-running beta feature that graduated recently; training data overwhelmingly says beta.

**Trap.** Answering `beta` or "beta since 1.30, GA target unknown".
