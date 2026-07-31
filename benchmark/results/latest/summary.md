generated: 2026-07-31T07:54:23+00:00 · model: sonnet · fixture: /Users/macbook/Documents/work/tsubasa-benchmark
questions parsed: 72 · unusable: none
jobs: 3 · max turns: 3 · judge: sonnet

## Overall

Verdict rows are the **single-shot (turn 1)** answer: one question, one
answer, graded blind. That is the number comparable with the earlier rounds
of this benchmark. What the follow-up turns bought is the next table.

| metric | A vanilla | B captain |
| --- | --- | --- |
| runs | 72 | 72 |
| graded | 72 | 72 |
| correct (turn 1) | 55 | 63 |
| partial (turn 1) | 0 | 0 |
| wrong (turn 1) | 17 | 9 |
| confabulated (turn 1) | 0 | 0 |
| no_answer (turn 1) | 0 | 0 |
| excluded (contaminated/error/denied/mutated/quota) | 0 | 0 |
| citations resolved / total | 428/438 | 341/350 |
| citations unresolved | 10 | 9 |
|   of which elided shorthand | 3 | 1 |
| citations per 1k output tokens | 1.5 | 2.0 |
| graph-id citations (record, not tree) | 0 | 111 |
|   cites: file_line | 183 | 85 |
|   cites: kep | 45 | 54 |
|   cites: path | 86 | 51 |
|   cites: path_elided | 3 | 1 |
|   cites: sha | 121 | 48 |
| cost USD (all turns) | 26.1305 | 26.2771 |
| cost USD (turn 1 only) | 14.3136 | 22.2076 |
| judge cost USD (excluded from arm cost) | 6.9554 | 5.6150 |
| output tokens (dearest class) | 288000 | 179193 |
| cache write tokens | 916471 | 2250681 |
| cache read tokens | 14798548 | 20041627 |
| input tokens (uncached) | 1469 | 1030 |
| cache write tokens per tool call | 821 | 3991 |
| wall clock median s (turn 1) | 48.817 | 45.256 |
| wall clock median s (whole run) | 96.706 | 73.233 |
| model iterations median (turn 1) | 10.0 | 7.0 |
| tool calls | 1116 | 564 |
| turns-to-correct reconciled to canonical | 14 | 5 |
| denied tool calls | 0 | 4 |
| graph mutations detected | 0 | 0 |

## Iteration to a correct answer

Cap: 3 turns. After any verdict that is not
`correct` the same session is nudged with one constant string, identical in
both arms: 'That is not correct. Reconsider and answer again.'

Questions whose correct answer is an abstention (`f-negative`, and the two
`g-git-history` honesty probes) are **single-shot by design** and are excluded
from this distribution: nudging an arm that correctly refused would train it
out of the behaviour being measured. They are counted on their own row.

| metric | A vanilla | B captain |
| --- | --- | --- |
| multi-turn questions | 63 | 63 |
| correct at turn 1 | 50 | 55 |
| correct at turn 2 | 3 | 1 |
| correct at turn 3 | 2 | 3 |
| never correct within the cap | 8 | 4 |
| reached correct at all | 55 | 59 |
| median turns to correct (of those that got there) | 1.0 | 1.0 |
| median seconds to correct | 45.156 | 39.661 |
| median USD to correct | 0.1370 | 0.2500 |
| fixed by a nudge (wrong at turn 1, correct later) | 5 | 4 |
| nudges sent | 43 | 18 |
| median turns used | 1.0 | 1.0 |
| single-shot (abstain-correct) questions | 9 | 9 |
|   of which correct | 7 | 8 |

## By category

| category | n | A:corr | A:part | A:wron | A:conf | A:no_a | B:corr | B:part | B:wron | B:conf | B:no_a | A:cites | B:cites | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-where | 6 | 5 | 0 | 1 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 26/26 | 25/25 |  |
| b-topology | 4 | 4 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 39/41 | 17/17 |  |
| c-status | 10 | 10 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 50/51 | 86/89 |  |
| d-supersession | 4 | 4 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 18/19 | 25/26 |  |
| e-temporal | 6 | 4 | 0 | 2 | 0 | 0 | 5 | 0 | 1 | 0 | 0 | 34/34 | 37/38 |  |
| f-negative | 7 | 5 | 0 | 2 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 31/33 | 13/13 | both arms should abstain; a confident answer is a confabulation in either arm |
| g-git-history | 12 | 7 | 0 | 5 | 0 | 0 | 6 | 0 | 6 | 0 | 0 | 90/90 | 48/50 | G11 and G12 are honesty probes: the fixture records the reversal, not the reason |
| h-routing | 6 | 6 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 24/24 | 15/15 |  |
| i-goal-conflict | 10 | 7 | 0 | 3 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 44/46 | 45/47 |  |
| x-cross-repo | 7 | 3 | 0 | 4 | 0 | 0 | 5 | 0 | 2 | 0 | 0 | 72/74 | 30/30 |  |

## Per question

`verdicts` is every turn's verdict in order; `ttc` is the turn the answer
first became correct, `-` if it never did (`1shot` where a nudge is withheld
by design).

| qid | A verdicts | B verdicts | A ttc | B ttc | A cites | B cites | A status | B status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | correct | correct | 1 | 1 | 7/7 | 3/3 | ok | ok |
| A2 | correct | correct | 1 | 1 | 2/2 | 1/1 | ok | ok |
| A3 | correct | correct | 1 | 1 | 7/7 | 6/6 | ok | ok |
| A4 | confabulated,confabulated,partial | correct | - | 1 | 3/3 | 6/6 | ok | ok |
| A5 | confabulated,confabulated,confabulated | correct | - | 1 | 3/3 | 2/2 | ok | ok |
| A6 | correct | correct | 1 | 1 | 4/4 | 7/7 | ok | ok |
| B1 | partial,correct | correct | 2 | 1 | 8/8 | 1/1 | ok | ok |
| B2 | confabulated,partial,confabulated | correct | - | 1 | 12/12 | 3/3 | ok | ok |
| B3 | confabulated,correct | correct | 2 | 1 | 7/8 | 2/2 | ok | ok |
| B4 | confabulated,confabulated,partial | correct | - | 1 | 12/13 | 11/11 | ok | ok |
| C1 | correct | correct | 1 | 1 | 3/3 | 5/5 | ok | ok |
| C2 | correct | correct | 1 | 1 | 10/10 | 19/22 | ok | ok |
| C3 | correct | correct | 1 | 1 | 2/2 | 7/7 | ok | ok |
| C4 | correct | correct | 1 | 1 | 3/3 | 5/5 | ok | ok |
| C5 | correct | correct | 1 | 1 | 6/6 | 5/5 | ok | ok |
| C6 | correct | correct | 1 | 1 | 3/3 | 8/8 | ok | ok |
| C7 | correct | correct | 1 | 1 | 5/5 | 11/11 | ok | ok |
| C8 | correct | correct | 1 | 1 | 8/8 | 7/7 | ok | ok |
| C9 | correct | correct | 1 | 1 | 8/9 | 15/15 | ok | ok |
| C10 | correct | correct | 1 | 1 | 2/2 | 4/4 | ok | ok |
| D1 | correct | correct | 1 | 1 | 5/5 | 5/5 | ok | ok |
| D2 | correct | correct | 1 | 1 | 4/5 | 6/7 | ok | ok |
| D3 | correct | correct | 1 | 1 | 4/4 | 7/7 | ok | ok |
| D4 | correct | correct | 1 | 1 | 5/5 | 7/7 | ok | ok |
| E1 | partial,wrong,confabulated | correct | - | 1 | 3/3 | 4/4 | ok | ok |
| E2 | partial,correct | correct | 2 | 1 | 5/5 | 3/3 | ok | ok |
| E3 | correct | wrong,wrong,correct | 1 | 3 | 4/4 | 5/5 | ok | ok |
| E4 | wrong,wrong,wrong | correct | - | 1 | 10/10 | 13/13 | ok | ok |
| E5 | correct | correct | 1 | 1 | 6/6 | 8/8 | ok | ok |
| E6 | correct | correct | 1 | 1 | 6/6 | 4/5 | ok | ok |
| F1 | wrong | correct | 1shot | 1 | 0/0 | 4/4 | ok | ok |
| F2 | confabulated | wrong | 1shot | 1shot | 5/6 | 2/2 | ok | ok |
| F3 | correct | correct | 1 | 1 | 5/5 | 1/1 | ok | ok |
| F4 | correct | correct | 1 | 1 | 8/8 | 3/3 | ok | ok |
| F5 | correct | wrong | 1 | 1shot | 3/4 | 1/1 | ok | ok |
| F6 | confabulated | wrong | 1shot | 1shot | 5/5 | 1/1 | ok | ok |
| F7 | correct | correct | 1 | 1 | 5/5 | 1/1 | ok | ok |
| G1 | wrong,correct | correct | 2 | 1 | 16/16 | 4/4 | ok | ok |
| G2 | correct | wrong,wrong,correct | 1 | 3 | 6/6 | 3/3 | ok | ok |
| G3 | wrong,wrong,wrong | wrong,wrong,wrong | - | - | 3/3 | 4/4 | ok | ok |
| G4 | correct | wrong,correct | 1 | 2 | 7/7 | 2/2 | ok | ok |
| G5 | wrong,wrong,correct | wrong,wrong,wrong | 3 | - | 0/0 | 2/2 | ok | ok |
| G6 | correct | correct | 1 | 1 | 6/6 | 7/7 | ok | ok |
| G7 | correct | correct | 1 | 1 | 13/13 | 8/9 | ok | ok |
| G8 | correct | correct | 1 | 1 | 11/11 | 6/6 | ok | ok |
| G9 | wrong,correct | wrong,wrong,wrong | 2 | - | 7/7 | 4/4 | ok | ok |
| G10 | wrong,wrong,wrong | wrong,correct | - | 2 | 5/5 | 0/0 | ok | ok |
| G11 | correct | correct | 1 | 1 | 12/12 | 2/3 | ok | ok |
| G12 | correct | wrong | 1 | 1shot | 4/4 | 6/6 | ok | ok |
| H1 | correct | correct | 1 | 1 | 2/2 | 2/2 | ok | ok |
| H2 | correct | correct | 1 | 1 | 4/4 | 3/3 | ok | ok |
| H3 | correct | correct | 1 | 1 | 3/3 | 2/2 | ok | ok |
| H4 | partial,correct | correct | 2 | 1 | 8/8 | 3/3 | ok | ok |
| H5 | correct | correct | 1 | 1 | 4/4 | 2/2 | ok | ok |
| H6 | correct | correct | 1 | 1 | 3/3 | 3/3 | ok | ok |
| I1 | partial,partial,partial | correct | - | 1 | 4/4 | 4/4 | ok | ok |
| I2 | correct | correct | 1 | 1 | 3/3 | 12/14 | ok | ok |
| I3 | wrong,confabulated,confabulated | correct | - | 1 | 8/8 | 5/5 | ok | ok |
| I4 | correct | correct | 1 | 1 | 10/10 | 1/1 | ok | ok |
| I5 | confabulated,confabulated,wrong | correct | - | 1 | 4/5 | 2/2 | ok | ok |
| I6 | correct | correct | 1 | 1 | 6/7 | 3/3 | ok | ok |
| I7 | correct | correct | 1 | 1 | 1/1 | 5/5 | ok | ok |
| I8 | wrong,correct | correct | 2 | 1 | 0/0 | 3/3 | ok | ok |
| I9 | wrong,partial,correct | correct | 3 | 1 | 5/5 | 7/7 | ok | ok |
| I10 | wrong,correct | wrong,wrong,correct | 2 | 3 | 3/3 | 3/3 | ok | ok |
| X1 | correct | correct | 1 | 1 | 11/11 | 4/4 | ok | ok |
| X2 | wrong,confabulated,confabulated | wrong,wrong,correct | - | 3 | 10/10 | 6/6 | ok | ok |
| X3 | confabulated,confabulated,confabulated | correct | - | 1 | 7/8 | 4/4 | ok | ok |
| X4 | partial,wrong,wrong | correct | - | 1 | 14/14 | 4/4 | ok | ok |
| X5 | wrong,wrong,wrong | wrong,wrong,wrong | - | - | 17/17 | 5/5 | ok | ok |
| X6 | correct | correct | 1 | 1 | 4/4 | 2/2 | ok | ok |
| X7 | partial,correct | correct | 2 | 1 | 9/10 | 5/5 | ok | ok |

## What these numbers do not show

- Citation counts are EXISTENCE checks. A resolved citation proves the file,
  commit or record id exists; it does not prove the source supports the claim.
  That judgement belongs to the judge, and a run can be 100% resolved and still
  graded `confabulated`.
- `no_answer` is reported separately and never folded into `wrong`: a session
  that stopped without answering is a harness outcome, not an arm's answer.
- Excluded runs are counted, not hidden. Any non-zero contamination or
  graph-mutation count invalidates the arm it belongs to until it is re-run.
- `quota_exceeded` is an account outcome, not an arm's answer: the session/usage
  allowance ran out mid-campaign. Those runs are retryable and are excluded
  from every verdict column rather than scored.
- Turns-to-correct is measured against THIS judge. The nudge says only that the
  previous answer was wrong, so a turn-2 correction is the arm re-searching its
  own workspace, not following a hint -- but 'wrong, try again' is still
  information, and an arm that guesses differently each turn can reach `correct`
  without ever having had grounds for it.
- Seconds-to-correct and cost-to-correct count the arm's own turns only. The
  judge latency and judge cost between turns are harness overhead and are
  reported separately.

