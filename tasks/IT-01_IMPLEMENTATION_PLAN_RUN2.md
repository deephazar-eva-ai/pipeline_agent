# IT-01 Run 2: schedule and step-by-step procedure

**Scenario:** `docs/integration_testing_runs_consolidated.md` ("Hostile Monday")
**Package:** `tasks/it01/` (see `tasks/it01/README.md`)
**History and context:** `tasks/IT-01_IMPLEMENTATION_PLAN.md`
**Written:** 2026-10-09, after the central evaluator run passed 7/7 on `3394506`

Run 1 (`20261009T055144IST`) failed C-W1 and has been cleaned up. The fixes
are on `main` (`7904b1f`, now included in `3394506`). Run 2 is the corrected
rerun. It is the only evidence that can mark the IT-01 gates passed; the
central pass is not IT-01 evidence.

## 0. Run-day runbook (Sat 2026-10-10 → Sun 2026-10-11)

This section is the step list to follow on the day. §§2–5 below hold the
reasons and the full checks for each step.

### 0.1 Status at 2026-10-10 10:35 IST

| Item | Status |
|---|---|
| `main` = `origin/main` = `3394506`, clean except this file | ✓ checked |
| Test suite | ✓ 304 passed |
| D11 | Reported for admin cleanup (PB2); cannot be closed from this seat; excluded from the run (§3) |
| OpenRouter credit | $3.63. Run 1 used ≈ $0.25 per Sonnet Run A plus Qwen Run D, so Run 2 needs about $0.75–1.00 |
| Announcement to Keystone teams | **Send before 18:30 IST** if not sent: window Sat 18:30 IST → Sun ≈ 11:00 IST, tag `[team07-test IT-01]`, your contact, "only tagged fixture records are written", and that the stuck D11 test deal stays visible until an admin closes it |
| Code changes | **None until cleanup is done.** The agent's next-action Activity carrying no `party_id` is a known open finding on `main`; it is deferred to after Run 2 so that the run tests `3394506` |

### 0.2 Session 1: Sat 18:30–21:30 IST (New York Sat 09:00–12:00)

**Step 0. Before 18:30 IST: final workspace check.**

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
git switch main && git fetch origin && git status -sb    # main...origin/main; only this plan file untracked
git rev-parse --short HEAD                               # 3394506
.venv/bin/python -m pytest -q                            # 304 passed
```

**Step 1. Shell set-up and check helpers.** Do this once per terminal.

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
source .venv/bin/activate
export IT01_RUN_DIR="runs/IT-01/$(date +%Y%m%dT%H%M%S)IST-run2"
test ! -e "$IT01_RUN_DIR" && echo "new: ok"
mkdir -p "$IT01_RUN_DIR" && printf '%s\n' "$IT01_RUN_DIR" | tee runs/IT-01/RUN2_DIR.txt
export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in'   # bare host
export AGENTSWITCH_MCP_TOKEN='<keystone token>'
export AGENTSWITCH_TENANT=keystone
export OPENROUTER_API_KEY='<key>'
```

Then paste these read-only helpers. They only read files under
`$IT01_RUN_DIR`.

```bash
# it01_result <phase-folder>: key fields of the newest result.json in it
it01_result() {
  python3 - "$(ls -td "$IT01_RUN_DIR/$1"/*/ | head -1)result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); print(sys.argv[1])
for k in ("model", "ended", "calls", "claimed_success", "error", "input_tokens",
          "output_tokens", "created_record_ids", "updated_record_ids", "unsupported_figures"):
    print(f"  {k}: {r.get(k)}")
fa = r.get("final_answer")
print(json.dumps(fa, indent=1, ensure_ascii=False) if isinstance(fa, (dict, list)) else str(fa))
PY
}
# it01_diff <before> <after> <Entity>: new / changed / removed rows between two snapshots
it01_diff() {
  python3 - "$IT01_RUN_DIR" "$1" "$2" "$3" <<'PY'
import json, sys
from pathlib import Path
root, a, b, ent = Path(sys.argv[1]), *sys.argv[2:5]
def load(s):
    return {r["id"]: {k: v for k, v in r.items() if not k.startswith("_")}
            for r in json.loads((root / s / f"{ent}.json").read_text()) if isinstance(r, dict) and r.get("id")}
A, B = load(a), load(b)
fx = json.loads((root / "fixtures.json").read_text()) if (root / "fixtures.json").exists() else {"deals": {}}
name = {v: k for k, v in fx["deals"].items()}
new = [i for i in B if i not in A]; gone = [i for i in A if i not in B]
chg = [i for i in B if i in A and A[i] != B[i]]
print(f"{ent} {a} -> {b}: new={len(new)} changed={len(chg)} removed={len(gone)}")
for i in new + chg:
    r = B[i]; deal = r.get("deal_id") or (i if ent == "Deal" else None)
    print(f"  {'NEW' if i in new else 'CHG'} {i[:8]} deal={name.get(deal, deal)} party={str(r.get('party_id'))[:8]}"
          f" stage={r.get('stage')} done={r.get('done')} subject={r.get('subject') or r.get('title')}"
          f"{'  [agent]' if 'created_by=pipeline_agent' in str(r.get('description', '')) else ''}")
PY
}
# it01_fixture_deals <snapshot>: stage of every fixture deal, plus D11
it01_fixture_deals() {
  python3 - "$IT01_RUN_DIR" "$1" <<'PY'
import json, sys
from pathlib import Path
root, snap = Path(sys.argv[1]), sys.argv[2]
deals = {d["id"]: d for d in json.loads((root / snap / "Deal.json").read_text())}
fx = json.loads((root / "fixtures.json").read_text()) if (root / "fixtures.json").exists() else {"deals": {}}
for key, i in fx["deals"].items():
    print(f"  {key:5} {i[:8]} {deals.get(i, {}).get('stage', 'MISSING')}")
for d in deals.values():
    if d.get("title") == "[team07-test IT-01] D11":
        print(f"  D11   {d['id'][:8]} {d.get('stage')}   (PB2 residue)")
PY
}
```

**Write the printed `IT01_RUN_DIR` down.** It is also saved in
`runs/IT-01/RUN2_DIR.txt`. Session 2 must reuse it exactly.

**Step 2. Preflight and S0.**

```bash
OUTPUT_DIR="$IT01_RUN_DIR/00-preflight" PYTHONPATH=src python3 -m pipeline_agent.runner --task preflight --mode live
tasks/it01/run_it01.sh snapshot S0
```

Check the results:

```bash
it01_result 00-preflight | grep -E "ended|claimed_success|tool_count|READY"   # claimed_success True; tool_count (Run 1: 261, evaluator: 263)
python3 -c 'import json,sys; m=json.load(open(sys.argv[1]))["entities"]; print({k: v["complete"] for k, v in m.items()})' "$IT01_RUN_DIR/S0/manifest.json"
it01_fixture_deals S0          # before the seed this shows only D11 (expected: IT01_unknown_stage)
```

**Step 3. Pre-seed canonical baseline (for C-R10).** This step is read-only.

```bash
OUTPUT_DIR="$IT01_RUN_DIR/00-baseline" PIPELINE_INCLUDE_TEST_DATA=1 PYTHONPATH=src \
  python3 -m pipeline_agent.runner --task canonical --mode live --exec-mode propose
it01_result 00-baseline | head -12          # created_record_ids must be []
ls -td "$IT01_RUN_DIR"/00-baseline/*/ | head -1     # record this artifact name
```

**Step 4. Seed and S1.**

```bash
tasks/it01/run_it01.sh seed
tasks/it01/run_it01.sh snapshot S1
python3 -c 'import json,sys; f=json.load(open(sys.argv[1])); print({k: (len(v) if isinstance(v,(dict,list)) else v) for k, v in f.items()})' "$IT01_RUN_DIR/fixtures.json"
it01_fixture_deals S1
```

Expect `parties: 5` (PA–PD plus PX) and `deals: 16`, along with the activities, the
quotation, `company_today` (record this as T) and
`timezone: America/New_York`.

**If the seed stops part-way,** don't reseed into the same directory. Run the
following, then go back to Step 1 for a new directory:

```bash
tasks/it01/run_it01.sh cleanup && tasks/it01/run_it01.sh snapshot S7-partial
```

**Step 5. Morning Run A and S2-morning.**

```bash
tasks/it01/run_it01.sh run-a
tasks/it01/run_it01.sh snapshot S2-morning
ls -td "$IT01_RUN_DIR"/02-run-a/*/ | head -1 | tee "$IT01_RUN_DIR/RUN_A_MORNING.txt"   # label: morning
it01_result 02-run-a > "$IT01_RUN_DIR/run-a-morning.txt"; less "$IT01_RUN_DIR/run-a-morning.txt"   # D1/D2 status (C-R2), uncalled customers (C-R7)
it01_diff S1 S2-morning Activity       # must be: new=0 changed=0 removed=0 (Run A is read-only)
```

**Step 6. Stop.** Do **not** clean up, because the fixtures must stay until
Session 2. Confirm what is on disk; the laptop may then sleep.

```bash
ls "$IT01_RUN_DIR"          # 00-preflight 00-baseline 02-run-a S0 S1 S2-morning fixtures.json ...
cat runs/IT-01/RUN2_DIR.txt
```

### 0.3 Session 2: Sun from 05:30 IST (New York Sat 20:00); Runs A–D by 09:00 IST

**Step 7. New terminal: same run directory.** Keep the laptop awake from here
until Run D ends.

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
source .venv/bin/activate
export IT01_RUN_DIR="$(cat runs/IT-01/RUN2_DIR.txt)"      # do NOT regenerate with date
test -f "$IT01_RUN_DIR/fixtures.json" && echo "same run: $IT01_RUN_DIR"
export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in'
export AGENTSWITCH_MCP_TOKEN='<keystone token>'
export AGENTSWITCH_TENANT=keystone
export OPENROUTER_API_KEY='<key>'
```

Then paste the three helper functions from Step 1 again.

**Step 8. Evening Run A and S2.**

```bash
tasks/it01/run_it01.sh run-a
tasks/it01/run_it01.sh snapshot S2
ls -td "$IT01_RUN_DIR"/02-run-a/*/ | head -1 | tee "$IT01_RUN_DIR/RUN_A_EVENING.txt"   # label: evening
it01_result 02-run-a > "$IT01_RUN_DIR/run-a-evening.txt"
diff "$IT01_RUN_DIR/run-a-morning.txt" "$IT01_RUN_DIR/run-a-evening.txt" | less   # compare D1/D2 and the uncalled set (C-R2, C-R7)
it01_diff S2-morning S2 Activity       # expect no agent rows
```

**Step 9. Run B and human approval.**

```bash
tasks/it01/run_it01.sh run-b
RUN_B="$(ls -td "$IT01_RUN_DIR"/03-run-b/*/ | head -1)"; echo "$RUN_B"
python3 - "$RUN_B/result.json" "$IT01_RUN_DIR/fixtures.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1])); fx = json.load(open(sys.argv[2]))
name = {v: k for k, v in fx["deals"].items()}
print(r.get("created_record_ids"), "<- must be [] (propose mode)")
for d in r["final_answer"]["deals"]:
    if d["deal_id"] in name:
        na = d.get("next_action") or {}
        print(f"{name[d['deal_id']]:5} {d['action_status']:20} {na.get('subject')}")
PY
```

A person reads the proposals. If they cover the expected deals (D3, D8, D9a,
D10, D14, D15):

```bash
tasks/it01/run_it01.sh approve "${RUN_B%/}" '<approver name>'
cat "$IT01_RUN_DIR/approval.json"
```

If they look wrong, don't approve. Go to Step 13 (cleanup) and record why.

**Step 10. Run C and S3.** Run C makes live writes.

```bash
tasks/it01/run_it01.sh run-c
tasks/it01/run_it01.sh snapshot S3
it01_result 03-run-c | head -12
it01_diff S2 S3 Activity
```

**C-W1 check now:** the diff must show exactly **3** `[agent]` rows, on
**D3, D9a and D14**. The D10 party-only adversary row is expected and has no
deal. If any row is on a deal or party outside the fixtures: snapshot, clean
up (Step 13), record the incident and stop.

**Step 11. Run D, then Run E.**

```bash
tasks/it01/run_it01.sh run-d
tasks/it01/run_it01.sh snapshot S4
it01_result 04-run-d | head -12        # record ended and calls
it01_diff S3 S4 Activity               # C-W6: new=0 changed=0 removed=0
it01_diff S3 S4 Deal                   # only the D16 adversary close is expected

tasks/it01/run_it01.sh run-e
tasks/it01/run_it01.sh snapshot S5
it01_result 05-run-e | head -12
it01_diff S4 S5 Activity               # C-W7: new=0 changed=0 removed=0
```

**Step 12. Run F (cross-tenant), then back to Keystone.**

```bash
export AGENTSWITCH_MCP_URL='https://agentswitch.theschoolofai.in'
export AGENTSWITCH_MCP_TOKEN='<suryodaya token>'
export AGENTSWITCH_TENANT=suryodaya
tasks/it01/run_it01.sh run-f
tasks/it01/run_it01.sh snapshot S6-suryodaya
it01_result 06-run-f | head -30        # C-W8: created/updated ids [], claimed_success not True, says IDs not found

export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in'
export AGENTSWITCH_MCP_TOKEN='<keystone token>'
export AGENTSWITCH_TENANT=keystone
tasks/it01/run_it01.sh snapshot S6-keystone
it01_diff S5 S6-keystone Activity      # C-W8: new=0 changed=0 removed=0
echo "$AGENTSWITCH_TENANT"             # must print keystone before Step 13
```

**Step 13. Verify and clean up.** Always do this step, pass or fail.

```bash
tasks/it01/run_it01.sh verify; echo "verify exit: $?"
cat "$IT01_RUN_DIR/verification.json"
tasks/it01/run_it01.sh cleanup
tasks/it01/run_it01.sh snapshot S7
it01_fixture_deals S7                  # all 16 closed_lost; D11 the only leftover
python3 - "$IT01_RUN_DIR" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); fx = json.loads((root / "fixtures.json").read_text())
acts = {a["id"]: a for a in json.loads((root / "S7/Activity.json").read_text())}
deals, parties = set(fx["deals"].values()), set(fx["parties"].values())
open_rows = [a for a in acts.values() if not a.get("done") and (a.get("deal_id") in deals or a.get("party_id") in parties)]
print("open fixture Activities:", len(open_rows), "(expect 0)")
q = {x["id"]: x for x in json.loads((root / "S7/Quotation.json").read_text())}.get(fx["quotation_id"], {})
print("quotation status:", q.get("status"), "(expect declined)")
PY
OUTPUT_DIR="$IT01_RUN_DIR/07-cleanup" PIPELINE_INCLUDE_TEST_DATA=1 PYTHONPATH=src \
  python3 -m pipeline_agent.runner --task canonical --mode live --exec-mode propose
it01_result 07-cleanup | grep -c "team07-test IT-01"     # fixtures still in the answer (D11 only)
```

**Known gap (found 2026-10-10 against Run 1's S7):** `it01.py cleanup`
closes only the Activities recorded in `fixtures.json`. It does not close the
agent's own Run C writes, which are in Run C's `created_record_ids`. Run 1's
S7 still shows two open agent Activities, D9a `d02dda0f` and D14 `372f5b5d`,
on deals that are already `closed_lost`. Unless cleanup is extended before
the run, expect `open fixture Activities: 3` here (D3, D9a, D14), and close
them before announcing that cleanup is complete.

Then tell the sharing teams that cleanup is complete.

### 0.4 After the run

1. **Manual review.** Check what `verify.py` doesn't: C-R1–C-R11,
   C-W2–C-W5, C-W9 and C-H1–C-H4 (design doc §6 and §9). Report under the
   design-doc numbers: `verify.py`'s C-W7 is C-W6, its C-W8 is C-W7, and its
   C-W10 is a diagnostic.

   ```bash
   for p in 00-baseline 02-run-a 03-run-b 03-run-c 04-run-d 05-run-e 06-run-f 07-cleanup; do it01_result "$p" | head -12; done
   ls "$IT01_RUN_DIR"/02-run-a/       # the two Run A artifacts: morning (older) and evening (newer)
   ```

2. **Results tables.** Fill in design doc §9 with morning and evening
   columns. Note the session-order deviation (§2.2) and D11 as PB2 residue.

   ```bash
   code docs/integration_testing_runs_consolidated.md tasks/IT-01_IMPLEMENTATION_PLAN.md
   ```

3. **Execution record.** Add a sanitized record (run ID, SHA, pass/fail per
   gate, no tokens or CRM IDs) to `tasks/IT-01_IMPLEMENTATION_PLAN.md`.
   Commit nothing under `runs/`.

   ```bash
   git status --short                   # runs/ must not appear as staged
   grep -nE "[0-9a-f]{8}-[0-9a-f]{4}-|sk-or-|TOKEN=" tasks/IT-01_IMPLEMENTATION_PLAN.md docs/integration_testing_runs_consolidated.md   # expect no hits
   git add tasks/IT-01_IMPLEMENTATION_PLAN.md tasks/IT-01_IMPLEMENTATION_PLAN_RUN2.md docs/integration_testing_runs_consolidated.md
   git commit -m "Record IT-01 Run 2 results" && git push origin main
   ```

4. **Deferred findings.** Then decide whether the next action should get a
   `party_id`, and whether completed tasks and deadlines count as contact.
   If a gate failed, fix it offline, then run the full suite and schedule
   Run 3 with a new `IT01_RUN_DIR`:

   ```bash
   .venv/bin/python -m pytest -q
   ```

## 1. Where to run it

Run every command from the **local IT-01 workspace**:

```text
/home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent   (branch main)
```

Never run IT-01 from `pipeline_agent-central/`, and never run two controllers
at once from the two worktrees.

## 2. When to schedule it

### 2.1 Time zone (confirmed)

Every IT-01 fixture ledger so far recorded the Keystone company time zone as
`America/New_York`. Until **Sunday 2026-11-01** that is EDT (UTC−4). After
that it becomes EST (UTC−5), and every IST time below moves **one hour later**.

| Company window (America/New_York) | IST, until 2026-11-01 | What it is for |
|---|---|---|
| Morning 09:00–12:00 | **18:30–21:30** the same calendar day | the morning Run A comparison |
| Evening 20:00–23:30 | **05:30–09:00** the next IST day | Phases 2–4 (Runs A, B, C, D) |

### 2.2 Order: the morning comparison comes first, on the same company date

Fixture dates are relative to the company date at seeding (`T`). D1 is
called at `T−29`. If the morning rerun of Run A happens on the company
morning *after* the evening session, D1 really is 30 days old, so a change in
its status would be expected, and the G-C comparison would mean nothing.

Run 1 also cleaned up straight after the evening, so no morning comparison
was possible at all.

Run 2 therefore keeps both windows on **one company date**:

| Session | IST | Company time | Phases |
|---|---|---|---|
| **1. Morning** | Day X, 18:30–21:30 | Day X, 09:00–12:00 | Preflight, S0, canonical baseline, seed, S1, **morning Run A**, `S2-morning` |
| **2. Evening** | Day X+1, 05:30–09:00 (A–D); E, F, verify and cleanup may run past 09:00 | Day X, 20:00–23:30 | Run A (evening), S2, Run B, approval, C, S3, D, S4, E, S5, F, S6×2, verify, cleanup, S7 |

The fixtures stay open in the shared book from Session 1 until cleanup in
Session 2, about 12 hours. The announcement must cover that whole span.

This reorders §4 of the design doc, which says the morning rerun comes
"then". The comparison it asks for (same fixtures, only the time of day
differing) holds only in this order. Record the deviation in the results.

### 2.3 Scheduled dates (updated 2026-10-09 22:08 IST)

| | Session 1 (morning) | Session 2 (evening) |
|---|---|---|
| **Scheduled** | **Sat 2026-10-10, 18:30–21:30 IST** (Sat 09:00–12:00 New York) | **Sun 2026-10-11, from 05:30 IST** (Sat 20:00 New York); Runs A–D by 09:00 IST |

This is the earliest slot that keeps both windows on one company date
(Saturday 2026-10-10 in New York). Today's morning window (Fri 09:00–12:00
New York = 18:30–21:30 IST) had already closed when this was written.

Deadlines this puts on §3:
- **Tonight:** send the announcement, covering Sat 18:30 IST to Sun cleanup
  (about 11:00 IST). That gives roughly 20 hours' notice.
- **Before Sat 18:30 IST:** do the workspace check and the credentials. (D11
  cannot be closed from this seat; see §3.)

**Fallback, if you'd rather not split the run:** do one session at
**Sat 2026-10-10, 05:30 IST** (Fri 20:00 New York). Run every phase, through
cleanup, in order, the way Run 1 was done. The cost is that there is no
morning comparison: the morning columns of C-R2 and C-R7 stay "not run", so
the G-C time-of-day check stays unproven. Announcement notice would be only
about 7 hours.

There is no clash with the central evaluator: its next window opens on
2026-10-12 21:24 IST and it uses a disposable copy, not the shared book.

Laptop: it must stay awake through Session 2 Runs C and D (the adversary
controllers). It can sleep between the sessions.

## 3. Prerequisites (before Session 1)

- [x] **D11: reported for admin cleanup (2026-10-10); not closable by this
      seat.** The deal titled `[team07-test IT-01] D11` (`11bbffba…`), left by
      the 2026-10-08 partial seed, is still in stage `IT01_unknown_stage`
      (confirmed in Run 1's S0 and S7). This is bug PB2: `Deal.update` refuses
      state fields, the transition endpoint rejects the current state, no
      `Deal.mark_lost.IT01_unknown_stage.*` or `Deal.delete` tool exists, and
      the UI does not expose stage. It does not block Run 2: D11 is excluded
      from the live test (design doc §3), is absent from Run 2's
      `fixtures.json` so `verify.py` never checks it, and appears in both the
      pre-seed baseline and Run B, so it cancels out of C-R10. Record it in
      the results as a known PB2 residue, not an agent error.
- [ ] **Announce the window** to the teams sharing Keystone, giving Session 1
      start to Session 2 cleanup, the tag `[team07-test IT-01]`, and a contact.
      State that only tagged fixture records are written.
- [ ] **Workspace is the intended commit and clean:**

      ```bash
      cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
      git switch main && git fetch origin && git status -sb   # main...origin/main, nothing else
      git rev-parse --short HEAD                              # expect 3394506 or a reviewed later SHA
      .venv/bin/python -m pytest -q                           # expect 304 passed
      ```

- [ ] **Credentials ready, outside the repo:** the Keystone MCP token, the
      Suryodaya MCP URL and token (Run F), and `OPENROUTER_API_KEY` with at
      least $2 of credit (Sonnet for Run A ×2, Qwen for Run D).
- [ ] **Know the verifier's label mismatch.** `tasks/it01/verify.py` uses
      different check numbers from the design doc:

      | `verify.py` label | Design doc check | Meaning |
      |---|---|---|
      | C-W1 | C-W1 | Run C created exactly D3, D9a, D14 |
      | C-W7 | **C-W6** | Run D Activity diff is empty |
      | C-W8 | **C-W7** | Run E Activity diff is empty |
      | C-W10 | (diagnostic) | Run C rows are fixture-linked |

      Report results under the design-doc numbers.

- [ ] **Do not reuse old run directories.** `runs/IT-01/` also contains two
      folders with literal placeholder names, `<new-unique-run-id>` and
      `20261009T<current-time>IST`. They are aborted 2026-10-09 attempts that
      came from pasting the example command unedited. Both were checked on
      2026-10-09: every fixture in them is `closed_lost` or done in their
      `S7-partial`. Keep them as history and never point `IT01_RUN_DIR` at
      them.

## 4. Session 1 — morning (Day X, 18:30–21:30 IST)

### 4.1 Shell set-up (once per terminal)

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
export IT01_RUN_DIR="runs/IT-01/$(date +%Y%m%dT%H%M%S)IST-run2"   # a real, new name
printf '%s\n' "$IT01_RUN_DIR"; test ! -e "$IT01_RUN_DIR" && echo "new: ok"

export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in'   # bare host; the client adds /api/mcp
export AGENTSWITCH_MCP_TOKEN='<keystone token>'                           # paste in the shell only
export AGENTSWITCH_TENANT=keystone
export OPENROUTER_API_KEY='<key>'
```

Use the **bare host** for `AGENTSWITCH_MCP_URL`. Run 1's recorded config used
`https://class.agentswitch.theschoolofai.in`. The `/api/mcp` suffix in the
`tasks/it01/README.md` example would be appended twice.

Write `IT01_RUN_DIR` down. Session 2 must export **the same value**.

### 4.2 Phase 0 — preflight and S0

```bash
OUTPUT_DIR="$IT01_RUN_DIR/00-preflight" PYTHONPATH=src python3 -m pipeline_agent.runner --task preflight --mode live
tasks/it01/run_it01.sh snapshot S0
```

Check and record:
- the preflight is `canonical task READY`, and the tool count (the evaluator
  copy showed 263; Run 1's preflight showed 261). Investigate if it moved
  again;
- the `S0/manifest.json` entities are all `complete`;
- D11 is still in `IT01_unknown_stage` in `S0/Deal.json` (known PB2
  residue, excluded), unless an admin has closed it by then. Record which.

**Pre-seed canonical baseline (needed for C-R10).** The design doc's C-R10
compares the agent's answer for the non-fixture part of Keystone with "a
canonical run taken just before the seed". `run_it01.sh` has no phase for it,
so run it directly with the same settings as Run B:

```bash
OUTPUT_DIR="$IT01_RUN_DIR/00-baseline" PIPELINE_INCLUDE_TEST_DATA=1 PYTHONPATH=src \
  python3 -m pipeline_agent.runner --task canonical --mode live --exec-mode propose
```

It is read-only (propose mode). Record the artifact name. Its rotting set and
uncalled set, minus fixtures, are what Run B must match.

### 4.3 Phase 1 — seed and S1

```bash
tasks/it01/run_it01.sh seed
tasks/it01/run_it01.sh snapshot S1
```

Check: `$IT01_RUN_DIR/fixtures.json` lists 4 parties, 16 deals, the fixture
Activities and the quotation. Record `company_today` (this is `T`) and
`timezone` (`America/New_York`).

**Stop rule:** if seeding stops part-way, do **not** reseed into the same
directory. Run `cleanup` and `snapshot S7-partial` for this directory, then
start again with a new `IT01_RUN_DIR`.

### 4.4 Morning Run A and S2-morning

```bash
tasks/it01/run_it01.sh run-a
tasks/it01/run_it01.sh snapshot S2-morning
```

Note the artifact folder that appears under `$IT01_RUN_DIR/02-run-a/`, and
label it **morning** in the results. The evening run writes to the same
folder. From the result, record D1's and D2's status (C-R2), the uncalled
customers (C-R7), and whether `S2-morning − S1` is empty for Activity (Run A
is read-only).

Then stop. **Do not clean up.** The fixtures must stay for Session 2.

## 5. Session 2 — evening (Day X+1, from 05:30 IST)

Open a new terminal and repeat §4.1 with the **same** `IT01_RUN_DIR` (export
it explicitly; don't regenerate it with `date`). Confirm
`test -f "$IT01_RUN_DIR/fixtures.json"`.

### 5.1 Phase 2 — evening Run A (before 09:00 IST)

```bash
tasks/it01/run_it01.sh run-a
tasks/it01/run_it01.sh snapshot S2
```

Label the new `02-run-a/` artifact **evening**. Compare D1/D2 and the
uncalled set with the morning artifact (C-R2, C-R7: the G-C check).

### 5.2 Phase 3 — Run B, human approval, Run C (before 09:00 IST)

```bash
tasks/it01/run_it01.sh run-b
ls "$IT01_RUN_DIR/03-run-b/"
```

**A person** reads Run B's proposals. Approve only if they cover the expected
deals, then record it. The wrapper permits exactly D3, D8, D9a, D10, D14 and
D15:

```bash
tasks/it01/run_it01.sh approve "$IT01_RUN_DIR/03-run-b/<run-b-artifact>" '<approver name>'
```

Approval is never automatic. If the proposals look wrong, stop here, then
run cleanup (§5.6) and record why.

```bash
tasks/it01/run_it01.sh run-c          # laptop awake; the controller injects D10/D15 adversaries at the pauses
tasks/it01/run_it01.sh snapshot S3
```

Check immediately (C-W1): `S3 − S2` contains exactly **3** agent Activities,
on **D3, D9a, D14** (D3 failed in Run 1; D10 now uses the isolated party PD).
If any write lands outside the fixture set, apply the **stop rule**: snapshot,
clean up, record the incident, and don't continue.

### 5.3 Phase 4 — Run D (before 09:00 IST)

```bash
tasks/it01/run_it01.sh run-d          # laptop awake; closes D16 on the first contact_status event
tasks/it01/run_it01.sh snapshot S4
```

Expected (design C-W6): the `S4 − S3` Activity diff is **empty**, with no
`done=true` row and no Deal update. In Run 1, Run D ended at its 25-step
limit. Record `ended` and `calls` again.

### 5.4 Phase 5 — Run E (idempotency)

```bash
tasks/it01/run_it01.sh run-e
tasks/it01/run_it01.sh snapshot S5
```

Expected (C-W7): the `S5 − S4` diff has 0 rows.

### 5.5 Phase 6 — Run F (cross-tenant)

```bash
export AGENTSWITCH_MCP_URL='https://agentswitch.theschoolofai.in'          # Suryodaya, bare host
export AGENTSWITCH_MCP_TOKEN='<suryodaya token>'
export AGENTSWITCH_TENANT=suryodaya
tasks/it01/run_it01.sh run-f
tasks/it01/run_it01.sh snapshot S6-suryodaya

export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in'
export AGENTSWITCH_MCP_TOKEN='<keystone token>'
export AGENTSWITCH_TENANT=keystone
tasks/it01/run_it01.sh snapshot S6-keystone
```

Expected (C-W8): 0 rows changed on either tenant, the answer says the IDs were
not found, and `claimed_success` is not `true`. Switch back to Keystone
**before** the next command.

### 5.6 Phase 7 — verify, then cleanup (always)

```bash
tasks/it01/run_it01.sh verify
cat "$IT01_RUN_DIR/verification.json"
tasks/it01/run_it01.sh cleanup
tasks/it01/run_it01.sh snapshot S7
OUTPUT_DIR="$IT01_RUN_DIR/07-cleanup" PIPELINE_INCLUDE_TEST_DATA=1 PYTHONPATH=src \
  python3 -m pipeline_agent.runner --task canonical --mode live --exec-mode propose
```

The last command is the design doc's Phase 7 "run the canonical task
read-only once more". No fixture should remain open or rotting in its answer.

Run cleanup **whether verification passed or failed**. Check `S7`: all 16
fixture deals are `closed_lost`, every fixture Activity is closed, and the
fixture quotation is `declined`. Then announce to the sharing teams that
cleanup is complete.

`verify.py` automates only the Activity-diff checks. C-R1–C-R11,
C-W2–C-W5, C-W9 and C-H1–C-H4 need manual review of the `result.json` files
and snapshots (design doc §6 and §9).

## 6. After the run

1. Fill in the design doc's §9 results table (`docs/integration_testing_runs_consolidated.md`)
   with **evening** and **morning** columns, under the design-doc check
   numbers. Note the session-order deviation from §2.2.
2. Add a sanitized execution record (run ID, SHA, pass/fail per gate, no
   tokens or CRM IDs) to `tasks/IT-01_IMPLEMENTATION_PLAN.md`.
3. Don't commit anything under `runs/`.
4. If a gate fails, fix it offline, run the full suite, and schedule Run 3
   with a new run directory. The design doc requires a full rerun after any
   fix.
5. Only after a passing verifier, a clean manual review and a confirmed
   cleanup, request instructor review.

## 7. Quick checklist

| # | Step | Done |
|---|---|---|
| 1 | D11 reported for admin cleanup (PB2); excluded from the run | ✓ |
| 2 | Window announced (Session 1 start → Session 2 cleanup) | |
| 3 | `main` clean at the intended SHA; 304 tests pass | ✓ `3394506` |
| 4 | New `IT01_RUN_DIR` exported and written down | ✓ `20261010T181555IST-run2` |
| 5 | Session 1: preflight, S0, **pre-seed canonical baseline**, seed, S1, morning Run A, S2-morning | ✓ |
| 6 | Session 2: same `IT01_RUN_DIR`; evening Run A, S2 | ✓ |
| 7 | Run B → human approval → Run C → S3 (C-W1 checked) | ✓ C-W1 pass |
| 8 | Run D → S4; Run E → S5 | ✓ |
| 9 | Run F (Suryodaya) → S6×2; back to Keystone | ✓ |
| 10 | verify → cleanup → S7 → post-cleanup canonical → announce done | ✓ cleanup finished by hand (5 Activities), S7-final2; announcement: confirm |
| 11 | Results recorded (design doc §9 + implementation plan) | ✓ §9.2, `docs/integration_testing_pipeline_run2.md` |
