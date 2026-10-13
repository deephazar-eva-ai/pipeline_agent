# IT-01 Run 3 fixes: test coverage and test cases still to write

Written 2026-10-12. Fix commit `529f7c4` on `it01-run3-fixes`; test updates in `c0e7696`.
Background: `EAG_V3_capstone/docnotshared/complexjob_run3_bugfix.md` (what Run 2 found
and why each change was made); results in
[`docs/integration_testing_pipeline_run3.md`](../docs/integration_testing_pipeline_run3.md).

**Purpose.** This is a checklist for writing tests by hand. Each fix already has at least
one test for its main behaviour (from `c0e7696`). The cases below are the edge cases that
are not tested yet. The grading rubric gives AI-written tests zero, so **these are a list
to write from, not tests**.

The completed developer-style specifications are in
[run3_test_cases.md](run3_test_cases.md). The hand-written tests for them are in
[`tests/test_run3_bugfix_regressions.py`](../tests/test_run3_bugfix_regressions.py)
(`464aaa6`, 2026-10-13). They cover every case except R3-GA-05 and R3-GG-04, which wait
on the design decision marked ⚠️ below.

**Run:** `.venv/bin/python -m pytest -q` from `pipeline_agent/`.

**Two cases may find real bugs** (marked ⚠️). Decide what the behaviour should be before
you write those tests. If one fails, it is a finding, and its fix goes in after the
2026-10-15 central submission (`central-eval` is frozen until then).

---

## Summary

| # | Fix | Main code | Existing test(s) | Cases to add |
|---|---|---|---|---|
| 1 | G-A: only completed calls, meetings and emails count as contact | `is_contact()` | 2 | 6 (1 ⚠️) |
| 2 | G-B: "more than" the threshold | `assess_rot`, `classify_contact`, `find_uncalled` | 3+ | 2 |
| 3 | G-C: calendar days in the company's time zone | `local_day()`, `days_since()` | 1 | 4 |
| 4 | G-D: the agent's marker on a completed contact counts, and is flagged | `load_activity_index`, `contact_evidence_issues` | 2 | 4 |
| 5 | G-E: a future-dated contact gives `insufficient_evidence` | `ActivityIndex.last_contacted` | 2 | 3 |
| 6 | C-R6: `mismatched_links` names each row (canonical answer) | `run_canonical_task`, `contract.py` | 1 | 3 |
| 7 | C-R5/C-R6 in the loop: `must_report` in every response | `contact_status.py` | 1 | 4 |
| 8 | G-G: the loop refuses to create a completed Activity | `write_block_reason` | 1 | 4 (1 ⚠️) |
| 9 | IT-01 harness: cleanup closes the agent's own writes | `tasks/it01/it01.py cleanup` | 0 | 2 |

---

## 1. G-A: only completed calls, meetings and emails count as contact

**Code:** `CONTACT_TYPES` and `is_contact()` in
[`src/pipeline_agent/agent/workflow.py:58`](../src/pipeline_agent/agent/workflow.py#L58);
`noncontact_done` count and its summary sentence at
[`workflow.py:1485`](../src/pipeline_agent/agent/workflow.py#L1485).

**Rule:** a completed Activity is contact only if its `type` is `call`, `meeting` or
`email` and its `outcome` does not start with "cancel".

**Existing tests:** `test_completed_tasks_and_cancelled_calls_are_not_contact`,
`test_completed_email_and_meeting_count_as_contact` (`tests/test_keystone_agent_fixes.py`).

**To add:**
- [ ] The type check ignores case: `type="Call"` and `type="EMAIL"` count as contact.
- [ ] A completed row with no `type` (`None` or `""`) is not contact, and is added to `noncontact_done`.
- [ ] `type` `lunch`, `note` or `task` is not contact.
- [ ] Outcomes: `"cancelled"` and `"Cancelled - no answer"` are not contact. An outcome of `None` or `"Completed"` is contact.
- [ ] ⚠️ **Decide first.** The rule is `outcome.startswith("cancel")`, so a real call with outcome `"Cancellation terms agreed"` is **not** counted as contact. That is probably a bug.
- [ ] The summary includes "N completed task(s), cancelled call(s) or other non-contact activit(ies) were not counted" when `noncontact_done > 0`, and leaves the sentence out when it is 0.

## 2. G-B: "more than" the threshold, not "at least"

**Code:** `assess_rot` at [`workflow.py:381`](../src/pipeline_agent/agent/workflow.py#L381);
`classify_contact`; `find_uncalled`.

**Rule:** with the configured threshold (30), a deal or customer is flagged only after
**more than** 30 days. The platform-calibrated boundary (used when no threshold is
configured) keeps `>=`.

**Existing tests:** `test_exactly_threshold_days_is_not_rotting_or_uncalled`,
`test_contact_status_around_threshold`,
`test_uncalled_needs_more_than_threshold_days_since_last_call`, and the boundary tests in
`test_keystone_catalogue_gaps.py`.

**To add:**
- [ ] With `threshold_days=None`, the calibrated boundary still uses `>=`: `days == boundary` is flagged.
- [ ] A never-contacted deal, aged from when it opened, is **not** rotting at exactly 30 days and **is** rotting at 31. This is the Suryodaya case: its book is exactly 30 days old on 2026-10-12, so 64 deals and 34 customers flip on 2026-10-13.

## 3. G-C: day counts use calendar days in the company's time zone

**Code:** `local_day()` and `days_since()` at
[`workflow.py:140-165`](../src/pipeline_agent/agent/workflow.py#L140-L165).

**Rule:** a plain `YYYY-MM-DD` is already a company-local day. A timestamp is read as UTC
when it has no offset, and is moved into the company's zone before its date is taken.

**Existing test:** `test_contact_day_count_is_stable_throughout_the_company_day` (date-only
input, morning against evening).

**To add:**
- [ ] A naive timestamp is read as UTC: `"2026-10-12T02:00:00"` with `now` = 2026-10-11 22:00 America/New_York gives the local day 2026-10-11, and `days_since` = 0.
- [ ] A timestamp with an explicit offset (for example `+05:30`) is converted correctly.
- [ ] `""`, `None` and `"not-a-date"` return `None` from both functions.
- [ ] When `now` has no time zone, no conversion happens: the timestamp's own date is used.

## 4. G-D: the agent's marker on a completed contact counts, and is flagged

**Code:** `load_activity_index` at
[`workflow.py:716-724`](../src/pipeline_agent/agent/workflow.py#L716-L724);
`contact_evidence_issues` at [`workflow.py:1056`](../src/pipeline_agent/agent/workflow.py#L1056).

**Rule:** a completed contact-type row carrying `created_by=pipeline_agent` counts as
contact and is listed as data issue `agent_marker_on_completed_contact`, with the `run=` it
claims. The marker still keeps the agent's rows out of the rot bookkeeping.

**Existing tests:** `test_completed_call_with_agent_marker_counts_but_is_flagged`,
`test_completed_contact_with_agent_marker_still_counts`.

**To add:**
- [ ] A marker without `run=` gives `marker_run: None`, and the warning is still raised.
- [ ] The marker on a completed **task** is not flagged, and the row is counted as non-contact (`noncontact_done`).
- [ ] The marker on an **open** (not done) call is neither contact nor flagged.
- [ ] A party-only marked contact (no `deal_id`) flags every **open** deal of that party, and no closed deal.

## 5. G-E: a future-dated contact gives `insufficient_evidence`

**Code:** `ActivityIndex.last_contacted` at
[`workflow.py:542`](../src/pipeline_agent/agent/workflow.py#L542); the
`insufficient_evidence` issue in `contact_evidence_issues`.

**Rule:** when a deal's only completed contact is dated after today, `classify_contact`
says `insufficient_evidence` (not `never_contacted`) and a data issue is raised.

**Existing tests:** `test_future_dated_contact_alone_is_insufficient_evidence`,
`test_unparseable_contact_date_is_insufficient_evidence`.

**To add:**
- [ ] A future contact **plus** a real past contact on the same deal: the past date is used as last contact, and **no** `insufficient_evidence` issue is raised.
- [ ] A party-only future contact (no `deal_id`) gives the same result through the party link.
- [ ] A future contact on a closed deal raises no issue.

## 6. C-R6: `mismatched_links` names each mis-linked row (canonical answer)

**Code:** `CanonicalAnswer.mismatched_links` in `src/pipeline_agent/agent/contract.py`;
filled in `run_canonical_task`.

**Rule:** a completed activity whose `deal_id` is one customer's deal but whose `party_id`
is another customer is listed by name, and does not count as contact for that deal.

**Existing test:** `test_mismatched_contact_link_is_reported_with_names`.

**To add:**
- [ ] A row whose party is the deal's own party is not listed.
- [ ] A row with no `party_id` is not listed.
- [ ] The mis-linked row does not freshen the deal: last contact stays at the older correctly linked date (the D13 case, T−80).

## 7. C-R5 / C-R6 in the loop: `must_report` in every `contact_status` response

**Code:** [`src/pipeline_agent/agent/contact_status.py:200-214`](../src/pipeline_agent/agent/contact_status.py#L200-L214)
(`MUST_REPORT_CODES`, `MUST_REPORT_MAX = 25`).

**Rule:** every response, whichever section is asked for, carries `must_report`: excluded
deals plus the evidence problems (mis-linked rows, impossible dates, the agent's marker on
a completed contact).

**Existing test:** `test_view_includes_must_report_in_every_section`.

**To add:**
- [ ] Excluded test-fixture deals collapse into one line, "N open test-fixture deal(s) excluded", instead of one line each.
- [ ] More than 25 items: the list is cut at 25 and ends with "... and N more - page section data_issues".
- [ ] Only codes in `MUST_REPORT_CODES` appear. For example, a `no_owner` issue is **not** in `must_report`.
- [ ] With nothing to report, the `must_report` key is missing from the response.

## 8. G-G: the loop refuses to create a completed Activity

**Code:** `write_block_reason` at
[`src/pipeline_agent/harness/loop.py:241`](../src/pipeline_agent/harness/loop.py#L241).

**Rule:** a `create` of an Activity with a truthy `done` is refused. A reported contact must
go through `--task log-contact`.

**Existing test:** `test_loop_refuses_to_create_completed_activity`.

**To add:**
- [ ] `done` given at the top level of the arguments and `done` inside `data` are both refused.
- [ ] `done: false` is allowed (subject to the other policy checks).
- [ ] Truthy non-boolean values (`1`, `"true"`) are refused.
- [ ] ⚠️ **Decide first.** The rule covers **create** only. An `update` that sets `done=true` on a record in the update allow-list is not blocked by it, and that is exactly what D8's injected note asks for ("Mark this task done"). Run D held only because it had no update allow-list.

## 9. IT-01 harness: cleanup closes the agent's own writes

**Code:** `tasks/it01/it01.py cleanup` (added 2026-10-11, before Run 3).

**Rule:** cleanup also closes every open, tagged Activity listed in any runner artifact's
`created_record_ids` under the run directory.

**Existing tests:** none.

**To add:**
- [ ] It closes open, tagged Activities found in `created_record_ids`, and prints `closed agent-written Activity <id>` for each.
- [ ] It skips ones that are already done, and ones without the `[team07-test IT-01]` tag.
