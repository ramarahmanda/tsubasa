# D — What replaced what

Tests whether the captain can follow a supersession edge to a decision that *also exists in the corpus*, rather than reporting a dead end or inventing a successor.
A passing answer names the superseded decision, names the replacement by title and KEP number, and cites both `kep.yaml` files; naming only one endpoint, or pointing at an out-of-corpus URL, fails.

Only a minority of `replaces:` / `superseded-by:` values in this corpus resolve to another KEP directory — most point at archived design proposals, Google Docs or PR URLs. All four pairs below have **both** endpoints present at `e32008ea3ed16998fca89b72754bc7c598a07679`.

---

### D1 — topology-aware service routing

> the topology-aware service routing KEP is marked replaced. What replaced it?

**Gold.** KEP 2433, "Topology Aware Hints" (`status: implementable`, `stage: stable`, `latest-milestone: "v1.33"`, feature gate `TopologyAwareHints` on `kube-controller-manager` and `kube-proxy`). The superseded decision is KEP 536, "Topology-aware service routing", `status: replaced`, `creation-date: 2018-10-24`, `last-updated: 2021-05-12`.

**Required.**
- KEP 536 "Topology-aware service routing" was replaced by KEP 2433, "Topology Aware Hints".
  forms: the KEP number 2433; the title "Topology Aware Hints"; the path `keps/sig-network/2433-topology-aware-hints`. Number or title alone is enough; both is better. Not counted: "Topology Aware Routing" (the superseded KEP's own title restated as the successor); a generic "topology-aware routing was reworked" with no successor identified.
  sources: `kubernetes-enhancements/keps/sig-network/2433-topology-aware-hints/kep.yaml:26-27` (the `replaces:` block, `- "/keps/sig-network/536-topology-aware-routing"`); `kubernetes-enhancements/keps/sig-network/2433-topology-aware-hints/kep.yaml:1-2` (title and kep-number); graph entity `adr-topology-aware-routing`, field `superseded_by: adr-topology-aware-hints`.
- The superseded endpoint is KEP 536, recorded as `status: replaced`.
  forms: `status: replaced`; "KEP 536 is marked replaced". Not counted: giving 536's status as `withdrawn` or `deprecated`.
  sources: `kubernetes-enhancements/keps/sig-network/536-topology-aware-routing/kep.yaml:15`; graph entity `adr-topology-aware-routing`, key fact `status=replaced (kep) as of 2021-05-12 (kep.yaml:last-updated)`.

**Forbidden.**
- Answering that nothing replaced it, or that the record does not say. The edge exists, on 2433's `replaces:` list.
- Naming KEP 4444 "Service Traffic Distribution" as the replacement. 2433 lists it under `see-also:` (`keps/sig-network/2433-topology-aware-hints/kep.yaml:24`), not as a replacement of 536.
- Naming KEP 2004 "topology-aware subsetting", KEP 2030 "topology-aware proxying" or KEP 2086 "service internal traffic policy" as the replacement; those are also `see-also:` entries (kep.yaml:21-23).

**Locator.** superseded: `kubernetes-enhancements/keps/sig-network/536-topology-aware-routing/kep.yaml:15`; replacement: `kubernetes-enhancements/keps/sig-network/2433-topology-aware-hints/kep.yaml` — `replaces:` block reads `- "/keps/sig-network/536-topology-aware-routing"`

**Discriminates.** KEP 536 has **no** `superseded-by:` field at all. The edge exists only in the reverse direction, on 2433's `replaces:` list, so the answer requires a backlink traversal rather than reading the superseded file's own metadata.

**Trap.** Answering "nothing — the file doesn't say", or naming KEP 4444 "Service Traffic Distribution", which 2433 lists under `see-also:` but which does not replace 536.

---

### D2 — out-of-tree credential providers

> what replaced the Out-of-Tree Credential Providers KEP?

**Gold.** KEP 2133 (sig-node), "Kubelet Credential Providers", `status: implemented`, `stage: stable`, `latest-milestone: "v1.26"`, feature gate `KubeletCredentialProviders`. The superseded decision is KEP 2133 (sig-cloud-provider), "Out-of-Tree Credential Providers", whose status line is `status: replaced # replaced by keps/sig-node/2133-kubelet-credential-providers`, `creation-date: 2019-10-04`, `last-updated: 2019-12-10`.

**Required.**
- The sig-cloud-provider KEP "Out-of-Tree Credential Providers" was replaced by the sig-node KEP "Kubelet Credential Providers".
  forms: the title "Kubelet Credential Providers"; the path `keps/sig-node/2133-kubelet-credential-providers`; "the sig-node KEP, also numbered 2133". The KEP number alone (2133) does **not** identify the answer here, because both KEPs carry it — the SIG or the title must disambiguate. Not counted: "KEP 2133" with no SIG or title; "it moved to sig-node" with no successor named.
  sources: `kubernetes-enhancements/keps/sig-cloud-provider/2133-out-of-tree-credential-provider/kep.yaml:23` (`status: replaced # replaced by keps/sig-node/2133-kubelet-credential-providers` — the forward pointer is this end-of-line comment, not a `superseded-by:` field); `kubernetes-enhancements/keps/sig-node/2133-kubelet-credential-providers/kep.yaml:1-10` (title, kep-number, owning-sig, `status: implemented`).

**Forbidden.**
- Returning the sig-cloud-provider KEP itself as the answer, or reporting its `status: replaced` as the successor's status.
- Naming `/keps/sig-cloud-provider/20191004-out-of-tree-credential-providers.md` as the successor. That is the `replaces:` value on the sig-node KEP (kep.yaml:20-21), it points backwards, and the path does not exist at this pin.
- Stating that the record does not say what replaced it. The forward pointer is present in the YAML comment on the `status:` line.
- Stating the successor is still a proposal or unimplemented. It records `status: implemented`, `stage: stable`, `latest-milestone: "v1.26"` (kep.yaml:10,24,29).

**Locator.** superseded: `kubernetes-enhancements/keps/sig-cloud-provider/2133-out-of-tree-credential-provider/kep.yaml:23`; replacement: `kubernetes-enhancements/keps/sig-node/2133-kubelet-credential-providers/kep.yaml`

**Discriminates.** Both KEPs carry the **same number, 2133**, in different SIG directories — a number-keyed lookup collides. The forward pointer is buried in a YAML end-of-line comment on the `status:` line, not in a `superseded-by:` field, so a naive schema-only parse misses it. The replacement's own `replaces:` points at the *pre-directory* path `/keps/sig-cloud-provider/20191004-out-of-tree-credential-providers.md`, which no longer exists, so the forward edge is the only one that resolves.

**Trap.** Returning the sig-cloud-provider KEP as the answer to "what is KEP 2133", or reporting the dead `20191004-...md` path as the successor.

---

### D3 — KMS observability

> the KMS Observability KEP is replaced. By what?

**Gold.** KEP 3299, "KMS v2 Improvements", `status: implemented`, `stage: stable`, `latest-milestone: "v1.29"`, feature gate `KMSv2` on `kube-apiserver`, milestones alpha v1.25 / beta v1.27 / stable v1.29. The superseded decision is KEP 3130, "KMS Observability", `status: replaced`, `stage: alpha`, `latest-milestone: "v1.24"`, feature gate `KMSUID`.

**Required.**
- KEP 3130 "KMS Observability" was replaced by KEP 3299, "KMS v2 Improvements".
  forms: the KEP number 3299; the title "KMS v2 Improvements"; the path `keps/sig-auth/3299-kms-v2-improvements`. Number or title alone is enough. Not counted: "KMS v2" with no KEP number and no KEP title — the question asks which decision replaced it, not which feature; naming another sig-auth KMS KEP.
  sources: `kubernetes-enhancements/keps/sig-auth/3299-kms-v2-improvements/kep.yaml:15-16` (the `replaces:` block, `- "/keps/sig-auth/3130-kms-observability"`); same file :1-2 (title and kep-number); graph entity `adr-kms-observability`, field `superseded_by: adr-kms-v2-improvements`.
- KEP 3130's own file records `status: replaced` and never names the successor; the edge resolves only from 3299's side.
  forms: "3130 says `replaced` but does not say by whom"; "the pointer is on the successor's `replaces:` list"; simply giving 3130's status as `replaced` also counts. Not counted: claiming 3130 carries a `superseded-by:` field.
  sources: `kubernetes-enhancements/keps/sig-auth/3130-kms-observability/kep.yaml:8` (`status: replaced`, with no `superseded-by:` anywhere in the file).

**Forbidden.**
- Answering that the record does not identify a successor.
- Reporting KEP 3130's status (`replaced`) or its `stage: alpha` / `latest-milestone: "v1.24"` / gate `KMSUID` as belonging to KEP 3299.
- Naming a KMS KEP other than 3299 as the replacement, or naming 3299's own predecessors in place of the edge asked for.

**Locator.** superseded: `kubernetes-enhancements/keps/sig-auth/3130-kms-observability/kep.yaml:8`; replacement: `kubernetes-enhancements/keps/sig-auth/3299-kms-v2-improvements/kep.yaml` — `replaces:` reads `- "/keps/sig-auth/3130-kms-observability"`

**Discriminates.** Again a reverse-only edge: 3130 records `status: replaced` but never says by whom. The successor is the only file in the corpus pointing back at it, and it sits in the same SIG directory alongside several other KMS KEPs.

**Trap.** Naming KEP 3299's own precursor, or answering with a general "KMS v2" without a KEP number — the question is about a specific edge.

---

### D4 — service account token volumes

> what replaced the Service Account Token Volumes KEP, and which SIG owns the replacement?

**Gold.** KEP 1205, "Bound Service Account Tokens", owned by **sig-auth**, `status: implemented`, `stage: stable`, `latest-milestone: "v1.22"`, milestones alpha v1.13 / beta v1.21 / stable v1.22, feature gates `TokenRequest` and `TokenRequestProjection`. The superseded decision is KEP 2451, "Service Account Token Volumes", owned by **sig-storage**, `status: replaced`, `creation-date: 2018-05-15`, `last-updated: 2020-04-29`.

**Required.**
- KEP 2451 "Service Account Token Volumes" was replaced by KEP 1205, "Bound Service Account Tokens".
  forms: the KEP number 1205; the title "Bound Service Account Tokens"; the path `keps/sig-auth/1205-bound-service-account-tokens`. Number or title alone is enough. Not counted: "projected service account tokens" or "TokenRequest" as the successor without the KEP being identified.
  sources: `kubernetes-enhancements/keps/sig-auth/1205-bound-service-account-tokens/kep.yaml:17-18` (the `replaces:` block, `- "/keps/sig-storage/2451-service-account-token-volumes"`); same file :1-2 (title and kep-number); graph entity `adr-service-account-token-volumes`, field `superseded_by: adr-bound-service-account-tokens`.
- The replacement is owned by **sig-auth**, while the superseded KEP is owned by sig-storage.
  forms: "sig-auth"; "SIG Auth owns 1205"; naming the successor's path under `keps/sig-auth/`. Not counted: attributing the successor to sig-storage; naming a participating SIG in place of the owner.
  sources: `kubernetes-enhancements/keps/sig-auth/1205-bound-service-account-tokens/kep.yaml:6` (`owning-sig: sig-auth`); `kubernetes-enhancements/keps/sig-storage/2451-service-account-token-volumes/kep.yaml:8` (`owning-sig: sig-storage`), `:19` (`status: replaced`).

**Forbidden.**
- Naming sig-storage as the owner of the replacement, on the grounds that the superseded KEP lives there. sig-storage is only a `participating-sig` on 1205 (kep.yaml:7-9).
- Asserting that the successor must carry a higher KEP number, or rejecting 1205 because 1205 < 2451.
- Answering that no successor is recorded.

**Locator.** superseded: `kubernetes-enhancements/keps/sig-storage/2451-service-account-token-volumes/kep.yaml:19`; replacement: `kubernetes-enhancements/keps/sig-auth/1205-bound-service-account-tokens/kep.yaml` — `replaces:` reads `- "/keps/sig-storage/2451-service-account-token-volumes"`

**Discriminates.** The edge crosses SIG directories *and* runs backwards in KEP number (2451 → 1205), so neither directory locality nor numeric ordering helps. The corpus is the only way to get it.

**Trap.** Assuming the successor has a higher number, or attributing the replacement to sig-storage because the superseded KEP lives there.
