# Central evaluation: Runs 4–7 record (Keystone + Suryodaya)

| | |
|---|---|
| Run 4 | 2026-10-18 · `3f91478` · **failed**: the evaluator's model returned a 401 on the probe |
| Run 5 | 2026-10-19 · `6351813` · **failed**: Suryodaya hit the loop token budget; Keystone got a 401 from the evaluator's model mid-loop |
| Run 6 | 2026-10-19 12:59 (UI time) · `da9b7e8` · **PASSED, 14 of 14**, 158,532 tokens |
| Run 7 | to be submitted 2026-10-23 from 12:59 · `136733a` · rehearsed locally 14 of 14 (see section 7) |
| Instances | keystone, suryodaya (evaluator copies, not the shared book) |
| Harness deadline | 2026-10-24 23:59 IST, so Run 7 is the last submission before it |

As with earlier runs, a central pass shows only that the harness installs and runs in the evaluator. It is **not** IT-01 evidence. Run 3 is recorded in [central_run_test_result.md](central_run_test_result.md).

---

## 1. Run 4 (`3f91478`): model probe got a 401

`model-reachable` and `agent-loop-read-only` failed on both instances. The probe got `HTTP 401 … "The model provider answered 401." code upstream_error` within 0.3 s, so the loop recorded "not run: model probe failed". `llm.py` and `config.py` had not changed since Run 3 passed (`277391e`), so this was the evaluator's model provider, not our credential handling.

## 2. Run 5 (`6351813`): two loop failures, each with its own cause

`6351813` adds a hard token budget to the loop (110k per instance, sized for the 300k per-run cap). All other tasks passed.

| Instance | agent-loop-read-only evidence | Cause |
|---|---|---|
| suryodaya | `ended=token_budget; model calls=6; tool steps=3; … error=token budget reached: 119085 >= 110000 after 6 model call(s)` | The evaluator's model took 6 calls (about 20k each) and had not answered yet, so the hard stop ended the loop with no answer. The 10-18 local rehearsal with Sonnet took 3 calls (about 50k). |
| keystone | `ended=error; model calls=2; tool steps=1; … error=llm: LLMConfigError: HTTP 401 … "The model provider answered 401."` | The probe and loop call 1 succeeded, then call 2 got the evaluator's provider 401 again. Our key had been accepted, so this was the provider's failure, not our credential. The instructor later confirmed that a spent budget returns 429 `budget_exhausted`, not 401. |

## 3. Fixes between Run 5 and Run 6

| Commit | Change |
|---|---|
| `e6cb375` | **Wrap-up call.** Before each model call, if tokens used + 2 × the last call's cost would exceed the budget, that call becomes the last one: the model is told to answer from what it has read and not to call tools, and the hard stop does not apply to it. Only one wrap-up call is made; a non-answer on it still ends the loop as `token_budget`. The central budget goes from 110k to 140k per instance (two instances plus probes stay under 300k). |
| `d79f858` | **Proxy 401 retry.** A 401 whose body carries the proxy's `upstream_error` / "model provider answered" marker is treated as temporary, like a 5xx, so the existing single 10 s retry covers it. The model probe gets the same retry. A plain 401 for our own key still fails straight away. |
| `da9b7e8` | Hand-written tests for both changes (`tests/test_loop_wrapup.py`, 7 tests). 418 tests pass. |

Checks before submitting:

- Live Suryodaya run at 140k: 7/7, loop 3 calls, 49,131 tokens. With the budget forced to 40k: the loop still ended `done` at 49,333 tokens, so the wrap-up call works on a real model.
- A clean install of the pushed commit on Python 3.11.15 (`pip install .` installed only `pipeline-agent`): dry run 4/4, 418 tests pass.
- Checked against the instructor's harness rules: the platform URL comes only from `AGENTSWITCH_BASE_URL` and the instance from `AGENTSWITCH_INSTANCE`; no `.env` is read on the central path; there are no third-party dependencies; it runs on Python 3.11; and 429 is never treated as an auth error.
- Local run of `da9b7e8` on the shared book, read-only, Sonnet via OpenRouter: 14/14 (ks 25/8, loop 19,291 tokens; su 54/20, loop 49,132 tokens).

## 4. Run 6 (`da9b7e8`) task results

**Verdict: PASSED, 14 of 14 tasks** (keystone 7/7, suryodaya 7/7; every task scored 1.00). Tokens used: 158,532 of the 300k per-run cap.

| Task | Keystone | Suryodaya |
|---|---|---|
| env-contract | all 6 evaluator variables present | all 6 evaluator variables present |
| mcp-preflight | surface=entity_scoped, tools=250, G1=sales_reachable, canonical task READY [5.0s] | surface=entity_scoped, tools=250, G1=sales_reachable, canonical task READY [5.1s] |
| canonical-read-only | rot threshold 30d; rotting deals=25 (candidates 25); uncalled customers=8; hidden silence=0; deals with a proposed next action=25; tool calls=13; writes=0 [12.8s] | rot threshold 30d; rotting deals=54 (candidates 54); uncalled customers=20; hidden silence=0; deals with a proposed next action=54; tool calls=13; writes=0 [12.7s] |
| refusal-guard | refused 2/2 out-of-policy requests before any tool call; canonical request refused=False [0.0s] | same [0.0s] |
| model-reachable | reply='OK' [1.0s] | reply='OK' [1.3s] |
| agent-loop-read-only | ended=done; model calls=2; tool steps=1; writes=0; created=0; updated=0; unsupported figures=0 [69.6s] | ended=done; model calls=6; tool steps=4; writes=0; created=0; updated=0; unsupported figures=1 [139.7s] |
| result-contract | 6 earlier rows: unique ids, boolean passed, evidence present | same |

The evaluator redacts the per-task token counts.

## 5. Observations (none of them fail a task)

- **Suryodaya's loop needed 6 calls again.** That's the same count that hit the 110k stop in Run 5, and this time it ended `done`. Because the token counts are redacted, we can't tell whether the wrap-up call fired or the loop finished under 140k on its own. The run's total of 158,532 tokens leaves plenty of room either way.
- **The canonical numbers match the local run on the shared book** (ks 25/8, su 54/20). Suryodaya's 54/20 vs Run 3's 74/34 is the `3e21452` exclusion of 20 employee-party deals (see the Run 3 record, section 5).
- **The evaluator copy now shows 250 tools,** the same as the shared credential (Runs 1–3 showed 263).
- **Unsupported figures:** su went from 4 (Run 3) to 1. As before, these are figures the model worked out itself, not wording in the summary.

## 6. Follow-ups

- If a spent budget (429 `budget_exhausted`) ever happens, we currently retry it once after 10 s before failing. That's compliant but pointless; failing straight away is a two-line change and would need its own hand-written test.
- `main` now contains `central-eval` (merge `0f71725`), so both have the same code. Since 2026-10-22 `central-eval` is fast-forwarded from `main`; both are at `136733a`.

## 7. Run 7 preparation (`136733a`)

Two instructor updates on 2026-10-22 led to two changes after Run 6.

| Commit | Change |
|---|---|
| `cb51bfe` | **Time budget.** The evaluator now passes `AGENTSWITCH_SECONDS_LEFT` and `AGENTSWITCH_DEADLINE`. Each task's limit is capped at the time left minus 20 s, and the loop gets a deadline 30 s inside its own limit. When another round would not finish before that deadline, the loop makes its one "answer now, no tools" call, as it already does for tokens, and ends `time_budget` if the model still has no answer. Without either variable the fixed limits apply as before. Details: [time_wrapup_fix.md](time_wrapup_fix.md). |
| `c0870d8` | Hand-written tests for the time budget (`tests/test_time_wrapup.py`, 11 tests) and the write-up. |
| `136733a` | **Company-day date check.** The platform now dates "today" by the company's own day (instructor update N489, which fixed our bug `4681e718`). The loop's guard on a new Activity's `due_date`, and the date the model is told to respect, now use the company's day instead of the UTC date. Before this, between 00:00 and 05:30 IST the agent refused activities dated today. |

The instructor's other update, passing the model's reasoning settings through, needs no change: we send none, and changing them could not be rehearsed on the evaluator's model.

Checks before submitting:

- Platform fix verified live first: on Suryodaya at 2026-10-23 01:13 IST (2026-10-22 19:43 UTC), a completed call dated the IST day was accepted (201; it was 422 before the fix). The date change was committed only after this.
- 430 tests pass and the central dry-run passes 4/4.
- Local runs of a clean `git archive` export on the shared book, read-only, with only the evaluator's variables set, `AGENTSWITCH_SECONDS_LEFT=1800` and Sonnet 4.6 via OpenRouter:

| Commit (date) | Keystone | Suryodaya |
|---|---|---|
| `c0870d8` (2026-10-22) | 7/7; 25/8; loop 2 calls, 18,637 tokens | 7/7; 54/20; loop 3 calls, 48,156 tokens, 3 unsupported figures |
| `136733a` (2026-10-23) | 7/7; 25/8; loop 2 calls, 19,610 tokens | 7/7; 54/20; loop 3 calls, 47,978 tokens, 0 unsupported figures |

- Neither loop came near its time limit, so the time wrap-up did not fire in these runs. It is covered by the hand-written tests and a local check with a slow fake model.
- Our seat now sees 234 tools on the shared credential (250 in Run 6). Preflight still reports the canonical task READY.

Submitted once, as planned. The 3-day window had already opened on 2026-10-22 at 12:59 IST, so the submission went in on 2026-10-23 at 10:40 IST. The result is in section 8.

## 8. Run 7 (`136733a`) result

Submitted 2026-10-23 10:40 IST on `central-eval` @ `136733a` (keystone + suryodaya): **Passed, 14 of 14 tasks**, 174,121 tokens.

| Task | Keystone | Suryodaya |
|---|---|---|
| Evaluator variables present | pass: all 6 present | pass: all 6 present |
| MCP catalogue and read access | pass: entity_scoped, 234 tools, G1 sales_reachable, READY (5.8 s) | pass: entity_scoped, 234 tools, G1 sales_reachable, READY (6.1 s) |
| Canonical task, PROPOSE mode, zero writes | pass: 30 d; 25 rotting (25 candidates); 8 uncalled; 0 hidden silence; 25 next actions; 12 tool calls; 0 writes (14.8 s) | pass: 30 d; 54 rotting (54 candidates); 21 uncalled; 0 hidden silence; 54 next actions; 12 tool calls; 0 writes (14.9 s) |
| Out-of-policy refusals | pass: 2/2 refused before any tool call; canonical not refused | pass: 2/2 refused before any tool call; canonical not refused |
| Platform model probe | pass: `OK` (1.0 s) | pass: `OK` (1.2 s) |
| Agent loop on the platform model | pass: ended=done; 4 model calls; 2 tool steps; 0 writes; 1 unsupported figure (100.2 s) | pass: ended=done; 7 model calls; 4 tool steps; 0 writes; 1 unsupported figure (91.7 s) |
| results.json rows well-formed | pass | pass |

Notes:

- Suryodaya uncalled customers were 21 here and 20 in the 01:13 IST rehearsal. Both ran on the live shared book, so this is a data change between the two runs, not a code change.
- Both loops ended `done`, so the time wrap-up and token wrap-up did not fire. Total tokens (174,121) stayed well under the 300,000 per-run budget.
- This was the last allowed submission before the 2026-10-24 23:59 IST harness deadline.
