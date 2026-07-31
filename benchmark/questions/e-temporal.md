# E — Status as of a past date

Tests `tsubasa query "…" --as-of DATE`: whether the captain can report the state of the graph *as it was known then* instead of collapsing to the present.
A passing answer gives the historical value, states that it differs from today's, and names both; returning today's status, or refusing because "the KEP process doesn't record history", fails.

Each item was verified by resolving the last commit touching the `kep.yaml` at or before the as-of date, from the pinned tip `e32008ea3ed16998fca89b72754bc7c598a07679`, and reading the `status:` field in that blob. Commit SHAs are given so the run can be re-checked with `git show <sha>:<path>`.

---

### E1 — dynamic resource allocation, as of 2024-01-01

> what was the status of the dynamic resource allocation KEP as of 2024-01-01?

**Gold.** `implementable`. Today it is `withdrawn`. KEP 3063, `creation-date: 2021-05-17`. The governing blob on 2024-01-01 is commit `b9159cf8` (2023-06-15); the status became `withdrawn` only in commit `be482d3e` on 2024-10-04.

**Required.**
- On 2024-01-01 the recorded status of KEP 3063 was `implementable`.
  forms: the literal value `implementable`; "`status: implementable` as of that date". Not counted: "approved / in progress / being worked on" with the recorded value never given.
  sources: the blob governing that date is commit `b9159cf8` (2023-06-15), `git show b9159cf8:keps/sig-node/3063-dynamic-resource-allocation/kep.yaml` → `status: implementable`; the value changed only in commit `be482d3e` (2024-10-04, "Classic DRA: withdraw the KEP").
- Today's value is different — `withdrawn` — and the answer distinguishes the two.
  forms: naming today's value as `withdrawn` alongside the historical one; "it was `implementable` then and is `withdrawn` now". Not counted: giving only one value; giving both without saying which belongs to which date.
  sources: `kubernetes-enhancements/keps/sig-node/3063-dynamic-resource-allocation/kep.yaml:9` (`status: withdrawn` at the pinned tip); graph entity `adr-dynamic-resource-allocation`, key fact `status=withdrawn (kep)`.

**Forbidden.**
- Giving `withdrawn` as the 2024-01-01 value. The withdrawal is nine months later than the as-of date.
- Giving `alpha` or `beta` as the status for either date. `alpha` is the `stage:` field (kep.yaml:22), which did not change across this window.
- Refusing on the grounds that the KEP process records no history, or that only the current status is knowable.

**Locator.** `tsubasa query "status of dynamic resource allocation KEP" --as-of 2024-01-01`; `kubernetes-enhancements/keps/sig-node/3063-dynamic-resource-allocation/kep.yaml:9`

**Discriminates.** The as-of answer and the present-day answer are different words, and the present-day answer (`withdrawn`, category C2) is itself the counter-intuitive one. A system that ignores `--as-of` produces `withdrawn` here and is caught.

**Trap.** Answering `withdrawn` (today's value) or `alpha` (the `stage:` field, which was already `alpha` and did not change).

---

### E2 — sidecar containers, as of 2022-06-01

> what was the status of the Sidecar Containers KEP as of 2022-06-01?

**Gold.** `provisional`. Today it is `implemented`. KEP 753, `creation-date: 2018-05-14`, `last-updated: 2025-01-23`. The governing blob on 2022-06-01 is commit `7eef794b` (2020-12-17); the file then went `implementeable` (sic, a typo committed on 2023-02-09 in `0dc6db75`), corrected to `implementable` on 2023-02-13 in `78d2f361`, and finally `implemented` on 2025-01-23 in `4a646018`.

**Required.**
- On 2022-06-01 the recorded status of KEP 753 was `provisional`.
  forms: the literal value `provisional`; "`status: provisional` at that date". Not counted: "proposed / under discussion / not yet approved" with the recorded value never given.
  sources: the blob governing that date is commit `7eef794b` (2020-12-17, "Migrate some node KEPs to new template"), `git show 7eef794b:keps/sig-node/753-sidecar-containers/kep.yaml` → `status: provisional`.
- Today's value is different — `implemented` — and the answer distinguishes the two.
  forms: naming today's value as `implemented` alongside the historical one; "provisional then, implemented now". Not counted: giving only one value, or both without pairing each to its date.
  sources: `kubernetes-enhancements/keps/sig-node/753-sidecar-containers/kep.yaml:12`; graph entity `adr-sidecar-containers`, key fact `status=implemented (kep) as of 2025-01-23 (kep.yaml:last-updated)`.

**Forbidden.**
- Giving `implemented` (today's value), `implementable`, or the misspelled `implementeable` as the 2022-06-01 value. All three post-date it: the file went `implementeable` in commit `0dc6db75` (2023-02-09), was corrected to `implementable` in `78d2f361` (2023-02-13), and became `implemented` in `4a646018` (2025-01-23).
- Giving `alpha`, `beta` or "reopened for 1.28" as the recorded status at either date; those are release narrative, not the `status:` field.
- Refusing on the grounds that historical status is not recorded.

**Locator.** `tsubasa query "status of the Sidecar Containers KEP" --as-of 2022-06-01`; `kubernetes-enhancements/keps/sig-node/753-sidecar-containers/kep.yaml:12`

**Discriminates.** Four distinct status strings over the file's life, one of them misspelled. Correct as-of resolution has to pick the right one of four, and the misspelled intermediate state is a good check that the captain reports the literal recorded value rather than a normalised one.

**Trap.** Answering `implemented` (today), or `alpha`/"KEP 753 was reopened in 1.28" from pretraining narrative rather than the recorded field.

---

### E3 — dynamic kubelet configuration, as of 2022-01-01

> what was the status of Dynamic Kubelet Configuration as of 2022-01-01?

**Gold.** `implemented (beta)` — that exact string, parentheses included. Today it is `removed`. KEP 281, `creation-date: 2017-04-26`. The governing blob on 2022-01-01 is commit `e324e845` (2021-05-13); the status became `removed` in commit `fa4113d9` on 2022-10-10. The milestone block already recorded `deprecated: "v1.22"` at the time.

**Required.**
- On 2022-01-01 the recorded status of KEP 281 was the literal string `implemented (beta)`, parentheses included.
  forms: `implemented (beta)`; `status: implemented (beta)`. Not counted: `implemented` alone, or `beta` alone — the recorded value is the whole string and neither half is the value; "it was in beta" with no recorded value quoted.
  sources: the blob governing that date is commit `e324e845` (2021-05-13, "deprecate Dynamic Kubelet Config feature"), `git show e324e845:keps/sig-node/281-dynamic-kubelet-configuration/kep.yaml` → `status: implemented (beta)`.
- Today's value is different — `removed` — and the answer distinguishes the two.
  forms: naming today's value as `removed` alongside the historical one; "`implemented (beta)` then, `removed` now". Not counted: giving only one value, or both without pairing each to its date.
  sources: `kubernetes-enhancements/keps/sig-node/281-dynamic-kubelet-configuration/kep.yaml:7` (changed by commit `fa4113d9`, 2022-10-10); graph entity `adr-dynamic-kubelet-configuration`, key fact `status=removed (kep)`.

**Forbidden.**
- Giving `deprecated` as the 2022-01-01 status. `deprecated: "v1.22"` is a milestone key in the same file (kep.yaml:34), not the `status:` field.
- Giving `removed` (today's value) as the 2022-01-01 value, or `implemented (beta)` as today's.
- Normalising either value into the legal KEP vocabulary — reporting `implemented` for the historical value or `deprecated` for the current one. Neither recorded value is in the list at `keps/sig-architecture/0000-kep-process/README.md:151`, and both must be quoted as written.

**Locator.** `tsubasa query "status of Dynamic Kubelet Configuration" --as-of 2022-01-01`; `kubernetes-enhancements/keps/sig-node/281-dynamic-kubelet-configuration/kep.yaml:7`

**Discriminates.** Neither the historical value (`implemented (beta)`) nor the current one (`removed`) is a legal KEP status. Both must be quoted verbatim, and they must not be swapped.

**Trap.** Answering `deprecated` — true of the *milestone* at that date, not of the `status:` field — or `removed` (today's value).

---

### E4 — in-place pod resize, as of 2025-06-01

> what was the status of in-place update of pod resources as of 2025-06-01?

**Gold.** `implementable`. Today it is `implemented`. KEP 1287, `creation-date: 2018-11-06`. The governing blob on 2025-06-01 is commit `ea7c223a` (2025-05-01); the status became `implemented` in commit `d47a8df4` on 2025-12-29.

**Required.**
- On 2025-06-01 the recorded status of KEP 1287 was `implementable`.
  forms: the literal value `implementable`; "`status: implementable` at that date". Not counted: "approved but not shipped" with the recorded value never given.
  sources: the blob governing that date is commit `ea7c223a` (2025-05-01, "KEP-1287: Priority of Resize Requests"), `git show ea7c223a:keps/sig-node/1287-in-place-update-pod-resources/kep.yaml` → `status: implementable`.
- Today's value is different — `implemented` — and the answer distinguishes the two.
  forms: naming today's value as `implemented` alongside the historical one; "implementable then, implemented now"; adding "GA in v1.35" is supported but not required.
  sources: `kubernetes-enhancements/keps/sig-node/1287-in-place-update-pod-resources/kep.yaml:14` (set by commit `d47a8df4`, 2025-12-29, "mark in-place pod resize as implemented"); graph entity `adr-in-place-update-pod-resources`, key fact `status=implemented (kep)`.

**Forbidden.**
- Giving `beta` as the status at either date. `beta: "v1.33"` is a milestone, and `stage:` at the pinned tip reads `"stable"` (kep.yaml:35), so `beta` is neither field's current value.
- Giving `implemented` as the 2025-06-01 value; that change is seven months later.
- Giving `implementable` as today's value.

**Locator.** `tsubasa query "status of in-place update of pod resources" --as-of 2025-06-01`; `kubernetes-enhancements/keps/sig-node/1287-in-place-update-pod-resources/kep.yaml:14`

**Discriminates.** The change is recent (Dec 2025) and inside the window where a model's memory is confidently wrong in *both* directions — it will say "beta" for 2025 and "beta" for today. The corpus says `implementable` then and `implemented` now.

**Trap.** Answering `beta` for either date; `beta` is the release-maturity story, not the recorded status, and the `stage:` field at the pinned tip reads `"stable"`.

---

### E5 — declarative validation, as of 2024-06-01

> what was the status of the Declarative Validation KEP as of 2024-06-01?

**Gold.** `implementable`. Today it is `superseded`. KEP 4153, `creation-date: 2023-08-20`. The governing blob on 2024-06-01 is commit `3801dda4` (2023-09-28) — the file's first commit; the status became `superseded` in commit `6a5d411c` on 2025-02-13.

**Required.**
- On 2024-06-01 the recorded status of KEP 4153 was `implementable`.
  forms: the literal value `implementable`; "`status: implementable` at that date"; noting it had carried that value since the file was created. Not counted: "approved" or "in progress" with the recorded value never given.
  sources: the blob governing that date is commit `3801dda4` (2023-09-28, "KEP-4153: Declarative Validation") — the file's first commit — `git show 3801dda4:keps/sig-api-machinery/4153-declarative-validation/kep.yaml` → `status: implementable`.
- Today's value is different — `superseded` — and the answer distinguishes the two.
  forms: naming today's value as `superseded` alongside the historical one; "implementable then, superseded now". Not counted: giving only one value; giving today's value as `replaced`, which is the schema-legal neighbour and is not what the file says.
  sources: `kubernetes-enhancements/keps/sig-api-machinery/4153-declarative-validation/kep.yaml:7` (set by commit `6a5d411c`, 2025-02-13); graph entity `adr-declarative-validation`, key fact `status=superseded (kep)`.

**Forbidden.**
- Giving `superseded` or `replaced` as the 2024-06-01 value; the change is over eight months later.
- Giving `alpha` as the status at either date. That is the `stage:` field (kep.yaml:27).
- Refusing because the as-of date lands on the file's first commit, or reporting "no record before 2025".

**Locator.** `tsubasa query "status of the Declarative Validation KEP" --as-of 2024-06-01`; `kubernetes-enhancements/keps/sig-api-machinery/4153-declarative-validation/kep.yaml:7`

**Discriminates.** As-of resolution lands on the file's *first* commit, so the captain must handle "no change since creation" without falling through to HEAD.

**Trap.** Answering `superseded` (today) or `replaced` (the schema-legal normalisation of today's value).

---

### E6 — discontiguous cluster CIDR, as of 2023-01-01

> what was the status of the discontiguous cluster CIDR / MultiCIDRRangeAllocator KEP as of 2023-01-01?

**Gold.** `implementable`. Today it is `withdrawn`. KEP 2593, `creation-date: 2021-03-22`. The governing blob on 2023-01-01 is commit `44828bde` (2022-10-06), where `stage:` also still read `alpha`; the status became `withdrawn` in commit `19decda7` on 2023-09-29, and `stage:` at the pinned tip reads `beta`.

**Required.**
- On 2023-01-01 the recorded status of KEP 2593 was `implementable`.
  forms: the literal value `implementable`; "`status: implementable` at that date". Not counted: "active / being developed" with the recorded value never given.
  sources: the blob governing that date is commit `44828bde` (2022-10-06, "Update feature-gate, API Name and Milestones"), `git show 44828bde:keps/sig-network/2593-multiple-cluster-cidrs/kep.yaml` → `status: implementable` (and `stage: alpha` at line 16 of that blob).
- Today's value is different — `withdrawn` — and the answer distinguishes the two.
  forms: naming today's value as `withdrawn` alongside the historical one; "implementable in 2023, withdrawn now". Not counted: giving only one value, or both without pairing each to its date.
  sources: `kubernetes-enhancements/keps/sig-network/2593-multiple-cluster-cidrs/kep.yaml:7` (set by commit `19decda7`, 2023-09-29, "withdrawn cluster-cidr kep"); `kubernetes-enhancements/keps/sig-network/2593-multiple-cluster-cidrs/README.md:69`; graph entity `adr-multiple-cluster-cidrs`, key fact `status=withdrawn (kep)`.

**Forbidden.**
- Answering `beta` for the 2023-01-01 date. `beta` is the `stage:` field at the pinned tip (kep.yaml:16); on 2023-01-01 that field still read `alpha`, so `beta` is wrong for the field asked about *and* for the date.
- Answering `withdrawn` for 2023-01-01, or `implementable` for today.
- Claiming the two fields moved together. `stage:` advanced `alpha` to `beta` while `status:` fell `implementable` to `withdrawn`; reporting that divergence is supported.

**Locator.** `tsubasa query "status of the discontiguous cluster CIDR KEP" --as-of 2023-01-01`; `kubernetes-enhancements/keps/sig-network/2593-multiple-cluster-cidrs/kep.yaml:7`

**Discriminates.** Two fields moved in opposite senses: `stage:` advanced `alpha → beta` while `status:` fell `implementable → withdrawn`. A correct as-of answer reports the 2023 pair (`implementable`, `alpha`); reading `stage:` as the answer produces "beta", which was not even true on that date.

**Trap.** Answering `beta` (neither the field asked for, nor its 2023-01-01 value), or `withdrawn` for both dates.
