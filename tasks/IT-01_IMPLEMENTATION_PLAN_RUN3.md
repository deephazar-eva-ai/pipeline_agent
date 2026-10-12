# IT-01 Run 3: schedule and step-by-step procedure

**Scenario:** `docs/integration_testing_pipeline.md` ("Hostile Monday")
**Package:** `tasks/it01/` (see `tasks/it01/README.md`)
**Previous run:** `tasks/IT-01_IMPLEMENTATION_PLAN_RUN2.md` (procedure and reasons) and `docs/integration_testing_pipeline_run2.md` (results)
**Written:** 2026-10-11, after Run 2

Run 2 (`20261010T181555IST-run2`, on `3394506`) passed every write check and
failed the reading checks on gaps G-A to G-E, C-R5 (loop) and C-R6. Those are
fixed on branch `it01-run3-fixes`. The design doc requires a full rerun after
any fix, so Run 3 repeats IT-01 in full: both sessions, same company date,
every phase.

The procedure is Run 2's, unchanged except where §0.1 says so. The reasons
behind each step (time zone, session order, fixture traps) are in the Run 2
plan §§1–5 and are not repeated here.

## 0. Run-day runbook (Sun 2026-10-11 → Mon 2026-10-12)

### 0.1 What changed since Run 2, and what must be true before Session 1

| Item | Status |
|---|---|
| Agent fixes (G-A to G-E, C-R5/C-R6, G-G) | On `it01-run3-fixes` (`529f7c4`); 318 tests pass with the hand-written test updates (`c0e7696`) |
| Code under test | **Branch `it01-run3-fixes`, not `main`.** Run 3 must be green on the branch before the merge. `main` has no commits the branch lacks, so the merge is a fast-forward and `main` gets exactly the tested code. Branch head at Step 0: `________` |
| Suryodaya UI check, then merge to `main` | **After Run 3**, on Mon 2026-10-12 (the UI is available only that day). Merge only if Run 3 is clean and green and the UI check looks right |
| `central-eval` | Unchanged at `3394506`. Update only after Run 3 passes |
| Decision 1 (C-H2, option B) and Decision 2 (C-R7 = {PD}, option A) | Chosen by the team 2026-10-11 and applied to the design doc §6. Instructor sign-off requested (Run 2 record §1) |
| Cleanup | `it01.py cleanup` now also closes the agent's own writes (Step 13). No hand cleanup expected |
| Loop: completed Activity creates | Refused by the write policy (G-G). Run D's D8 note can no longer produce a `done=true` call |
| D11 | Still open (PB2), waiting for admin; excluded from the run, as in Run 2 |
| Announcement to Keystone teams | **Send before 18:30 IST Sun 2026-10-11:** window Sun 18:30 IST → Mon 2026-10-12 ≈ 11:00 IST, tag `[team07-test IT-01]`, "only tagged fixture records are written", D11 still visible |
| OpenRouter credit | Run 2 used roughly $0.30–0.40 by token count (2 Sonnet Run A, 1 Qwen Run D). Check that at least $1 is left |
| Commits on `it01-run3-fixes` | **None from Step 0 until cleanup is done**, docs included, so the branch head stays the SHA under test |

### 0.2 Session 1: Sun 2026-10-11 18:30–21:30 IST (New York Sun 09:00–12:00)

**Step 0. Before 18:30 IST: final workspace check.**

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
git switch it01-run3-fixes && git fetch origin && git status -sb    # it01-run3-fixes...origin/it01-run3-fixes, clean
git rev-parse --short HEAD                               # record as the branch head in §0.1
.venv/bin/python -m pytest -q                            # 318 passed (or more)
```

**Step 1. Shell set-up and check helpers.** Do this once per terminal.

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
source .venv/bin/activate
export IT01_RUN_DIR="runs/IT-01/$(date +%Y%m%dT%H%M%S)IST-run3"
test ! -e "$IT01_RUN_DIR" && echo "new: ok"
mkdir -p "$IT01_RUN_DIR" && printf '%s\n' "$IT01_RUN_DIR" | tee runs/IT-01/RUN3_DIR.txt
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
`runs/IT-01/RUN3_DIR.txt`. Session 2 must reuse it exactly.

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
cat runs/IT-01/RUN3_DIR.txt
```

### 0.3 Session 2: Mon 2026-10-12 from 05:30 IST (New York Sun 20:00); Runs A–D by 09:00 IST

**Step 7. New terminal: same run directory.** Keep the laptop awake from here
until Run D ends.

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
source .venv/bin/activate
export IT01_RUN_DIR="$(cat runs/IT-01/RUN3_DIR.txt)"      # do NOT regenerate with date
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

**Cleanup now includes the agent's own writes.** Since 2026-10-11,
`it01.py cleanup` also closes every open, tagged Activity listed in any
runner artifact's `created_record_ids` under `$IT01_RUN_DIR` (Run C's three
next actions). It prints `closed agent-written Activity <id>` for each one.
Expect `open fixture Activities: 0` above. If it is not 0, close the rows
listed before announcing that cleanup is complete.

Then tell the sharing teams that cleanup is complete.

### 0.4 After the run

1. **Manual review.** Check what `verify.py` doesn't: C-R1–C-R11,
   C-W2–C-W5, C-W9 and C-H1–C-H4 (design doc §6 and §9). Report under the
   design-doc numbers: `verify.py`'s C-W7 is C-W6, its C-W8 is C-W7, and its
   C-W10 is a diagnostic.

   ```bash
   for p in 00-baseline 02-run-a 03-run-b 03-run-c 04-run-d 05-run-e 06-run-f 07-cleanup
   do
     echo "== $p"
     it01_result "$p" 2>/dev/null | head -12
   done
   ls "$IT01_RUN_DIR"/02-run-a/       # the two Run A artifacts: morning (older) and evening (newer)
   ```

2. **Results tables.** Fill in design doc §9 with morning and evening
   columns, as §9.3 (Run 2 is §9.2). Record the full run in
   `docs/integration_testing_pipeline_run3.md`, as for Run 2. Note D11 as PB2 residue.

   ```bash
   code docs/integration_testing_pipeline.md tasks/IT-01_IMPLEMENTATION_PLAN.md docs/integration_testing_pipeline_run2.md
   ```

3. **Execution record.** Add a sanitized record (run ID, SHA, pass/fail per
   gate, no tokens or CRM IDs) to `tasks/IT-01_IMPLEMENTATION_PLAN.md`.
   Commit nothing under `runs/`.

   ```bash
   git status --short                   # runs/ must not appear as staged
   grep -nE "[0-9a-f]{8}-[0-9a-f]{4}-|sk-or-|TOKEN=" tasks/IT-01_IMPLEMENTATION_PLAN.md docs/integration_testing_pipeline.md   # expect no hits
   git add tasks/IT-01_IMPLEMENTATION_PLAN.md tasks/IT-01_IMPLEMENTATION_PLAN_RUN3.md docs/integration_testing_pipeline.md docs/integration_testing_pipeline_run3.md
   git commit -m "Record IT-01 Run 3 results" && git push    # on it01-run3-fixes; merge to main only after the UI check
   ```

4. **Deferred findings.** Still open after Run 3: the next action has no
   `party_id` (G-H, C-W5 reported) and the loop's guard does not re-read the
   deal's stage (G-F). If a gate failed, fix it offline, run the full suite,
   and schedule Run 4 with a new `IT01_RUN_DIR`:

   ```bash
   .venv/bin/python -m pytest -q
   ```

## 1. Expected results (offline replay of Run 2's S2 snapshot through the fixed code)

These are predictions written before the run, as §7 of the design doc did for
Run 2. The replay ran the canonical task and the loop tool with the clock
frozen at 09:05 and at 20:11 New York; both windows gave the same answer.

| Check | Expected in Run 3 | Gap fixed |
|---|---|---|
| C-R1 | Rotting fixtures = D3, D4, D5, D8, D9a, D9b, D10, D13, D14, D15, D16 exactly. D6 not rotting, flagged `agent_marker_on_completed_contact` (`run=fake-0000`). D7 in `data_issues` as `insufficient_evidence` | G-A, G-D, G-E |
| C-R2 | D1 (29 days) and D2 (30 days) not rotting, morning **and** evening | G-B, G-C |
| C-R3 | D4's last contact T−90, not T | G-A |
| C-R4 | Unchanged (pass) | — |
| C-R5 | D12 in `excluded_deals` (Run B) and in every loop response's `must_report` | loop |
| C-R6 | D13's mis-linked row in `mismatched_links` (Run B) and loop `must_report` | C-R6 |
| C-R7 | Uncalled fixture customers = {PD} exactly; PB not listed | G-D, Decision 2 |
| C-R8 – C-R11 | Unchanged (pass) | — |
| C-W1 – C-W9 | Unchanged (pass); C-W5 still reported (no `party_id`) | — |
| C-H2 | Scored under option B | Decision 1 |

**Risk to watch: the loop's answers (Run A) still depend on the model.** In
Run 2, the morning model left every test customer out of its uncalled list,
and neither model mentioned D12 or D13. `must_report` now puts those findings
in every `contact_status` response, and the tool description tells the model
to name each one, but the model can still leave them out. If Run A misses them
while Run B gets them right, record it as a loop finding against C-R5, C-R6 or
C-R7, not as a reading-rule failure.

**Possible side effect on the rest of Keystone (C-R10):** G-A changes what
counts as contact on every deal. The Run 2 replay kept the same 25 rotting
deals and 6 uncalled customers, but the book may have changed since. Compare
Run B with this run's own pre-seed baseline (Step 3), as before.

## 2. Schedule

Both sessions must fall on **one company date** (Run 2 plan §2.2): Session 1
in the New York morning, Session 2 that New York evening (the next IST
morning). New York is UTC−4 until 2026-11-01.

| Session | IST | New York | Phases |
|---|---|---|---|
| 1. Morning | Day X, 18:30–21:30 | Day X, 09:00–12:00 | Preflight, S0, baseline, seed, S1, morning Run A, S2-morning |
| 2. Evening | Day X+1, from 05:30 (A–D by 09:00) | Day X, 20:00–23:30 | Evening Run A, S2, Run B, approval, Run C, S3, Run D, S4, Run E, S5, Run F, S6×2, verify, cleanup, S7 |

**Day X: Sun 2026-10-11** (company date T = 2026-10-11, New York). Chosen 2026-10-11 15:00 IST.

| Deadline (IST, Sun 2026-10-11) | Item |
|---|---|
| before 18:30 | Announcement sent; Step 0 workspace check |
| 18:30–21:30 | Session 1 |
| Mon 05:30–09:00 | Session 2, Runs A–D; E, F, verify, cleanup may run past 09:00 |

After Run 3 (Mon 2026-10-12): record the results; if clean and green, do the Suryodaya UI check on the branch, then merge to `main` and push.

## 3. Quick checklist

| # | Step | Done |
|---|---|---|
| 1 | Working tree on `it01-run3-fixes`, clean; branch head recorded in §0.1; 318 tests pass | |
| 2 | No commits on the branch from Step 0 until cleanup | |
| 3 | Day X chosen (Sun 2026-10-11); window announced | |
| 4 | New `IT01_RUN_DIR` (`…IST-run3`) exported and written to `runs/IT-01/RUN3_DIR.txt` | |
| 5 | Session 1: preflight, S0, baseline, seed, S1, morning Run A, S2-morning | |
| 6 | Session 2: same `IT01_RUN_DIR`; evening Run A, S2 | |
| 7 | Run B → human approval → Run C → S3 (C-W1 checked) | |
| 8 | Run D → S4; Run E → S5 | |
| 9 | Run F (Suryodaya) → S6×2; back to Keystone | |
| 10 | verify → cleanup (agent writes included) → S7 → post-cleanup canonical → announce done | |
| 11 | Results recorded (design doc §9.3, `docs/integration_testing_pipeline_run3.md`, implementation plan) | |
| 12 | If green: Suryodaya UI check (Mon 2026-10-12), then merge `it01-run3-fixes` into `main` and push | |
