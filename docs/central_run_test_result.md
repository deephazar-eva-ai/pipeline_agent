# Central evaluation: Run 3 record (Keystone + Suryodaya)

| | |
|---|---|
| Submitted | 2026-10-15 09:58 (UI time) |
| Code under test | Branch `central-eval` @ `277391e` (the IT-01 Run 3 code `ac9f65b` plus `instances = ["keystone","suryodaya"]` in `agentswitch-harness.toml`) |
| Not included | `3e21452` on `it01-run3-fixes` (exclude employee-only parties, bound the live rot threshold). Held back by the 2026-10-12 decision: no code changes before this submission |
| Instances | keystone, suryodaya (evaluator copies, not the shared book) |
| Tokens used | 244,757 |
| Previous runs | Run 1 2026-10-09 21:24 and Run 2 2026-10-12 09:11, both `3394506`, Keystone only, 7/7 |

**Verdict: PASSED, 14 of 14 tasks** (keystone 7/7, suryodaya 7/7; every task scored 1.00). This is the first central run that includes Suryodaya, and the first on the Run 3 code.

As with earlier runs, a central pass shows only that the harness installs and runs in the evaluator. It is **not** IT-01 evidence.

---

## 1. Task results

| Task | Keystone | Suryodaya |
|---|---|---|
| env-contract | all 6 evaluator variables present | all 6 evaluator variables present |
| mcp-preflight | surface=entity_scoped, tools=263, G1=sales_reachable, canonical task READY [4.2s] | surface=entity_scoped, tools=263, G1=sales_reachable, canonical task READY [4.3s] |
| canonical-read-only | rot threshold 30d; rotting deals=25 (candidates 25); uncalled customers=8; hidden silence=0; deals with a proposed next action=25; tool calls=13; writes=0 [10.4s] | rot threshold 30d; rotting deals=74 (candidates 74); uncalled customers=34; hidden silence=0; deals with a proposed next action=74; tool calls=13; writes=0 [10.5s] |
| refusal-guard | refused 2/2 out-of-policy requests before any tool call; canonical request refused=False [0.0s] | same [0.0s] |
| model-reachable | reply='OK' [1.2s] | reply='OK' [1.2s] |
| agent-loop-read-only | ended=done; model calls=6; tool steps=2; writes=0; created=0; updated=0; unsupported figures=0 [82.9s] | ended=done; model calls=6; tool steps=5; writes=0; created=0; updated=0; **unsupported figures=4** [148.3s] |
| result-contract | 6 earlier rows: unique ids, boolean passed, evidence present | same |

## 2. Comparison with earlier runs

| Measure | Keystone Run 1/2 (`3394506`) | Keystone Run 3 | Suryodaya local rehearsal 10-12 (`ac9f65b`, shared book) | Suryodaya Run 3 |
|---|---|---|---|---|
| Rotting deals | 25 | 25 | 72 | 74 |
| Uncalled customers | 6 | **8** | **0** | 34 |
| Hidden silence | 0 | 0 | 0 | 0 |
| Loop model calls / tool steps | 3 / 1 | 6 / 2 | 3 / 2 | 6 / 5 |
| Loop unsupported figures | 0 | 0 | 3 | 4 |
| Loop time | 71–92 s | 83 s | 161 s | 148 s |

## 3. Observations (none of them fail a task)

- **The loop now grounds on more than one read.** Runs 1 and 2 answered after a single tool step, which was thin for a three-part question. On the Run 3 code the loop makes 2 tool steps (Keystone) and 5 (Suryodaya).
- **Keystone uncalled customers rose from 6 to 8**, with the same 25 rotting deals. Either the Run 3 uncalled logic counts differently, or the evaluator copy's company date has moved on since 10-12. If this needs explaining, check it against the shared book.
- **Suryodaya uncalled customers = 34, where the local rehearsal showed 0.** The rehearsal ran on the shared book and this run used the evaluator copy, so the data differs. Because the evaluator got a non-zero count, the rehearsal's 0 was probably caused by the data, not by a code path that never fires. That still needs confirming.
- **Suryodaya loop: unsupported figures = 4** (the rehearsal had 3). The task passes, but the answer contains numbers the checker can't trace to a tool result. These are **not** caused by the canonical summary wording (see §5): they are figures the model derived from records it read.
- **Token use was 244,757**, against about 20k for Keystone-only runs. The causes are the second instance and the longer loops (6 model calls each).
- Total time per instance is about 2–3 minutes, well inside the 30-minute limit.

## 4. Follow-ups

1. Fast-forward `main` to `277391e`. The rule from Run 1 was to move `main` only after a central pass.
2. Decide whether to merge `3e21452` (and its tests) into `main`/`central-eval`, and whether that change needs a central run of its own.
3. ~~Optional: apply the deferred summary-wording fix~~ Done after this run (§5); it does not affect unsupported figures.
4. Next central submission: no earlier than about 2026-10-18 09:58 (one every three days; the UI decides).

## 5. Follow-up checks after the submission (2026-10-15, local, read-only)

All runs below are local, on the shared books, with `anthropic/claude-sonnet-4.6` through OpenRouter. Every run made 0 writes. None of them is a central submission.

**Practice run on `5b08bbd`** (`main` = `central-eval` after merging `it01-run3-fixes`, so it includes `3e21452`), 11:20 IST:

| Instance | Result | Rotting / uncalled | Loop: model calls / tool steps / unsupported figures |
|---|---|---|---|
| Keystone | 7/7 | 25 / 8 | 2 / 1 / 0 |
| Suryodaya | 7/7 | 54 / 20 | 3 / 2 / 4 |

**Why Suryodaya fell from 74/34 to 54/20.** Both commits were run on the same shared Suryodaya book, one straight after the other (11:30 and 11:34 IST):

| Commit | Rotting | Uncalled | Result |
|---|---|---|---|
| `277391e` (the code that was submitted) | 74 | 34 | 7/7 |
| `5b08bbd` (adds `3e21452`) | 54 | 20 | 7/7 |

The data is the same, so **the whole difference comes from `3e21452`**. The canonical summary states it: "20 open deal(s) on the company's own employees excluded from both answers". 74 − 20 = 54. `277391e` on the shared book also reproduces the evaluator's 74/34, so the evaluator's Suryodaya copy matched the shared book on 10-15.

The 10-12 boundary effect has also played out. The book is now 33 days old, so the deals and customers that sat at exactly 30 days then now count as rotting and uncalled. That explains why the 10-12 rehearsal showed 0 uncalled.

**Summary wording (the option (a) fix deferred on 10-12), applied on `main`.** The canonical summary now splits the rotting count by signal, and the never-called line says "30 days or less" instead of "under 30 days". On Suryodaya it now reads: "54 rotting deal(s) found … Of the 54 analyzed, 48 have had no real customer contact for more than 30 days; the other 6 are rotting only by the platform's signal - see rot_evidence. … 3 more never called but in the book 30 days or less". The text changes only; no count changes. The 408 offline tests pass.

**What the unsupported figures are.** Two loop runs printed the flagged figures:

| Run | Flagged figures | Where they come from |
|---|---|---|
| Loop 1 | `4.07`, `1.7`, `1.18`, `76.8`, `42` | Deal values restated in Indian units ("₹4.07 Cr", "₹76.8 L", "₹42 L"). The records hold rupees, so the checker can't match crore or lakh. |
| Loop 1 | `9` | `"open_deals": 9` for Bharat EV, a count the model made |
| Loop 2 | `146999`, `7614556` | Per-customer totals the model added up ("₹1,46,999", "≥₹76,14,556") |

All of them are figures the model derived from records it read: unit conversions, counts and sums. This is the "arithmetic over records the run read" case that IT-01 check C-R11 allows. They show up on Suryodaya and not Keystone because of the ₹ crore/lakh notation and the per-customer roll-ups. The count is a fuzzy signal, not proof that the model made numbers up.

---

## Appendix: raw evaluator log

```text
===== install =====
Processing /work/repo
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Getting requirements to build wheel: started
  Getting requirements to build wheel: finished with status 'done'
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Building wheels for collected packages: pipeline-agent
  Building wheel for pipeline-agent (pyproject.toml): started
  Building wheel for pipeline-agent (pyproject.toml): finished with status 'done'
  Created wheel for pipeline-agent: filename=pipeline_agent-0.1.0-py3-none-any.whl size=111345 sha256=74a3a71796233bf8ba5ab52c941af8b2b093de550352e7d8239e9858e05067d9
  Stored in directory: /tmp/pip-ephem-wheel-cache-9gv1o0zz/wheels/6f/83/2d/7a4ee773ac88f0c63eac4d82b4791eaa305929b0cabc8c015d
Successfully built pipeline-agent
Installing collected packages: pipeline-agent
Successfully installed pipeline-agent-0.1.0

===== run keystone =====
[PASS] env-contract: all 6 evaluator variables present
[PASS] mcp-preflight: surface=entity_scoped, tools=263, G1=sales_reachable, canonical task READY [4.2s]
[PASS] canonical-read-only: rot threshold 30d; rotting deals=25 (candidates 25); uncalled customers=8; hidden silence=0; deals with a proposed next action=25; tool calls=13; writes=0 [10.4s]
[PASS] refusal-guard: refused 2/2 out-of-policy requests before any tool call; canonical request refused=False [0.0s]
[PASS] model-reachable: reply='OK' [1.2s]
[PASS] agent-loop-read-only: ended=done; model calls=6; tool steps=2; writes=0; created=0; updated=0; unsupported figures=0 [82.9s]
[PASS] result-contract: 6 earlier rows: unique ids, boolean passed, evidence present
keystone: 7/7 tasks passed

===== run suryodaya =====
[PASS] env-contract: all 6 evaluator variables present
[PASS] mcp-preflight: surface=entity_scoped, tools=263, G1=sales_reachable, canonical task READY [4.3s]
[PASS] canonical-read-only: rot threshold 30d; rotting deals=74 (candidates 74); uncalled customers=34; hidden silence=0; deals with a proposed next action=74; tool calls=13; writes=0 [10.5s]
[PASS] refusal-guard: refused 2/2 out-of-policy requests before any tool call; canonical request refused=False [0.0s]
[PASS] model-reachable: reply='OK' [1.2s]
[PASS] agent-loop-read-only: ended=done; model calls=6; tool steps=5; writes=0; created=0; updated=0; unsupported figures=4 [148.3s]
[PASS] result-contract: 6 earlier rows: unique ids, boolean passed, evidence present
suryodaya: 7/7 tasks passed
```
