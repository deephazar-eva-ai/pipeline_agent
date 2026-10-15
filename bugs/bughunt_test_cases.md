# Bug-hunt agent fixes (2026-10-13) — changes and test cases to write

Two platform bugs filed on 2026-10-13 reach the Pipeline Agent. This file records the agent
changes made for them and specifies the tests that cover those changes.

Run the suite from `pipeline_agent/` with
`.venv/bin/python -m pytest -q`.

Branch `it01-run3-fixes`; the changes are uncommitted as of writing. `central-eval` is
unchanged (frozen until the 2026-10-15 central submission). Baseline after the change:
376 tests pass.

Of the 10 bugs filed on 2026-10-13, the other 8 do not reach the agent:

| Bug | Why it does not reach the agent |
|---|---|
| `e1d9ee8c` | Already guarded: the request guard's `other_users_task` rule, plus the loop's write policy |
| `f2fa8b84` | The loop never passes server schemas, so no defaults are injected |
| `64f0e8a1`, `eb9ae415` | The agent only relays the platform's `grand_total` |
| `5ee2f965`, `7e508b8e`, `d8a903e9`, `7bd63624` | The agent does not use these |

## 1. Bug 31589ab3 — deals and leads on the company's own employees

**Platform bug.** Suryodaya's seed book has 25 deals and 53 leads on employee-only parties
(5 won, ₹3.99M). The platform lets any party become an employee while it has open deals.

**Agent impact before the fix.** Only supplier-only parties were excluded. On Suryodaya,
20 of 90 open deals (₹54.5 lakh, 16 over the threshold) sat on staff, so the rotting and
uncalled answers listed colleagues as customers.

**Change.**
- `workflow.py`:
  - [L1146–1162](../src/pipeline_agent/agent/workflow.py#L1146): new `EMPLOYEE_ROLE`,
    `SUPPLIER_REASON`, `EMPLOYEE_REASON`, `is_employee_only()` and `non_sales_reason()`.
  - [L1362](../src/pipeline_agent/agent/workflow.py#L1362): the canonical task excludes an
    open deal when `non_sales_reason` returns a reason, and records it in `excluded_deals`
    with that reason.
  - [L1468](../src/pipeline_agent/agent/workflow.py#L1468): the summary counts supplier and
    employee exclusions separately.
- `pipeline_checks.py` [L488–493](../src/pipeline_agent/agent/pipeline_checks.py#L488):
  leads on supplier-only **or** employee-only parties are skipped.
- `contact_status.py` [L200–216](../src/pipeline_agent/agent/contact_status.py#L200):
  `must_report` collapses employee exclusions into one line, as it does for test fixtures.
- Rule: a party is employee-only when its active roles include `employee` and include
  neither `customer` nor `prospect`. Unknown or empty roles are never excluded. Supplier
  takes precedence when a party is both supplier and employee.

**Verified live (read-only propose run, 2026-10-13).**
- Suryodaya: 20 deals excluded with the employee reason; 16 supplier exclusions
  unchanged; uncalled = 20. `must_report` contains "20 open deal(s) on the company's own
  employees excluded".
- Keystone: no change (no employee-party deals).

### BH-EMP-01 — employee-only roles are classified as non-sales

| | |
|---|---|
| **Technique** | equivalence / decision table |
| **Target** | `workflow.is_employee_only`, `workflow.non_sales_reason` |
| **Protects** | 31589ab3 |
| **Door** | unit |
| **Writes** | no |

**Input / expected** (parametrise):

| roles | `is_employee_only` | `non_sales_reason` |
|---|---|---|
| `{"employee"}` | True | `EMPLOYEE_REASON` |
| `{"employee", "contact"}` | True | `EMPLOYEE_REASON` |
| `{"employee", "customer"}` | False | None |
| `{"employee", "prospect"}` | False | None |
| `{"employee", "supplier"}` | True | `SUPPLIER_REASON` (supplier wins) |
| `{"supplier"}` | False | `SUPPLIER_REASON` |
| `{"customer"}` | False | None |
| `set()` | False | None |
| `None` | False | None |

**Must NOT be true:** unknown (`None`) or empty roles are never excluded.

### BH-EMP-02 — canonical task excludes an employee-only deal and lists it

| | |
|---|---|
| **Technique** | scenario |
| **Target** | `workflow.run_canonical_task` |
| **Protects** | 31589ab3 |
| **Door** | stub (monkeypatch `load_party_roles`, `load_deals`, `load_activity_index`, `load_rot_threshold`, `checks.load_company_clock`, `checks.load_context`, the same pattern as `test_keystone_agent_fixes.py` L241) |
| **Writes** | no |

**Precondition / fixture.** Three open deals, all aged past the threshold with no
contact:
- deal E on party with roles `{"employee"}`;
- deal C on `{"customer"}`;
- deal M on `{"employee", "customer"}`.

**Expected — guard ACTIVE.**
- `excluded_deals` holds exactly E, with reason `EMPLOYEE_REASON`.
- E is in neither `deals` (rotting) nor `uncalled`.
- The summary contains "1 open deal(s) on the company's own employees excluded".

**Expected — guard INACTIVE.** C and M are rotting and uncalled as normal.

**Must NOT be true:** M (employee who is also a customer) is not excluded.

### BH-EMP-03 — unreadable Party excludes nothing and says so

| | |
|---|---|
| **Technique** | error path |
| **Target** | `workflow.run_canonical_task` |
| **Protects** | 31589ab3 (degraded mode) |
| **Door** | stub (`load_party_roles` returns `(None, "Party is not readable from this seat")`) |
| **Writes** | no |

**Expected.**
- `excluded_deals` is empty, and the employee deal stays in the answers.
- The summary contains "supplier-side and employee deals could not be identified and are
  NOT excluded".

### BH-EMP-04 — employee-only leads are not flagged as needing action

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `pipeline_checks` leads-needing-action (`ctx.parties` roles) |
| **Protects** | 31589ab3 |
| **Door** | unit (`PipelineContext` with parties and leads) |
| **Writes** | no |

**Input.** Three `qualified` leads with no deal, on parties with roles `{"employee"}`,
`{"employee", "prospect"}` and `{"prospect"}`.

**Expected.** The employee-only lead is absent from `leads_needing_action`; the other two
are present.

### BH-EMP-05 — must_report collapses employee exclusions to one line

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `contact_status.analyse` → `must_report` |
| **Protects** | 31589ab3 |
| **Door** | stub (the `analyse` fixture pattern in `test_run3_bugfix_regressions.py`) |
| **Writes** | no |

**Input.** `excluded_deals` with 20 rows of reason `EMPLOYEE_REASON`, 1 row of
`SUPPLIER_REASON` and 2 test-fixture rows.

**Expected.** `must_report` contains:
- exactly one line "20 open deal(s) on the company's own employees excluded";
- one line for the supplier deal;
- one "2 open test-fixture deal(s) excluded" line.

**Must NOT be true:** no individual employee deal title appears in `must_report`.

## 2. Bug 66f96caa — company-wide CRM settings writable by any seat

**Platform bug.** The single `CRMPreferences` record (incl. `deal_rot_days`) is writable
from every CRM seat on the shared book.

**Agent impact before the fix.** The agent used any positive live `deal_rot_days`. Another
team setting it to 1 or 365 would silently change every rotting and uncalled answer.

**Change.**
- `workflow.py` [L184–220](../src/pipeline_agent/agent/workflow.py#L184):
  - `MIN_ROT_DAYS = 7`, `MAX_ROT_DAYS = 90`.
  - `load_rot_threshold` returns `RotThreshold`, a tuple subclass that still unpacks as
    `(days, used_live)` and adds `.note`.
  - A live value outside 7–90 → `(30, False)` with a note naming the value.
  - Missing, non-numeric or ≤ 0 → `(30, False)` with an empty note (unchanged behaviour).
- `workflow.py` [L1265](../src/pipeline_agent/agent/workflow.py#L1265),
  [L1481–1486](../src/pipeline_agent/agent/workflow.py#L1481):
  - the rejection note becomes the answer's `unavailable_note` and is added to the
    summary;
  - when a live value is accepted but differs from 30, the summary says it comes from the
    live company-wide setting.
- `contact_status.py` [L204](../src/pipeline_agent/agent/contact_status.py#L204): a
  non-empty `unavailable_note` is the first `must_report` line. This also applies to the
  existing "not readable" and "not a positive number" notes.

**Verified.**
- Scratch check (not a test): 6 and 91 rejected; 7, 30, 45 and 90 accepted; 0, "thirty"
  and None fall back with no note.
- Live, both instances: threshold 30, no note.

### BH-ROT-01 — threshold range boundaries

| | |
|---|---|
| **Technique** | boundary value |
| **Target** | `workflow.load_rot_threshold` |
| **Protects** | 66f96caa |
| **Door** | unit (fake client whose `list_("CRMPreferences")` returns `{"records": [{"deal_rot_days": v}]}`) |
| **Writes** | no |

**Input / expected** (parametrise):

| `deal_rot_days` | days | used_live | note |
|---|---|---|---|
| 6 | 30 | False | contains "is 6 days, outside the accepted 7-90" |
| 7 | 7 | True | "" |
| 30 | 30 | True | "" |
| 90 | 90 | True | "" |
| 91 | 30 | False | contains "is 91 days" |
| 365 | 30 | False | contains "is 365 days" |
| 1 | 30 | False | contains "is 1 days" |
| "45" / 45.0 | 45 | True | "" |

**Also assert:**
- `days, live = result` unpacks to exactly two values;
- `result.note` is a string.

### BH-ROT-02 — unusable values fall back without a range note

| | |
|---|---|
| **Technique** | equivalence (invalid) |
| **Target** | `workflow.load_rot_threshold` |
| **Protects** | 66f96caa (no regression of the earlier `_positive_days` fix) |
| **Door** | unit |
| **Writes** | no |

**Input.** `0`, `-5`, `"thirty"`, `None`, and an empty `records` list.

**Expected.** `(30, False)` and `note == ""` for each.

**Must NOT be true:** the run does not crash on a string.

### BH-ROT-03 — out-of-range value reaches the summary and must_report

| | |
|---|---|
| **Technique** | scenario |
| **Target** | `workflow.run_canonical_task`, `contact_status.analyse` |
| **Protects** | 66f96caa |
| **Door** | stub (monkeypatch `load_rot_threshold` to return `workflow.RotThreshold(30, False, "<note>")`) |
| **Writes** | no |

**Expected.**
- `answer.unavailable_note == "<note>"`.
- `answer.summary` contains the note.
- `deal_rot_days == 30`.
- `contact_status` `must_report[0] == "<note>"`.

**Must NOT be true:** a plain `(30, True)` pair (the old monkeypatch shape) still works and
adds no note. This keeps `test_keystone_agent_fixes.py` L241 valid.

### BH-ROT-04 — an accepted non-default threshold is disclosed

| | |
|---|---|
| **Technique** | decision table |
| **Target** | `workflow.run_canonical_task` summary |
| **Protects** | 66f96caa |
| **Door** | stub |
| **Writes** | no |

**Input / expected.**

| `load_rot_threshold` returns | Summary contains "live CRMPreferences.deal_rot_days" |
|---|---|
| `RotThreshold(45, True)` | yes, with "45-day threshold" |
| `RotThreshold(30, True)` | no |
| `RotThreshold(30, False)` (unreadable) | no; the unavailable note is used instead |

**Must NOT be true:** with an accepted 45, the answer is not computed with 30
(`deal_rot_days == 45`).

## Known limits

- The 7–90 range is a judgement, not a platform rule. If the seat owner sets a deliberate
  value outside it, widen `MIN_ROT_DAYS` / `MAX_ROT_DAYS` and update BH-ROT-01.
- On Suryodaya, the 16 supplier exclusions are still one `must_report` line each
  (unchanged behaviour). They are not collapsed like employee exclusions.
- Bug 31589ab3 itself (seed deals on staff) remains a platform defect. The agent now
  excludes those deals; it does not correct them.
