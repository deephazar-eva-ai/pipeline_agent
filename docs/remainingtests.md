# Remaining tests (as of 2026-10-23)

Deadline: **2026-10-24 23:59 IST**. 
Code references are to `main` @ `c516205`.

## Ground rules

- **Where to put them.** Offline tests go in a new module of your own, e.g.
  `tests/test_team_remaining_cases.py`, or extend `tests/test_pipeline_enhancements.py`.
  Live verifiers go in `tasks/`, next to `mandatory_refusal_task.py`.
- **Reuse your own doubles.** `tests/test_pipeline_enhancements.py` already has
  `RecordsClient` (an in-memory client: get by id, list with equality filters, paging)
  and `NOW = 2026-10-07 12:00 UTC`. `tests/test_time_wrapup.py` has `ScriptedLLM` and
  the `loop_clock` fixture. `ActivityIndex` can be built directly with keyword
  arguments, e.g. `ActivityIndex(last_done_by_party={"p": "2026-09-02"})`.
- **Run.** `.venv/bin/pytest tests -q` (430 tests pass today).
- **Afterwards.** Tell me which tests are yours. I'll update the docs only: the
  scored-matrix table in `README.md`, `tests/README.md`, `TEST_CASES.md` and
  `EVIDENCE.md`, the same way as for `test_time_wrapup.py`. I won't edit the test bodies.

## Priority and size

| # | Area | Matrix case | Kind | Tests | Effort | Why |
|---|---|---|---|---|---|---|
| T1 | Contact classification | **No recent contact** | offline | 10–12 | ~1 h | Case marked "Not started" in the README matrix; pure functions, easiest points |
| T2 | Re-read before write | **Concurrent change** | offline | 5 | ~1.5 h | Case marked "Not started"; needs one small scripted client |
| T3 | Company-day date guard (`136733a`) | — (regression) | offline | 5 | ~30 min | Pure function; the existing test is AI-written, so it doesn't score |
| T4 | Time budget in the central runner | — (regression) | offline | 4 | ~1 h | Paths not in your `test_time_wrapup.py` |
| T5 | Live verifiers | Rotting deal, no recent contact, existing/missing next action | live | 1–3 tasks | ½–1 day | Matrix says "No live verifier yet"; the rubric wants verifiers that read the database |

If time is short, do T1 → T2 → T3 in that order (about 3 hours, ~20 tests). T4 is
the next easiest. T5 is the most valuable for the "harness rigor" grade, but it is
also the most work, and the write cases need consent.

---

## T1. No recent contact (offline)

**Matrix case:** "Correct contact classification, with date and channel evidence."

### What the code does

- `workflow.is_contact(activity)` (`src/pipeline_agent/agent/workflow.py:58`): True only
  when `type` is one of `call`, `meeting`, `email` (`CONTACT_TYPES`, line 55) **and**
  `outcome` does not start with "cancel" (case-insensitive, leading spaces ignored).
- `workflow.classify_contact(now, threshold_days, last_contacted, basis="deal")`
  (line 801) is a pure function. It returns `(ContactStatus, evidence_dict)`. The
  statuses are in `agent/contract.py:20`.
  - `last_contacted is None` → `NEVER_CONTACTED`, evidence `{"last_contacted": None, "basis": basis}`.
  - An unparseable date → `INSUFFICIENT_EVIDENCE`, evidence `reason: "unparseable date"`.
  - A future date (elapsed < 0) → `INSUFFICIENT_EVIDENCE`, evidence `reason` mentions
    "completion date is in the future".
  - elapsed **>** threshold → `NOT_CONTACTED_SINCE_THRESHOLD`; elapsed ≤ threshold →
    `RECENTLY_CONTACTED`. Evidence has `last_contacted`, `days_since`, `basis`.
  - Days are counted in the timezone of `now` (`days_since` → `local_day`, line 140),
    not as a UTC difference.
- `workflow.find_uncalled(deals, index, now, threshold_days, too_new=None)` (line 1030)
  returns one row per **party** with open pipeline. **Only `type == "call"` counts**
  (an email or a meeting does not). Closed deals and deals without `party_id` are
  skipped. Never-called parties sort first, then the longest silence.

### Scenarios (`now = 2026-10-07 12:00 UTC`, threshold 30 unless stated)

| ID | Call | Input | Expected |
|---|---|---|---|
| NC-01 | `classify_contact` | `last_contacted=None` | `NEVER_CONTACTED`; evidence `last_contacted` is None, `basis` echoed |
| NC-02 | `classify_contact` | `"2026-09-07"` (30 days) | `RECENTLY_CONTACTED`, `days_since == 30` (boundary: equal is still recent) |
| NC-03 | `classify_contact` | `"2026-09-06"` (31 days) | `NOT_CONTACTED_SINCE_THRESHOLD`, `days_since == 31` |
| NC-04 | `classify_contact` | `"2026-10-09"` (future) | `INSUFFICIENT_EVIDENCE`, evidence has a `reason` |
| NC-05 | `classify_contact` | `"not-a-date"` | `INSUFFICIENT_EVIDENCE`, `reason == "unparseable date"` |
| NC-06 | `classify_contact` | `basis="party"` with any date | evidence `basis == "party"` (the channel/linkage evidence the matrix asks for) |
| NC-07 | `classify_contact` | `now = 2026-10-07 03:00` in **America/New_York** (07:00 UTC), contact `"2026-10-06T23:30:00-04:00"` | `days_since == 1` (company day), not 0 or 2 |
| NC-08 | `is_contact` | `{"type": "call", "done": true, "outcome": "Cancelled"}` | False; also `" cancelled by customer"` → False |
| NC-09 | `is_contact` | `type` `email` / `meeting` / `call` → True; `task` / `note` → False | as stated |
| NC-10 | `find_uncalled` | Two open deals A and B on party P; `index.last_call_by_party = {"P": <14 days ago>}`; P's latest email (2 days ago) is only in `last_unlinked_contact_by_party` | P is **not** listed (called within 30). Change the call to 40 days ago: exactly **one** row for P (not one per deal), `days_since == 40`, `open_deal_ids == [A, B]`. The recent email does not rescue it |
| NC-11 | `find_uncalled` with `too_new=[]` | Party Q never called; its only contact (`first_contact_by_party`) is 10 days old | Q is **not** in the returned rows, and is in `too_new` with `days_since == 10` (a new customer has not gone 30 days uncalled) |
| NC-12 | `find_uncalled` | Party R never called, relationship 60 days old; party S called 45 days ago | Both listed; R first (never called sorts first), then S |

`find_uncalled` reads calls through `index.last_called(party_id, deal_ids)`, which uses
`last_call_by_party` / `last_call_by_deal`. The relationship age comes from
`first_contact_by_party` / `first_contact_by_deal` (`relationship_days`, line 1016).
These are different fields from `last_done_by_party`.

---

## T2. Concurrent change (offline)

**Matrix case:** "Agent re-reads; no stale or duplicate write."

### What the code does

`workflow.determine_next_action(client, deal, *, mode, run_id, now, index, ctx=None)`
(line 839) returns `(ActionStatus, record_or_proposal, reasons)`. In
`ExecutionMode.CREATE_NEXT_ACTIONS`, just before writing (lines 976–1013) it:

1. If `ctx` is given: `client.get("Deal", id)`. If the deal is now `closed*` or its
   stage changed → returns `RECOMMENDED` (no write), reason "the deal moved to …".
2. `client.list_("Activity", filters={"deal_id": id, "done": False}, limit=1)`. If any
   are found → `EXISTING`, reason "appeared between the check and the write".
3. `client.list_("Activity", filters={"party_id": party, "done": False}, limit=100)`. If
   any has **no** `deal_id` → `EXISTING` (the V2 Maumee River case).
4. Otherwise `client.create("Activity", proposed)` → `CREATED`.

In `ExecutionMode.PROPOSE` it never writes and returns `RECOMMENDED`.

### Test double you need

A client (e.g. a subclass of your `RecordsClient`) that returns **nothing** from the
first read of open activities (so the deal looks unactioned), then returns the
"concurrent" record on the re-read. It must also count `create` calls. Pass an
`index` with no open activity for the deal, so the first check finds none.

### Scenarios (deal `D1`, stage `proposal`, party `P1`; mode `CREATE_NEXT_ACTIONS`)

| ID | Setup | Expected |
|---|---|---|
| CC-01 (control) | Nothing appears on the re-read | `CREATED`; `create` called exactly once; the payload's `deal_id == D1` and its description contains `created_by=pipeline_agent` |
| CC-02 | Re-read by `deal_id` returns an open Activity on D1 | `EXISTING`, the returned record is that Activity, `create` **not** called, reason mentions "appeared between the check and the write" |
| CC-03 | Deal re-read empty; party re-read returns an open Activity on P1 with **no** `deal_id` | `EXISTING`, no create |
| CC-04 | Party re-read returns an open Activity on P1 **linked to another deal** D2 | Not a blocker: `CREATED` (only deal-less party rows block) |
| CC-05 | `ctx` given (`PipelineContext(today="2026-10-07")`); `get("Deal", D1)` now returns stage `closed_lost` (or `negotiation`) | `RECOMMENDED`, reason "the deal moved to …", no create |
| CC-06 | Same as CC-02 but mode `PROPOSE` | `RECOMMENDED`, no re-read needed, no create |

`PIPELINE_PAUSE_BEFORE_WRITE_SECONDS` is the live hook for this case, used in IT-01
Run C. Leave it unset offline.

---

## T3. Company-day date guard (offline, commit `136733a`)

### What the code does

`harness.loop.call_shape_error(tool, arguments, *, derived_tools, today=None)`
(`src/pipeline_agent/harness/loop.py:152`) returns an error string, or None when the
call is acceptable. For `create` on `Activity` with a `due_date` (lines 190–200):

- not `YYYY-MM-DD` → `"Activity.due_date must be YYYY-MM-DD"`;
- earlier than `today` → a message containing "before the company's today (<today>)";
- `today=None` falls back to the **UTC** date.

`run_loop` reads the company clock and passes `today=company_today` (lines 483–496,
call site line 600).

Arguments shape: `{"entity": "Activity", "data": {"subject": "...", "type": "call",
"due_date": "...", "done": false}}` with `tool="create"`, `derived_tools=False`.

### Scenarios

| ID | today | due_date | Expected |
|---|---|---|---|
| DG-01 | `date(2026,10,17)` | `2026-10-17` | None (today is allowed) |
| DG-02 | `date(2026,10,17)` | `2026-10-16` | error containing `2026-10-17` |
| DG-03 | `date(2026,10,17)` | `2026-10-18` | None (future is allowed for a new Activity) |
| DG-04 | `date(2026,10,17)` | `17/10/2026` | `"Activity.due_date must be YYYY-MM-DD"` |
| DG-05 | today = the IST day at 04:25 IST (UTC is still the day before) | IST date | None. This is the N489 case: it failed before `136733a`. A due date one day earlier is still refused |

The AI-written `test_activity_due_date_guard_uses_the_company_day_not_utc` in
`tests/test_nonwrite_workflows.py:104` covers part of this but doesn't score. Yours
should stand on its own.

---

## T4. Time budget in the central runner (offline)

Your `tests/test_time_wrapup.py` (11 tests) covers the loop's time wrap-up, deadline
parsing (`evaluator_deadline`) and the `task_timeout` cap and floor. Not yet covered:

| ID | Code | Scenario | Expected |
|---|---|---|---|
| TB-01 | `central_runner.run_task(results, task, timeout, body)` (line 224) | `body` sleeps longer than `timeout` | Returns False; `results` has one row for the task with `passed == False` and the evidence ends in `[<elapsed>s]`. A timeout is a failed row, not a crash |
| TB-02 | `run_task` | `body` raises `RuntimeError("x")` | One failed row whose evidence comes from `describe_error` |
| TB-03 | `Results(path, "ks").record(...)` (lines 131–158) | Record 2 rows to a `tmp_path` file, reading the file after each | `record` saves by itself, so after the first call the file already has 1 row and after the second it has 2: a crash keeps the finished rows. Ids are `"ks:<task_id>"`, `passed` is a bool, `score` is 1.0/0.0, `summary` reads `"ks: 1/2 tasks passed"`; no `.tmp` file is left behind |
| TB-04 | `check_loop` (line 296) with a stub loop result | `run.ended == "time_budget"` (or `"token_budget"`), reads ≥ 1, no writes | Task fails (`passed is False`); evidence contains `ended=time_budget`. With `ended == "done"`, reads ≥ 1 and 0 writes, it passes |

For TB-01/02, monkeypatch `central_runner.DEADLINE = None` so `task_timeout` returns
the timeout unchanged. For TB-04, monkeypatch `central_runner.run_loop` with an async
stub that returns an object with the fields `check_loop` reads (`ended`, `calls`,
`steps`, `created_record_ids`, `updated_record_ids`, `unsupported_figures`,
`input_tokens`, `output_tokens`, `error`).

---

## T5. Live verifiers (against AgentSwitch)

The rubric wants "verifiers that read the database directly, not the agent's own
prose". Today only the refusal task does that. Follow its pattern
(`tasks/mandatory_refusal_task.py`): a `run` step writes artifacts, then a `verify`
step recomputes the verdict from the **platform**, with exit codes 0 pass / 1 fail /
3 blocked.

### V-A. Rotting deal + no recent contact, read-only (recommended; no consent needed)

1. **Run:** `PYTHONPATH=src python3 -m pipeline_agent.runner --task canonical --mode live`
   (propose mode). The run folder `runs/<ts>__canonical__live__<id>/result.json` holds
   the answer.
2. **Verify, directly from REST/MCP:** read `CRMPreferences.deal_rot_days`, all open
   `Deal` rows and all `Activity` rows yourself. Then for each deal in the answer:
   - your own days since the last completed, non-cancelled call/meeting/email
     (company timezone) > threshold → the deal must be listed as rotting;
   - pick 3–5 deals **not** in the answer and confirm they are fresh, closed, or
     supplier/employee-only (`non_sales_reason`);
   - for each uncalled customer in the answer, confirm there is no `type == "call"`
     done in the last 30 days for that party;
   - snapshot `Activity`/`Deal` counts before and after the run: **no change** (zero writes).
3. **Asserts:** set equality on rotting deal ids (or an explicit, explained tolerance
   for rows changed by other teams during the run: re-read those rows and exclude
   any whose `updated_at` is after the run started), `days_since` within ±0, zero writes.

Expected today: Keystone 25 rotting / 8 uncalled, Suryodaya 54 / 21 (central Run 7).
Don't hard-code these numbers; the shared book changes.

### V-B. Existing / missing next action, write (needs your consent)

Reuse the IT-01 fixture tooling (`tasks/it01/it01.py seed|cleanup`, tagged
`[team07-test …]`). Seed one deal with an open Activity (existing) and one with none
(missing). Run `--exec-mode create-next-actions --deal-id <both>`. Then verify from the
platform:

- missing: **exactly one** new open Activity on that deal, `deal_id` set, description
  contains `created_by=pipeline_agent`, due date ≥ the company's today;
- existing: **no** new Activity on that deal; the result links the existing id;
- rerun the same command: **zero** new Activities (idempotent);
- cleanup with `it01.py cleanup`.

The IT-01 `verify.py` was written with Claude, so a scored verifier must be your own,
even if it checks the same things.

### V-C. Concurrent change, live (optional)

As V-B with `PIPELINE_PAUSE_BEFORE_WRITE_SECONDS=40`. During the pause, add an open
Activity on the deal by hand (UI or REST). Verify that the agent wrote **nothing** on
that deal, and that its result says EXISTING with "appeared between the check and the
write".

---

## T6. Cancelled Activity flag (offline, Release 13 / N478, added 2026-10-23)

Release 13 (2026-10-23) added `Activity.cancelled` (boolean, default false; also a
list filter). Older rows were **not** migrated: on 2026-10-23 every row had
`cancelled=false`, including 127 Keystone and 9 Suryodaya rows whose `outcome` starts
with "Cancelled". The code fix (`main`, after `506444b`) does this:

- `workflow.is_cancelled(activity)` (new) is True only when `activity["cancelled"] is True`.
- `workflow.is_contact(activity)` returns False when `is_cancelled` is True. The
  "cancel" outcome prefix stays as the fallback for the old rows.
- `load_activity_index`: an open (`done` false) row with `cancelled=True` is **not** an
  open next action, so it isn't put in `open_by_deal` / `open_by_party` /
  `overdue_open_by_deal`.
- `determine_next_action` (create mode) re-read before the write: cancelled rows are
  skipped on both the deal re-read (limit raised from 1 to 100 for this) and the
  party re-read.
- `harness.loop.open_activity_duplicate`: the same, for the loop's own guard.

Live check after the fix (2026-10-23 22:20 IST, read-only): Keystone 25 rotting / 8
uncalled and Suryodaya 54 / 21, the same as central Run 7. No live row has the flag
yet, so nothing changed.

### Scenarios

| ID | Call | Input | Expected |
|---|---|---|---|
| CX-01 | `is_contact` | `{"type": "call", "done": true, "cancelled": true, "outcome": "Spoke to Pat"}` | False: the flag wins over an ordinary outcome |
| CX-02 | `is_contact` | `{"type": "call", "done": true, "cancelled": false, "outcome": "Cancelled"}` | False: an old row, caught by the prefix fallback |
| CX-03 | `is_contact` | `cancelled` false or missing, `outcome` "Answered" | True |
| CX-04 | `is_cancelled` | `cancelled` True → True; False, missing or None → False | as stated |
| CX-05 | `load_activity_index(client, now=NOW)` | Deal D: one **open** call with `cancelled: true`, due tomorrow | `index.open_by_deal` has no D, so the deal looks unactioned. Add a second open, not-cancelled row on D and it **is** in `open_by_deal` |
| CX-06 | `load_activity_index` | Party P (no deal): one open `cancelled: true` row | `index.open_by_party` has no P |
| CX-07 | `load_activity_index` | Deal D, party P: a **done** call 5 days ago with `cancelled: true`, outcome "Spoke to Pat" | Not contact: `last_contact_by_deal` has no D, `last_call_by_party` has no P, and `noncontact_done` is 1 |
| CX-08 | `determine_next_action`, mode `CREATE_NEXT_ACTIONS` | The deal re-read (`deal_id`, `done: False`) returns only a `cancelled: true` row | `CREATED`; `create` called once (before the fix this was EXISTING) |
| CX-09 | `determine_next_action`, create mode | The deal re-read returns a cancelled row **and** a live open row | `EXISTING`, and the returned record is the **live** row, not the cancelled one |
| CX-10 | `determine_next_action`, create mode | The party re-read returns only a deal-less `cancelled: true` row | `CREATED` |
| CX-11 | `harness.loop.open_activity_duplicate(client, {"entity": "Activity", "data": {"deal_id": D, "subject": "x", "done": false}})` | `list_` on D returns only a cancelled open row; the deal's party has nothing open | `None`, so the create may go ahead |
| CX-12 | `open_activity_duplicate` | Same, but `list_` on D also returns a live open row | A message containing "already has an open Activity" and the **live** row's id |

**How to build them:**
- **CX-05 … CX-07:** `tests/keystone_support.py` has `ActivityClient(activities)`, a
  client whose `list_("Activity")` returns the given rows, already used as
  `asyncio.run(load_activity_index(ActivityClient(rows), now=now))`. Its
  `activity(id=..., due_date=..., deal_id=..., party_id=..., done=1, type="call", **extra)`
  builder takes `cancelled=True` and `outcome=...` as extras (`done` is 0/1 there, as on
  the live platform).
- **CX-11 / CX-12:** when the arguments carry `deal_id` but no `party_id`,
  `open_activity_duplicate` calls `client.get("Deal", D)` to find the customer, so your
  double must answer that `get` (a deal with a `party_id` whose re-read is empty).
- **CX-08 … CX-12:** reuse the T2 client from your `test_remaining.py`, with re-read
  rows that carry `"cancelled": True`.
- **Shape matters:** the re-read uses `filters={"deal_id": D, "done": False}`. So if
  your double filters by equality, give the rows `"done": False`, and do not add
  `cancelled` to the filter: the code filters cancelled rows itself.

Writing CX-01 … CX-12 gives about 12 tests (~1 h).

---

## Checklist

- [ ] T1 written and passing (NC-01 … NC-12)
- [ ] T2 written and passing (CC-01 … CC-06)
- [ ] T3 written and passing (DG-01 … DG-05)
- [ ] T4 written and passing (TB-01 … TB-04)
- [x] T5 V-A live verifier run on at least one instance; artifacts kept (`tests/test_live_verifier.py`, both instances passed 2026-10-24, `tests/live_evidence/`)
- [x] T6 written and passing (CX-01 … CX-12), `tests/test_remaining.py`, 2026-10-23
- [ ] Tell Claude the module names, so the docs (README matrix, tests/README, TEST_CASES, EVIDENCE) get updated
- [ ] Commit and push to `main`

Details worth knowing before you start:

Boundaries: exactly 30 days still counts as "recently contacted" and 31 doesn't. Uncalled customers count only type == "call", so an email doesn't count. A cancelled call is never contact.
T2 test double: it needs one small in-memory client. The first read must return no open activity, the re-read must return the "concurrent" one, and the client must count create calls. You can build it from the RecordsClient already in your test_pipeline_enhancements.py.
T3: the existing company-day test was written by me, so it doesn't score. Yours needs to stand on its own.
T5 V-A: this read-only live verifier helps the "harness rigor" grade most without needing consent. Run the canonical task in propose mode, then recompute rot and uncalled customers yourself from REST, and confirm nothing was written. V-B (next actions with writes) needs your consent and the IT-01 fixtures.
