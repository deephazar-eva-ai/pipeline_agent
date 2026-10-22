# Time-budget wrap-up for the central runner and agent loop

Commit `cb51bfe` on `main` (2026-10-22). Files: `src/pipeline_agent/central_runner.py` and
`src/pipeline_agent/harness/loop.py`.

This change stops the agent loop from being killed mid-answer when it runs out of time. When
time gets short, the loop asks the model for its final answer instead.

## Background

On 2026-10-22 the instructor announced that the central evaluator now passes
`AGENTSWITCH_SECONDS_LEFT` and `AGENTSWITCH_DEADLINE` to the harness, so it knows how much of the
time budget is left. The harness deadline is unchanged: 24 Oct, 23:59 IST.

## The problem

Each run of the central evaluator has a 30-minute limit (`timeout_minutes = 30` in
`agentswitch-harness.toml`). Before this change the runner gave each task a fixed limit, and the
agent loop got `LOOP_TIMEOUT = 900` seconds. If the loop was still working at 900 seconds,
`asyncio.wait_for` cancelled it:

- the `results.json` row just said `timed out`;
- whatever the model had read was thrown away, and the task failed.

The same situation was already handled for tokens. Central Run 5 (2026-10-19) failed on Suryodaya
when the loop hit its token cap, and the fix was one wrap-up call: "answer now from what you have,
no tools" (see `central_runs_4_to_6.md`). This change gives time the same treatment. It also uses
the two new evaluator variables, so the runner no longer assumes the full 30 minutes is available.

## How it works

There are two layers.

### 1. The runner caps every task by the evaluator's time left

In `central_runner.py`:

- **Reading the budget.** At start-up `main()` calls `evaluator_deadline()`, which reads
  `AGENTSWITCH_SECONDS_LEFT`. If that is missing, it reads `AGENTSWITCH_DEADLINE`, which can be epoch
  seconds or an ISO 8601 time; a time with no offset is taken as UTC. The result is stored in
  `DEADLINE` as a `time.monotonic()` value, so a change to the system clock can't move it. The
  runner prints `evaluator time left: Ns` when a budget is given.
- **Capping tasks.** `run_task` passes every limit through `task_timeout(t)`, which returns the
  smaller of the task's fixed limit and the time left minus `TIME_MARGIN` (20s), but never less
  than `MIN_TASK_TIMEOUT` (5s). The 20s keeps the last `results.json` write inside the evaluator's
  limit.
- **No budget given.** If neither variable is set, or the value won't parse, `DEADLINE` is `None`
  and the fixed limits apply exactly as before. Local runs and the old evaluator behave the same.

### 2. The loop answers before its deadline

- **The deadline.** `check_loop` gives `run_loop` a deadline of the loop task's limit
  (`task_timeout(LOOP_TIMEOUT)`) minus `LOOP_ANSWER_MARGIN` (30s). The 30s leaves room for the
  wrap-up call itself.
- **Measuring rounds.** `run_loop(..., deadline=...)` measures, at the start of each round, how
  long the previous round took, from one model call's start to the next. That covers the model
  call plus the tool calls it made.
- **When it wraps up.** If `now + 2 × last round > deadline`, there isn't room for a normal round
  followed by an answer. That round becomes the wrap-up call, and the prompt gets a `budget` key:

  > Time budget nearly used: this is your last turn. Reply now with action done and your final
  > answer, using only what is in history and notes. Say what you could not finish. Do not call
  > any tool.

- **Afterwards.** If the model answers, the run ends `done` and the task can pass. If it doesn't,
  the run ends `time_budget` with the error `time budget: no final answer on the wrap-up call`,
  rather than an anonymous `timed out`. The run's context notes record the seconds left and how
  long the last round took.
- **One shared rule.** Tokens and time use the same single wrap-up call. Whichever runs short
  first triggers it, and the prompt, error and `ended` value (`token_budget` or `time_budget`) say
  which budget it was. The token-budget wording is unchanged.

## Worked example

The loop starts with 900s, so its deadline is 870s. Each round takes about 120s.

| Round starts at | Check: now + 2×120 > 870? | What happens |
|---|---|---|
| 0, 120 … 480 | no | normal rounds |
| 600 | 840, no | normal round |
| 720 | 960, yes | **wrap-up call**, answer at ~840s |

Before the fix, the round at 840 would have started and been killed at 900.

## Settings

| Name | Value | Where | Purpose |
|---|---|---|---|
| `TIME_MARGIN` | 20s | `central_runner.py` | kept back from the evaluator's time left for every task |
| `LOOP_ANSWER_MARGIN` | 30s | `central_runner.py` | loop deadline sits this far inside the loop task's limit |
| `MIN_TASK_TIMEOUT` | 5s | `central_runner.py` | floor for a capped task limit |
| `LOOP_TIMEOUT` | 900s | `central_runner.py` | unchanged; still the loop's upper limit |

## Limits

- **The first round never wraps up.** There is no measured round yet to compare against.
- **One long call can still be cut off.** The loop decides only between calls, and the
  `asyncio.wait_for` limit still applies. If a single model or tool call runs past the task
  limit, the task still times out.
- **The 2× rule is a guess.** A sudden slow round can still overrun; that is what the 30s margin
  is for.
- **Not yet run against the real evaluator.** See Verification.

## Verification

- Full offline suite on the committed tree: 418 passed.
- `python -m pipeline_agent.central_runner --dry-run`, with and without
  `AGENTSWITCH_SECONDS_LEFT=600`: 4/4, and the runner printed `evaluator time left: 600s`.
- A throwaway script (not part of the test suite) checked:
  - `evaluator_deadline` with seconds, epoch, UTC `Z`, IST offset, missing and junk values;
  - a slow fake model against a 1.5s deadline: it got the "Time budget nearly used" prompt and
    ended `done`.
- Still to do: a local Keystone + Suryodaya rehearsal with `AGENTSWITCH_SECONDS_LEFT` set, then the
  next central run.

## Testing

Where the behaviour is easiest to pin down:

- **Wrap-up answered.** A fake LLM that keeps calling tools until it sees the `budget` key, then
  answers: the run ends `done` and the last prompt contains "Time budget".
- **Wrap-up ignored.** A fake LLM that never answers: `ended == "time_budget"` and the error
  contains "time budget".
- **No deadline.** `deadline=None` gives the old behaviour.
- **Parsing.** `evaluator_deadline({...}, now=...)` with each input format, and `None` for missing
  or junk values.
- **Capping.** Set `central_runner.DEADLINE` and check that `task_timeout(900)` comes back capped,
  and never below 5.

For deterministic tests, monkeypatch `time.monotonic` in `pipeline_agent.harness.loop` rather than
sleeping. `tests/test_loop_wrapup.py` already has the fake-LLM pattern (`run_with`) to copy.
