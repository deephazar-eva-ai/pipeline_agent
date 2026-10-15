# IT-01 Run 3 — developer test-case specifications

This is a manual test-case catalogue for the unimplemented edge coverage identified in
[run3_bugfixes.md](run3_bugfixes.md). Run any resulting offline
suite from `pipeline_agent/` with `.venv/bin/python -m pytest -q`.

All activity fixtures below are completed unless they explicitly say `done=0`. `NOW` is
`2026-09-28T12:00:00+00:00` unless the case gives another value. The `activity`, `deal`,
and `build_index` helpers named here are the existing offline Keystone helpers.

## G-A — completed contact recognition

### R3-GA-01 — contact type matching is case-insensitive

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.is_contact`, via `load_activity_index` |
| **Protects** | G-A |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  CRM type casing must not turn completed customer contact into a false
`never_contacted` result.

**Precondition / fixture**  One open deal and two completed rows linked to it.

**Input**  Rows have `type="Call"` and `type="EMAIL"`, no cancelling outcome; the
inactive neighbour is `type="task"`.

**Expected — guard ACTIVE**  Both mixed-case rows are indexed as contacts; the latest is
returned by `last_contacted`.

**Expected — guard INACTIVE**  The task is not indexed as contact and increments
`noncontact_done`.

**Must NOT be true**  The mixed-case rows do not appear in `noncontact_done`.

**Evidence to keep**  Indexed latest date/type and `noncontact_done` assertion values.

**Known limits**  Does not cover outcome wording.

### R3-GA-02 — missing contact type is recorded as completed non-contact

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.load_activity_index` |
| **Protects** | G-A |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Sparse Activity records must not invent contact evidence.

**Precondition / fixture**  One open deal and two completed rows linked to it.

**Input**  One row omits `type`/uses `None`; the other has `type=""`. The inactive
neighbour is a completed `meeting`.

**Expected — guard ACTIVE**  Neither blank-type row is contact; `noncontact_done == 2`.

**Expected — guard INACTIVE**  The meeting is contact and does not increase
`noncontact_done`.

**Must NOT be true**  A blank type does not establish a last-contact date.

**Evidence to keep**  Last-contact map and final non-contact counter.

**Known limits**  Does not cover a missing `done` field.

### R3-GA-03 — completed non-contact activity types remain excluded

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.is_contact` |
| **Protects** | G-A |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Operational tasks and notes must not reset sales-contact age.

**Precondition / fixture**  Completed Activity-shaped mappings.

**Input**  `type` values `lunch`, `note`, and `task`; inactive neighbour `email`.

**Expected — guard ACTIVE**  Each listed non-contact type returns false.

**Expected — guard INACTIVE**  The email returns true when it has no cancelling outcome.

**Must NOT be true**  None of the three types is added to a contact-date index.

**Evidence to keep**  Parameter value and boolean assertion for each type.

**Known limits**  Does not exercise indexing counters.

### R3-GA-04 — cancelling outcomes override an otherwise valid contact type

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.is_contact` |
| **Protects** | G-A |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  A cancelled call is not proof that a customer was contacted.

**Precondition / fixture**  Completed call-shaped mappings.

**Input**  Outcomes `"cancelled"` and `"Cancelled - no answer"`; inactive neighbours
have `None` and `"Completed"` outcomes.

**Expected — guard ACTIVE**  Both cancelling outcomes return false.

**Expected — guard INACTIVE**  `None` and `Completed` return true.

**Must NOT be true**  Case variation in `Cancelled` must not change the result.

**Evidence to keep**  Outcome-to-result parameter table in the test failure message.

**Known limits**  The ambiguous `Cancellation terms agreed` language is isolated in
R3-GA-05.

### R3-GA-05 — cancellation-prefix semantics require a product decision

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `workflow.is_contact` |
| **Protects** | G-A follow-up |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Prefix matching can discard evidence of a completed substantive
call merely because its outcome begins with “Cancellation”.

**Precondition / fixture**  A completed `call` with a date and normal deal linkage.

**Input**  `outcome="Cancellation terms agreed"`; inactive comparator
`outcome="Cancelled by customer"`.

**Expected — guard ACTIVE**  **Decision required before implementation:** record whether
this outcome counts as contact. If product intent is substantive-contact recognition,
assert true; otherwise assert false and document that choice.

**Expected — guard INACTIVE**  The cancelled comparator remains non-contact under either
decision.

**Must NOT be true**  The chosen expectation must not be inferred from current prefix
implementation rather than the approved contract.

**Evidence to keep**  Linked requirement/decision and the two outcome assertions.

**Known limits**  Blocked from becoming an executable regression until the decision is
made.

### R3-GA-06 — non-contact summary appears only when completed rows were excluded

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.run_canonical_task` summary rendering |
| **Protects** | G-A |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Operators need to know when completed non-contact rows were
deliberately ignored, without seeing a misleading zero-count warning.

**Precondition / fixture**  Canonical task fixture with otherwise valid scope.

**Input**  Variant A contains one completed task and one cancelled call; variant B
contains only a completed valid email.

**Expected — guard ACTIVE**  Variant A summary contains exactly
`2 completed task(s), cancelled call(s) or other non-contact activit(ies) were not counted as customer contact`.

**Expected — guard INACTIVE**  Variant B omits that sentence entirely.

**Must NOT be true**  Variant B must not emit a “0 completed” summary.

**Evidence to keep**  Rendered summary text for both variants.

**Known limits**  Does not test pluralisation beyond count 2.

## G-B — threshold boundaries

### R3-GB-01 — calibrated fallback retains its inclusive boundary

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `workflow.assess_rot` |
| **Protects** | G-B |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  The explicit 30-day policy is exclusive, but inferred platform
calibration must retain its distinct inclusive contract.

**Precondition / fixture**  A deal whose independent age equals a `RotCalibration(5)`
boundary and an empty index.

**Input**  Call `assess_rot(..., threshold_days=None)` at age 5; inactive neighbour age
4.

**Expected — guard ACTIVE**  Age 5 is a rot candidate through calibration.

**Expected — guard INACTIVE**  Age 4 is not a candidate.

**Must NOT be true**  The fallback must not apply the configured-threshold `>` rule.

**Evidence to keep**  Candidate result and evidence source/boundary fields.

**Known limits**  Does not cover calibration derivation.

### R3-GB-02 — never-contacted records cross the configured threshold after day 30

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `workflow.assess_rot`, `workflow.find_uncalled` |
| **Protects** | G-B; Suryodaya day-30 transition |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  A book must not flip a day early when a customer or deal has never
been contacted.

**Precondition / fixture**  Empty index; open deal with a customer party and creation
date chosen relative to `NOW`.

**Input**  Run at age 30 and age 31 with `threshold_days=30`.

**Expected — guard ACTIVE**  At day 31, the deal is rotting and the never-contacted
customer is uncalled.

**Expected — guard INACTIVE**  At exactly day 30, neither is flagged.

**Must NOT be true**  Evidence must not report age 30 as “more than 30”.

**Evidence to keep**  Candidate lists and hand-computed `days_since` values.

**Known limits**  Does not assert tenant-wide fixture counts.

## G-C — company-local day calculation

### R3-GC-01 — a naive timestamp is interpreted as UTC before company-zone conversion

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `workflow.local_day`, `workflow.days_since` |
| **Protects** | G-C |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Near-midnight UTC records otherwise move a contact into the wrong
company day.

**Precondition / fixture**  `now=2026-10-11 22:00 America/New_York`.

**Input**  Naive timestamp `2026-10-12T02:00:00`; inactive comparator date-only
`2026-10-11`.

**Expected — guard ACTIVE**  `local_day` is `2026-10-11` and `days_since` is 0.

**Expected — guard INACTIVE**  The date-only value remains `2026-10-11`, also 0 days.

**Must NOT be true**  The naive input must not be treated as local Oct 12.

**Evidence to keep**  Returned local dates and day deltas.

**Known limits**  Does not cover daylight-saving transition hour.

### R3-GC-02 — explicit-offset timestamps are converted to the company zone

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.local_day`, `workflow.days_since` |
| **Protects** | G-C |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  CRM exports with offsets must agree with UTC and local representations
of the same instant.

**Precondition / fixture**  `now=2026-10-11 22:00 America/New_York`.

**Input**  `2026-10-12T07:30:00+05:30` (same instant as 02:00 UTC); inactive comparator
`2026-10-12T07:30:00+00:00`.

**Expected — guard ACTIVE**  The `+05:30` timestamp converts to local day Oct 11 and is
0 days old.

**Expected — guard INACTIVE**  The UTC comparator converts to local day Oct 12 and is
one calendar day ahead (negative delta, if exposed).

**Must NOT be true**  The offset suffix must not be ignored.

**Evidence to keep**  Source timestamps and returned local dates.

**Known limits**  Does not cover non-ISO timestamp formats.

### R3-GC-03 — absent and malformed dates have no day value

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.local_day`, `workflow.days_since` |
| **Protects** | G-C |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Bad source values must become evidence gaps instead of fabricated
ages.

**Precondition / fixture**  A timezone-aware `now`.

**Input**  Each function receives `""`, `None`, and `"not-a-date"`; inactive comparator
is `"2026-09-28"`.

**Expected — guard ACTIVE**  Both functions return `None` for every invalid input.

**Expected — guard INACTIVE**  The valid date returns a real local day and integer delta.

**Must NOT be true**  Invalid inputs must not raise parsing errors or default to today.

**Evidence to keep**  Parameter-to-return table.

**Known limits**  Does not assert later issue rendering.

### R3-GC-04 — a timezone-naive reference time preserves timestamp calendar date

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.local_day` |
| **Protects** | G-C |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Offline callers without an established company timezone need a
deterministic, non-converting fallback.

**Precondition / fixture**  `now=2026-10-11 22:00` with no timezone.

**Input**  Timestamp `2026-10-12T02:00:00+00:00`; inactive comparator uses timezone-aware
New York `now` from R3-GC-01.

**Expected — guard ACTIVE**  The timestamp's own date, Oct 12, is returned.

**Expected — guard INACTIVE**  With zoned New York `now`, the result is Oct 11.

**Must NOT be true**  Naive `now` must not receive an implicit host timezone.

**Evidence to keep**  Both reference times and local-day results.

**Known limits**  Does not decide how a naive source timestamp is stored.

## G-D — agent marker evidence

### R3-GD-01 — an unscoped agent marker is still a completed-contact data issue

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.load_activity_index`, `workflow.contact_evidence_issues` |
| **Protects** | G-D |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  A malformed marker must not hide a possibly agent-generated contact.

**Precondition / fixture**  One open deal and a completed valid call containing
`created_by=pipeline_agent` with no `run=` token.

**Input**  Load the index and calculate issues; inactive comparator is the same call
without a marker.

**Expected — guard ACTIVE**  The call counts as contact; `marked_contacts[0].marker_run`
is `None`; one `agent_marker_on_completed_contact` issue is emitted.

**Expected — guard INACTIVE**  The unmarked call counts as contact but produces no marker
issue.

**Must NOT be true**  Missing `run=` must not suppress the warning.

**Evidence to keep**  Marked-contact mapping and issue code.

**Known limits**  Does not test malformed `run=` values.

### R3-GD-02 — marked completed task is non-contact but not a marker-contact issue

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.load_activity_index`, `workflow.contact_evidence_issues` |
| **Protects** | G-D; G-A |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  The warning is about suspect completed customer contact, not every
agent-tagged operational task.

**Precondition / fixture**  One open deal with a completed tagged `task`.

**Input**  Description contains a valid agent marker; inactive comparator is a completed
tagged `call`.

**Expected — guard ACTIVE**  The task increments `noncontact_done` and emits no
`agent_marker_on_completed_contact` issue.

**Expected — guard INACTIVE**  The marked call is contact and emits the marker issue.

**Must NOT be true**  The task must not appear in `marked_contacts`.

**Evidence to keep**  Counter, marker list, and issue-code list.

**Known limits**  Does not test task status open.

### R3-GD-03 — a marker on an open call does not become evidence

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.load_activity_index`, `workflow.contact_evidence_issues` |
| **Protects** | G-D |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Planned work must not be reported as completed contact or a
completed-contact integrity issue.

**Precondition / fixture**  One open deal with one tagged `call`, `done=0`.

**Input**  Call `load_activity_index` and `contact_evidence_issues`; inactive comparator
sets `done=1`.

**Expected — guard ACTIVE**  No contact date, marked-contact entry, or marker issue is
created.

**Expected — guard INACTIVE**  The completed comparator creates both contact evidence and
the marker issue.

**Must NOT be true**  An open call must not increment `noncontact_done` either.

**Evidence to keep**  Index maps and issue list for both variants.

**Known limits**  Does not cover an open non-contact type.

### R3-GD-04 — party-only marked contact is reported for every open sibling deal

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.contact_evidence_issues` |
| **Protects** | G-D |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Party-linked contact can affect several open opportunities and each
affected one must be visible to reviewers.

**Precondition / fixture**  Two open deals and one closed deal share party P; a completed
marked email has `party_id=P` and no `deal_id`.

**Input**  Calculate issues for all three deals; inactive comparator unmarks the email.

**Expected — guard ACTIVE**  Each open deal receives exactly one
`agent_marker_on_completed_contact` issue; the closed deal receives none.

**Expected — guard INACTIVE**  The unmarked party-only email yields no marker issues.

**Must NOT be true**  The closed deal and unrelated parties must not be listed.

**Evidence to keep**  Sorted `(deal_id, code)` issue pairs.

**Known limits**  Does not assert rot bookkeeping.

## G-E — future contact evidence

### R3-GE-01 — a past contact wins over a newer future contact

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.ActivityIndex.last_contacted`, `workflow.contact_evidence_issues` |
| **Protects** | G-E |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  A bad future-dated row must not erase valid historical evidence.

**Precondition / fixture**  One open deal with valid completed calls dated Sep 10 and Oct
1 relative to Sep 28 `NOW`.

**Input**  Request last contact and issues; inactive comparator contains only the future
call.

**Expected — guard ACTIVE**  Last contact is Sep 10; classification is based on it and no
`insufficient_evidence` issue is emitted.

**Expected — guard INACTIVE**  Future-only contact returns Oct 1 and emits
`insufficient_evidence`.

**Must NOT be true**  The future row must not replace the usable past date.

**Evidence to keep**  `(date, basis)`, contact status, and issue codes.

**Known limits**  Does not cover unparseable dates.

### R3-GE-02 — party-only future contact is handled through party linkage

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.ActivityIndex.last_contacted`, `workflow.contact_evidence_issues` |
| **Protects** | G-E |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Future-date safety cannot be bypassed simply by omitting a deal link.

**Precondition / fixture**  One open deal with party P and a completed future `call` for
P without `deal_id`.

**Input**  Resolve contact for the deal; inactive comparator replaces the future date with
a past date.

**Expected — guard ACTIVE**  Basis is `party`; status is `insufficient_evidence`; one
matching data issue is raised.

**Expected — guard INACTIVE**  Past party contact is used normally and has no future-date
issue.

**Must NOT be true**  The missing `deal_id` must not produce `never_contacted`.

**Evidence to keep**  Last-contact tuple and issue list.

**Known limits**  Does not combine party and deal contacts.

### R3-GE-03 — future evidence on a closed deal is ignored for issue reporting

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.contact_evidence_issues` |
| **Protects** | G-E |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Data-quality queues should contain active work, not irrelevant closed
history.

**Precondition / fixture**  One closed deal with a future-dated completed call.

**Input**  Calculate issues; inactive comparator changes the same deal to an open stage.

**Expected — guard ACTIVE**  No `insufficient_evidence` issue names the closed deal.

**Expected — guard INACTIVE**  The open comparator produces the issue.

**Must NOT be true**  The closed deal must not leak into `must_report` through this issue.

**Evidence to keep**  Issue list and deal stage used by each variant.

**Known limits**  Does not suppress normal historical reporting elsewhere.

## C-R6 — canonical mismatched links

### R3-CR6-01 — correctly linked activity is absent from mismatched-links output

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.run_canonical_task`, `contract.CanonicalAnswer.mismatched_links` |
| **Protects** | C-R6 |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Correct CRM linkage must not create false data-quality allegations.

**Precondition / fixture**  Open deal D owned by party P and one completed valid contact
on D.

**Input**  Contact has `party_id=P`; inactive comparator changes it to party Q.

**Expected — guard ACTIVE**  `mismatched_links` is empty for the correct row.

**Expected — guard INACTIVE**  The Q-linked row is listed by activity name/subject.

**Must NOT be true**  The correctly linked row must not be named as mismatched.

**Evidence to keep**  Canonical answer list and activity subject.

**Known limits**  Does not test missing party IDs.

### R3-CR6-02 — an activity without party linkage is not called mismatched

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `workflow.run_canonical_task` |
| **Protects** | C-R6 |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  A missing link is incomplete evidence, not proof of a link to the
wrong customer.

**Precondition / fixture**  Open deal D with party P and a completed call linked to D.

**Input**  Omit `party_id`; inactive comparator supplies unrelated party Q.

**Expected — guard ACTIVE**  The row is absent from `mismatched_links`.

**Expected — guard INACTIVE**  The Q-linked row appears once with its subject.

**Must NOT be true**  `party_id=None` must not be rendered as a customer name.

**Evidence to keep**  Canonical list and source row linkage.

**Known limits**  Does not decide whether missing-party contact counts for D.

### R3-CR6-03 — mismatched contact does not freshen the deal

| | |
|---|---|
| **Technique** | state |
| **Target** | `workflow.ActivityIndex.last_contacted` |
| **Protects** | C-R6; D13 |
| **Door** | stub |
| **Writes** | no |

**Why this exists**  Cross-customer activity must not hide a genuinely stale opportunity.

**Precondition / fixture**  Deal D for party P has a correctly linked contact at T−80 and
a newer completed contact with `deal_id=D`, `party_id=Q`.

**Input**  Resolve D’s last contact; inactive comparator changes Q to P.

**Expected — guard ACTIVE**  D’s last-contact date remains T−80 and the newer row is
listed as mismatched.

**Expected — guard INACTIVE**  With P linkage, the newer date becomes D’s last contact
and no mismatch is listed.

**Must NOT be true**  The mismatched newer date must not reduce D’s rot age.

**Evidence to keep**  Last-contact tuple, rot evidence, and mismatch entry.

**Known limits**  Does not cover a closed D.

## C-R5/C-R6 — loop must-report output

### R3-MR-01 — excluded fixture deals collapse into one required-report line

| | |
|---|---|
| **Technique** | state |
| **Target** | `agent.contact_status` must-report formatter |
| **Protects** | C-R5/C-R6 |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Repeating fixture exclusions consumes the fixed report budget and
hides actual evidence problems.

**Precondition / fixture**  Contact-status result containing three open test-fixture
exclusions.

**Input**  Format `must_report`; inactive comparator has one ordinary excluded deal.

**Expected — guard ACTIVE**  Exactly one line reads `3 open test-fixture deal(s) excluded`.

**Expected — guard INACTIVE**  The non-fixture exclusion is rendered normally under its
own code.

**Must NOT be true**  No individual test-fixture deal name appears in the collapsed list.

**Evidence to keep**  Full response `must_report` array.

**Known limits**  Does not test cap truncation.

### R3-MR-02 — required-report output truncates predictably after 25 items

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `agent.contact_status.MUST_REPORT_MAX` |
| **Protects** | C-R5/C-R6 |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Large data-quality sets must remain readable while telling the model
where to retrieve the remainder.

**Precondition / fixture**  Deterministic ordered collection of 27 reportable items.

**Input**  Format the response; inactive comparator supplies exactly 25 items.

**Expected — guard ACTIVE**  It contains the first 25 entries and ends with
`... and 2 more - page section data_issues`.

**Expected — guard INACTIVE**  At 25, it has no truncation line.

**Must NOT be true**  Items 26–27 must not be silently dropped without the pager text.

**Evidence to keep**  Array length, final line, and stable first/last item IDs.

**Known limits**  Does not test pagination retrieval itself.

### R3-MR-03 — only mandatory issue codes are promoted into must-report

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `agent.contact_status.MUST_REPORT_CODES` |
| **Protects** | C-R5/C-R6 |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  The always-visible channel must preserve room for safety-critical
issues.

**Precondition / fixture**  Response input has one `no_owner` issue and one issue whose
code belongs to `MUST_REPORT_CODES`.

**Input**  Render a section response; inactive comparator contains only `no_owner`.

**Expected — guard ACTIVE**  The mandatory issue appears; `no_owner` does not.

**Expected — guard INACTIVE**  No `must_report` entry is made for `no_owner` alone.

**Must NOT be true**  The code filter must not promote all `data_issues`.

**Evidence to keep**  Source issue codes and serialized response.

**Known limits**  Does not check every defined mandatory code.

### R3-MR-04 — response omits must-report when nothing qualifies

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `agent.contact_status` response builder |
| **Protects** | C-R5/C-R6 |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  An empty optional field can be mistaken for a partially populated
report by consumers.

**Precondition / fixture**  A valid contact-status result with no exclusions or mandatory
data issues.

**Input**  Request each supported response section; inactive comparator adds one mandatory
issue.

**Expected — guard ACTIVE**  Every response omits the `must_report` key.

**Expected — guard INACTIVE**  Every requested section includes the populated key.

**Must NOT be true**  The active result must not contain `must_report: []`.

**Evidence to keep**  Response dictionaries for each section.

**Known limits**  Does not test LLM rendering.

## G-G — loop write guard

### R3-GG-01 — completed Activity create is blocked whether done is nested or top-level

| | |
|---|---|
| **Technique** | guard-activation |
| **Target** | `harness.loop.write_block_reason` |
| **Protects** | G-G |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  Model argument shape must not bypass the completed-Activity create
prohibition.

**Precondition / fixture**  A permitted `create` invocation for entity `Activity`.

**Input**  Variants use `done=true` at argument top level and inside `data`; inactive
comparators omit `done`.

**Expected — guard ACTIVE**  Both variants return a non-empty block reason referring to
logging contact through the approved path.

**Expected — guard INACTIVE**  Neither omission is blocked by the completed-create guard
(other independent policies may still apply).

**Must NOT be true**  No active variant reaches a tool create call.

**Evidence to keep**  Argument shape and returned reason for both variants.

**Known limits**  Does not test truthy non-booleans.

### R3-GG-02 — an explicitly open Activity create remains eligible

| | |
|---|---|
| **Technique** | boundary |
| **Target** | `harness.loop.write_block_reason` |
| **Protects** | G-G |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  A safety guard must not disable valid creation of a planned follow-up.

**Precondition / fixture**  A `create Activity` request satisfying the normal allow-list
and scope checks.

**Input**  Set `done=false`; inactive comparator uses `done=true`.

**Expected — guard ACTIVE**  No completed-create block reason is returned.

**Expected — guard INACTIVE**  The truthy value is refused.

**Must NOT be true**  `false` must not be treated as truthy merely because the key exists.

**Evidence to keep**  Input payloads and returned guard results.

**Known limits**  Does not authorize an actual write.

### R3-GG-03 — truthy non-boolean done values are refused

| | |
|---|---|
| **Technique** | equivalence |
| **Target** | `harness.loop.write_block_reason` |
| **Protects** | G-G |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  JSON coercion and model-generated payloads must not create a loophole
around the completed-record guard.

**Precondition / fixture**  Otherwise allowed Activity create requests.

**Input**  `done=1` and `done="true"`; inactive comparators use `0` and `""` only if the
payload schema permits them.

**Expected — guard ACTIVE**  Both truthy values are refused.

**Expected — guard INACTIVE**  Falsy permitted values are not refused by this guard.

**Must NOT be true**  The guard must not require the literal boolean `true` to activate.

**Evidence to keep**  Value-to-block-reason parameter table.

**Known limits**  Does not validate Activity schema coercion.

### R3-GG-04 — completed Activity update policy requires a product decision

| | |
|---|---|
| **Technique** | guard-activation |
| **Target** | `harness.loop.write_block_reason` |
| **Protects** | G-G follow-up; D8 |
| **Door** | unit |
| **Writes** | no |

**Why this exists**  An allow-listed update that sets `done=true` could achieve exactly
the prohibited state by a different operation.

**Precondition / fixture**  An update-allow-listed Activity and a payload that sets
`done=true` in the D8-style request.

**Input**  Compare `operation="update"` with `operation="create"` using the same done
payload.

**Expected — guard ACTIVE**  **Decision required before implementation:** decide whether
the policy forbids all paths to a completed Activity or only creates. Encode that decision
explicitly for update.

**Expected — guard INACTIVE**  Create remains refused under the existing contract.

**Must NOT be true**  The test must not imply that lack of an update allow-list is a
permanent safety control.

**Evidence to keep**  Approved policy decision and operation-to-reason assertions.

**Known limits**  Not executable until the update policy is decided.

## IT-01 cleanup

### R3-CL-01 — cleanup closes open tagged agent-written Activities from runner artifacts

| | |
|---|---|
| **Technique** | artifact |
| **Target** | `tasks.it01.it01 cleanup` |
| **Protects** | IT-01 harness cleanup |
| **Door** | CLI |
| **Writes** | ⚠ yes — fixture Activities only |

**Why this exists**  Test-run writes must be cleaned reliably so they cannot contaminate a
later shared-CRM run.

**Precondition / fixture**  Isolated test run directory has an artifact whose
`created_record_ids` includes two open Activities tagged `[team07-test IT-01]`; client
stub exposes both records.

**Input**  Invoke `it01.py cleanup` for that run directory; inactive comparator has an
empty `created_record_ids` list.

**Expected — guard ACTIVE**  Both records are updated closed and stdout includes
`closed agent-written Activity <id>` once per ID.

**Expected — guard INACTIVE**  No close call and no such stdout line is produced.

**Must NOT be true**  Records not named in artifacts must not be touched.

**Evidence to keep**  Stub update calls, resulting `done` values, and captured stdout.

**Known limits**  Must run only against an isolated client fixture, never the shared CRM.

### R3-CL-02 — cleanup skips already-closed and untagged artifact IDs

| | |
|---|---|
| **Technique** | guard-activation |
| **Target** | `tasks.it01.it01 cleanup` |
| **Protects** | IT-01 harness cleanup |
| **Door** | CLI |
| **Writes** | ⚠ yes — fixture Activities only |

**Why this exists**  Artifact membership alone is insufficient authority to alter a record.

**Precondition / fixture**  Artifact lists one tagged `done=1` Activity and one open
untagged Activity; a third listed row is open and tagged.

**Input**  Invoke cleanup against an isolated stub client; inactive comparator is the
third eligible row.

**Expected — guard ACTIVE**  No update or `closed agent-written` line is emitted for the
already-done or untagged IDs.

**Expected — guard INACTIVE**  The open tagged row is closed and logged.

**Must NOT be true**  Cleanup must not alter the untagged record even when its ID came
from the artifact.

**Evidence to keep**  Per-ID initial state, update calls, and stdout.

**Known limits**  Does not cover malformed artifact JSON.
