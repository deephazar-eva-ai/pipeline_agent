# CRM Agent Gap Report

**Target agent question:** *"Which deals are rotting, who has not been contacted, and what is the next action on each?"*

**Platform under review:** AgentSwitch ("Schema-driven, agent-composed business platform" — `openapi.json` v0.2.0), tenant Suryodaya Precision Works.

**Method:** Static capability audit of the platform snapshot in `document/` — `schemas.json` (424 entity schemas / 27 domains), `mcp_list.json` (249 entity-CRUD MCP tools / 62 resources), `openapi.json` (752 REST endpoints / 36 tags), `agent_tools.json` (13 generic agent tools), and a live data sample (`deallist.json`, 128 deals). Competitor claims are read from each vendor's own marketing pages, fetched 2026‑09‑17 — not hands-on trials, so treat depth claims as *advertised*, not *verified*. §5's rot-score model and snapshot-table design are ported from a second reference project, `msmecrmapp` — specifically its rot-score writeup (`docs/test_pipeline.md`) and its `dummy_snapshot` table (`docs/dbschema_msme.md`) — adapted for this platform's `Deal` entity in [`deals_snapshot_schema.json`](deals_snapshot_schema.json).

**Competitors reviewed:**
- [Clarify](https://www.clarify.ai/agents) — AI-native CRM with autonomous agents for record upkeep, follow-up drafting, and custom workflows.
- [Monaco](https://www.monaco.com/) — "AI native platform that replaces legacy CRM," positioned around TAM building, account scoring, and agent-driven outreach ("Agents work. You close.").
- [Reevo](https://reevo.ai/) — AI-native sales platform (Find / Connect / Sell / Manage) built on a shared "memory layer" across calls, emails, and records, with an "Ask Reevo" pipeline Q&A agent.

---

## 1. What they do that we do not

Checked against all 424 entity names and 752 endpoints. Grouped by theme, each line is something with **no corresponding entity, field, or endpoint** in the current schema — not a vague impression.

### Pre-pipeline / prospecting
- **No lead/account enrichment data source.** No entity resembling a company/people database, no "enrich" endpoint. [Clarify](https://www.clarify.ai/agents) advertises built-in access to "28M companies / 175M people" plus live web search; [Monaco](https://www.monaco.com/) and [Reevo](https://reevo.ai/) both build a TAM (Total Addressable Market) list and auto-enrich accounts on entry.
- **No lead-scoring engine, despite a wired-up switch.** `CRMPreferences` has `lead_scoring_enabled` (bool) and `lead_score_threshold` (number) — but no entity computes or stores a score. The toggle exists; nothing implements it. [Monaco](https://www.monaco.com/) and [Reevo](https://reevo.ai/) both score/rank prospects automatically.
- **No intent-signal tracking.** No entity for buying signals (hiring, funding, tech-stack changes, etc.), which [Monaco](https://www.monaco.com/) and [Reevo](https://reevo.ai/) both surface as outreach triggers.

### Outbound execution
- **No sequence/cadence entity.** Nothing models a multi-step, multi-day outreach sequence (email 1 → wait 3d → call → email 2…). [Reevo](https://reevo.ai/) ("Connect") and [Monaco](https://www.monaco.com/) both run sequencing natively.
- **No dialer / call-session entity**, and no inbox-warmup or sending-domain-reputation model (`DomainConfig`/`HostedDomain` configure *your* mail domain, they don't warm it up or score deliverability). [Reevo](https://reevo.ai/) explicitly sells domain purchasing + inbox warming.

### Call intelligence
- **No call recording or transcript entity.** `CallNoteDraft` (domain `crm`) stores an AI-*generated* note (`generated_content`, `citations`, approval workflow) tied to an `Activity`, but nothing upstream captures audio or a transcript — the note-generation input is assumed to arrive from outside the platform. [Reevo](https://reevo.ai/) and [Monaco](https://www.monaco.com/) both record and transcribe calls as a first-class capability.
- **No sentiment or talk-track analysis.** Neither `EmailMessage` nor `Activity` nor `CallNoteDraft` carries a sentiment/objection-tag field. [Reevo](https://reevo.ai/)'s "coaching insights tied to call evidence" and [Monaco](https://www.monaco.com/)'s automated summarization both imply this.

### Forecasting & pipeline coaching
- **No forecast entity or model.** `Deal.probability` is a single manually-set number; there's no rollup/weighted forecast, confidence bands, or historical-accuracy tracking. [Reevo](https://reevo.ai/products/manage)'s "Manage" pillar explicitly includes forecasting.
- **No coaching/rep-performance entity** beyond raw `Activity`/`Deal` records — no win-rate-by-rep, talk-ratio, or objection-handling rollups.

### A single pipeline-reasoning surface
- **No purpose-built "ask about my pipeline" skill.** The chat/agent primitives exist generically (`AgentSession`, `AgentPersona`, `AgentSkill`) but there is no pre-authored skill that reproduces [Reevo](https://reevo.ai/)'s "Ask Reevo" — cite-backed pipeline Q&A in Slack. This is the one item in this list that is *infrastructure-ready but not authored* (see §2).

### Net
Everything above is genuinely absent from the 424-entity schema and the 752-endpoint API — not naming conventions I missed, and not something the generic agent tool seat can fabricate, because the underlying data (call audio, enrichment data, intent signals, sequence state) simply isn't captured anywhere in the system.

---

## 2. Which gaps an agent can close today vs. which need new backend

### 2a. Orchestration only — buildable now, on the existing 13-tool agent seat, zero new endpoints

The target question turns out to be almost entirely answerable with what's already there. Concretely, with `agent_tools.json`'s `list`, `get`, `report`, `create`, `transition`:

| Sub-question | How, today |
|---|---|
| Which deals are rotting? | `Deal.list` already returns computed `_rot_level` (`fresh`/`aging`/`stale`/`none`) and `_rot_days` per record, thresholds configurable via `CRMPreferences.deal_rot_days` (also exposed as its own `GET/PUT /api/deal-rot-config`). The agent calls `list(entity=Deal, limit=1000)` and filters client-side on `_rot_level` — no server-side filter exists on that field yet (see 2b), but at 128 deals total, one call is enough. That single threshold is coarse, though — §5 ports a richer five-factor rot score (stage time, inactivity, slippage, regression, deal value) the agent can compute today with the same tool seat. |
| Who hasn't been contacted? | No single "last contacted" field exists on `Deal` or `Party`, but the generic `report` tool supports `aggregate: max` with `group_by`. Three calls close it: `report(Activity, aggregate=max, field=due_date, group_by=deal_id, filters={done:true})`, the same against `EmailMessage.sent_at`/`received_at`, and — if conversations are linked via `ChannelConversation.linked_entity_type=Deal` — its own maintained `last_message_at` field is even cheaper, no aggregation required. |
| What's the next action? | `Activity.list(filters={deal_id: X, done: false})` sorted by `due_date` surfaces any already-scheduled next step. Where none exists, the agent reasons from `Deal.stage` + the `DealFlow` transition graph (`new→qualification→proposal→negotiation→closed_won/lost`) to propose one, then **writes it back** with `Activity.create(...)` — which also means the recommendation shows up in the human UI, not just in a chat reply. |
| Deliver as a standing habit, not a one-off query | `AgentTask` is a first-class scheduled-job entity (`cron`/`interval`, retry count, timeout, `last_run_status`) — the whole three-part question becomes a recurring job with no new table. |
| Escalate the ones that matter | `AgentEscalation` (SLA minutes, due_at, reason_code) hands a specific stale, high-value deal to a human with a tracked SLA instead of silently logging it. |
| Remember what's already been nudged | `AgentMemory` (category `relationship`/`context`, per-`party_id`, expiring) lets the agent avoid re-flagging a deal it already escalated last week. |
| Act under governance | `AgentRunbook` + `ApprovalRequest` let a proven "rotting-deal playbook" graduate from "propose only" to "auto-execute with approval gate," with a full audit trail (`steps_digest`, `approval_request_id`). |

None of this needs a new table, a new endpoint, or a schema change. It's an orchestration/authoring problem: write the skill (an `AgentSkill` record) that chains these existing calls, schedule it as an `AgentTask`, and wire escalation/memory. **This is the part that's ours to build as an agent, this week, on the current seat.**

### 2b. Needs new backend — genuinely ours to fix, not agent-buildable

| Gap | Why the agent seat can't close it | What's actually needed |
|---|---|---|
| `_rot_level` / `_rot_days` not server-filterable | These are computed at read time and attached to the response, but absent from `Deal.list`'s own filter schema (`inputSchema.properties`) — confirmed by inspecting the tool definition. Fine at 128 deals; breaks down at scale (agent has to page through everything). | Add `rot_level`/`rot_days` (or a `min_rot_days`) as a server-side filter/index on the list endpoint. |
| No structured "next action" field | Nothing missing *functionally* (Activity.create covers it), but there's no first-class `Deal.next_action_summary` the way `Lead.next_action` (date-only, no text) half-exists. Every "next action" today is either a full `Activity` record or buried in `notes` richtext. | Either accept Activity-as-next-action as the pattern (no change needed), or add a lightweight `next_action` text + date pair directly on `Deal` for cheaper reads. |
| Lead scoring switch has no engine | `CRMPreferences.lead_score_threshold` exists; nothing writes a score anywhere. | A scoring entity/job — real backend + possibly a data-science component, not something an orchestration agent can invent from nothing. |
| Call recording/transcription, sequences, TAM/enrichment, forecasting, sentiment | Listed in §1 — no entity, no field, no endpoint anywhere in the 424-schema catalog. | New tables + new integrations (recording/transcription vendor, enrichment data provider, sequence-state machine). Out of scope for an agent working the existing tool seat. |
| No persisted rot-score history / stage-transition ledger | The five-factor rot score in §5 can be *computed* on demand with existing tools, but nothing stores it — every re-ask recomputes from scratch, and trend-over-time questions ("is this deal's rot score getting worse?") have no history to query. | A new `DealSnapshot` ledger table — schema proposed in [`deals_snapshot_schema.json`](deals_snapshot_schema.json), §5 below. |

---

## 3. What an agent can do that their product cannot

This is where AgentSwitch's shape — one schema-driven platform instead of a CRM bolt-on — gives an agent more than any of the three competitors' agents can reach, *by construction*:

- **Cross-domain reasoning in one graph.** The same tool seat that reads `Deal` also reads `Ticket` (support), `financial_report` (`ar_aging`/`ap_aging`/`dashboard_summary`), `Contract`, and `Invoice` — 27 domains, 424 entities, one generic `list`/`get`/`report` interface. An agent can correlate "this deal has gone stale" with "this account has an overdue invoice" or "an open support ticket with this contact" in the same reasoning pass. [Clarify](https://www.clarify.ai/agents), [Reevo](https://reevo.ai/) and [Monaco](https://www.monaco.com/) are CRM-scoped: their agents reach email/calendar/Slack/dialer, not payables, tickets, or contracts, because those systems don't exist inside their product.
- **Governed autonomy with an audit trail, not a permission toggle.** [Clarify](https://www.clarify.ai/agents)'s model is "each agent gets access controls like a team member." AgentSwitch's `AgentRunbook` → `ApprovalRequest` → `AgentRunbookRun` chain is structurally different: a specific automated procedure is proposed, observed (`occurrences_observed`, `observed_from/to`), approved once, and every subsequent run is logged with a `steps_digest` and outcome (`completed`/`partial`/`refused`). The agent's autonomy is itself a versioned, auditable object — not a static scope.
- **Consent-ledgered omnichannel outreach.** `ChannelConversation`/`ChannelMessage`/`ChannelSendDecision`/`ChannelConsent` give the agent WhatsApp, Telegram, Slack, Discord, SMS and web chat — each outbound message passes through a `ChannelSendDecision` record that stores *why* it was allowed or refused (`consent_state`, `reason_code`, `purpose`). None of the three competitors mention consent-gated multichannel outbound at all; [Reevo](https://reevo.ai/) and [Monaco](https://www.monaco.com/) are email+call-centric, [Clarify](https://www.clarify.ai/agents) is Gmail+Slack+internal-tools. An agent here can legally and traceably nudge a stale contact over WhatsApp, something none of the three products can do today.
- **Durable, typed, per-relationship memory.** `AgentMemory` stores `preference`/`fact`/`instruction`/`context`/`relationship` items per `party_id` with importance and expiry — memory attached to *the contact*, portable across whatever channel or session next touches them. [Reevo](https://reevo.ai/)'s "shared memory layer" is the closest analogue but is deal/team-scoped, not an explicit typed, expiring, per-person memory store.
- **Self-scheduling as a native object, not a "Skill" bolted onto chat.** `AgentTask` (cron/interval, retries, timeout, `last_run_error_kind`) makes "check every Monday morning" a first-class, inspectable record with its own run history — not a hidden automation inside a SaaS scheduler.
- **One agent, any future domain.** Because the tool seat is schema-generic (`get_schema` + `list`/`get`/`create`/`update`/`transition`/`report` over *any* entity), the same agent that closes the deal-rot question today extends to manufacturing job tracking or payroll exceptions tomorrow with no new integration work — a structural advantage none of the three CRM-only products can offer, since their agents are hard-wired to CRM objects.

---

## 5. A richer rot score, ported from the MSME reference — and a snapshot table to persist it

`_rot_level`/`_rot_days` (§2a) is a single-threshold signal: days since the deal entered its current stage, compared against one number (`CRMPreferences.deal_rot_days`). The `msmecrmapp` reference project independently solved the same "which deals are rotting" problem for its own CRM with a **five-factor weighted score**, documented in `docs/test_pipeline.md`. The formula, weights, and thresholds are reusable as-is — only the *inputs* need re-deriving from AgentSwitch's entities instead of the reference's `opportunities`/`opportunity_activity` tables, exactly as worked out field-by-field in [`deals_snapshot_schema.json`](deals_snapshot_schema.json).

### 5.1 The formula

`rot_score = 0.35·S_stage + 0.25·S_activity + 0.20·S_slippage + 0.15·S_regression + 0.05·S_value`, each sub-score capped to `[0, 1]`:

| Component | Formula | Weight | AgentSwitch input (no new backend) |
|---|---|---|---|
| `S_stage` | `min(days_in_current_stage / (2 × median_days_for_stage), 1)` | 0.35 | `days_in_current_stage` from the deal's own stage-history; the median baseline is built from `closed_won` deals' stage durations |
| `S_activity` | `min(days_since_last_activity / 30, 1)` | 0.25 | `report(Activity, aggregate=max, field=due_date, group_by=deal_id, filters={done:true})` — same call as the "who hasn't been contacted" answer in §2a |
| `S_slippage` | `min(max(days_past_expected_close, 0) / 45, 1)` | 0.20 | `today − Deal.expected_close_date`, floored at 0 |
| `S_regression` | `1` if the deal ever moved backward a stage, else `0` | 0.15 | any stage-history entry tagged `event: "regression"` (a `Deal.stage` transition that goes backward relative to `DealFlow`'s forward order) |
| `S_value` | `min(deal_value / p90(deal_value), 1)` | 0.05 | `Deal.value` vs. the 90th percentile of value across currently-open deals |

**Risk bands**, unchanged from the reference: `rot_score ≥ 0.50` → **At Risk**; `≥ 0.75` → **Critical**; otherwise **Healthy**; and — this is the useful bit — **Unknown** whenever `days_since_last_activity` can't be computed at all, i.e. the deal has *no* activity record to measure from. In the reference dataset that case was hypothetical (every opportunity had synthetic activity); in AgentSwitch it is not hypothetical — **a deal with zero `Activity` rows ever is precisely a "who has not been contacted" deal.** The rot-score model's "Unknown" band and the target question's second clause turn out to be the same underlying signal, which is a good sign the model transfers cleanly rather than needing to be reinvented.

### 5.2 `rot_comment` — naming the dominant driver

Alongside the score, the reference attaches a one-line explanation naming whichever component contributes most — by **weighted** contribution (`weight × sub-score`), not raw sub-score, so a maxed-out `S_value` (weight 0.05) can never outrank a modest `S_stage` (weight 0.35); ties break in weight order (`stage → activity → slippage → regression → value`). Adapted message forms:

- *"Primarily driven by time in current stage: 46 days in negotiation vs. a 20-day typical stay (rot score 0.59)."*
- *"Primarily driven by inactivity: no logged Activity in 33 days (rot score 0.59)."*
- *"Primarily driven by close-date slippage: 30 days past the expected close date (rot score 0.55)."*
- *"Primarily driven by stage regression: this deal has previously moved backward a stage (rot score 0.58)."*
- *"Primarily driven by deal value: ₹17,04,000 deal size, among the largest in the open pipeline (rot score 0.51)."*
- *"Rot score unavailable — no Activity record exists for this deal."* (the Unknown band — and, per 5.1, the agent's cue that this is also a never-contacted deal)

This is exactly the "what's the next action" half of the target question, for free: the comment *is* the next action in most cases — chase the stage, log a touchpoint, re-confirm the close date, or address the regression.

### 5.3 Persisting it: the `DealSnapshot` table

The reference's own pattern is worth copying, not just its numbers: `compute_rot_scores_for_open_pipeline` stays the **on-demand source of truth** (the Streamlit app and its MCP server — `rot_scores`/`rot_score` tools — always call it live), while a *separate* materialization step (`populate_rot_scores.py`) periodically writes the same computation's output onto `dummy_snapshot.rot_score`/`rot_comment`, so the numbers are also queryable with plain SQL without re-running the computation. It is explicitly a point-in-time snapshot, not a live view — it reflects whatever date it was last run against.

[`deals_snapshot_schema.json`](deals_snapshot_schema.json) proposes the AgentSwitch equivalent — a `DealSnapshot` ledger entity (domain `sales`, kind `ledger`) that:

- Stores one row per deal per capture — `deal_id`, `captured_at`, `snapshot_reason` (`scheduled` / `stage_transition` / `escalation` / `manual`), the point-in-time `stage_history` and `rot_score`/`rot_comment`, plus denormalized facts (`value`, `owner`, `party_id`, `expected_close_date`…) frozen as they were at capture time rather than live-joined to a `Deal` record that may since have changed.
- Improves on the reference in one deliberate way: `dummy_snapshot` has no capture timestamp, so despite its name it behaves as a current-state cache, not a time series — one row per opportunity, overwritten. Since the brief here is explicitly to store *historical* information, `DealSnapshot` adds `captured_at` so a deal accumulates many rows over time, with `(deal_id, captured_at)` as the natural key.
- Leaves `rot_score`/`rot_comment` `NULL` for `closed_won`/`closed_lost` deals, matching the reference (the score is only meaningful for open pipeline).

Mirroring how this closes with §2 above: **computing** the score is orchestration-only, buildable today (§2a — `report`/`list` calls, no new backend); **persisting** it in `DealSnapshot` for trend queries is new backend (§2b — a new table). The suggested write path is the same shape as the reference's `populate_rot_scores.py`: an `AgentTask` (cron, e.g. weekly) that runs the §5.1 computation over every open deal and inserts one `DealSnapshot` row per deal, plus an extra row on every `Deal.*` stage-transition tool call and every `AgentEscalation` raised — so the table captures both the steady drumbeat and the moments that actually mattered. A skill built this way would also want to expose the reference MCP server's two-tool split — a ranked list (`rot_scores`, optionally filtered by risk band) and a single-deal breakdown (`rot_score`, one deal's full sub-score audit trail) — as the natural query surface on top of `DealSnapshot`, once it exists.

## 6. Caveats

- Competitor capability claims are taken from each vendor's own site ([Clarify](https://www.clarify.ai/agents), [Reevo](https://reevo.ai/), [Monaco](https://www.monaco.com/)), fetched 2026‑09‑17. They are marketing descriptions, not verified through a trial account — actual depth (e.g. how good Reevo's risk-flagging really is) may differ from what's advertised.
- The AgentSwitch capability picture is a **static snapshot**: `schemas.json`, `mcp_list.json`, `openapi.json`, and `agent_tools.json` as captured in `document/` on 2026‑09‑17, plus one live data pull (`deallist.json`, 128 deals). Anything added to the platform after that snapshot isn't reflected here. The `report` tool's `max`/`group_by` behavior on date fields (used throughout §2a) is inferred from its documented parameters, not confirmed against a live call — worth a smoke test before building on it.
- "No entity found" claims in §1 are based on an exhaustive name/keyword grep across all 424 schema entities (`recording`, `transcript`, `sequence`, `cadence`, `dialer`, `enrich`, `intent`, `signal`, `tam`, `warmup`, `forecast`, `coach`, `sentiment` — zero or irrelevant hits on each). A field could theoretically exist under a name this pass didn't anticipate.
- §5's rot-score formula, weights, thresholds and `rot_comment` logic are carried over unchanged from `msmecrmapp/docs/test_pipeline.md` / `src/msmecrmapp/pipeline_health/data.py`; only the input queries were re-derived for AgentSwitch's schema. The worked numbers in the example `rot_comment` messages (46 days in negotiation, 33 days since last activity) come from the illustrative row in `deals_snapshot_schema.json`, not a live computation — the `report`-tool call chain behind them (5.1's "AgentSwitch input" column) hasn't been run against the live API, same caveat as §2a. `DealSnapshot` itself does not exist in `document/schemas.json` today; it is a proposal, not a shipped entity.
