# IT-01 live-test package

This directory operates the live **Hostile Monday** scenario in
[`docs/integration_testing_pipeline.md`](../../docs/integration_testing_pipeline.md).
It creates a tagged fixture book, runs the planned phases, coordinates
concurrent adversary changes, preserves snapshots, and closes the fixtures.

Creating or inspecting these files does not contact AgentSwitch. The commands
below do, so use them only in the announced Keystone test window.

## Files

| File | Purpose |
|---|---|
| `scenario.py` | Declarative parties, deals, contacts, and adversarial rows. D11 is intentionally absent: every live fixture must be closable through a supported stage transition. |
| `it01.py` | Direct snapshot, seed, adversary, approval, and cleanup CLI. |
| `controller.py` | Runs C and D with their concurrent adversaries; also runs E and F. |
| `run_it01.sh` | Operator-facing phase dispatcher. |
| `verify.py` | Writes `verification.json` from direct snapshot comparisons. |

## Prerequisites

Use a fresh run directory and export the standard MCP settings before invoking
any command:

```bash
export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in/api/mcp'
export AGENTSWITCH_MCP_TOKEN='...'
export AGENTSWITCH_TENANT=keystone
export IT01_RUN_DIR='runs/IT-01/<unique-run-id>'
```

`it01.py` reads these exported names directly; it does **not** map
`AGENTSWITCH_MCP_KEYSTONE_URL` or `AGENTSWITCH_MCP_KEYSTONE_TOKEN` from a
local `.env` file. The agent runner loads its own `.env`, but the snapshot,
seed, adversary, and cleanup commands need the standard variables above in the
shell environment.

Runs A and D additionally need working model credentials for the configured
OpenRouter models. Run F needs the Suryodaya MCP URL and token plus
`AGENTSWITCH_TENANT=suryodaya`.

## Safety model

- `snapshot` is read-only.
- `seed`, `adversary`, and `cleanup` are live mutations. The dispatcher adds
  `--apply` only for those named phase commands.
- Before seeding, `it01.py` confirms that each fixture stage has an exposed
  `Deal.mark_lost.<stage>.closed_lost` transition. It refuses to seed when a
  future cleanup is not supported.
- `fixtures.json` is updated after every created record. It is the authority
  for adversary actions and cleanup; do not edit it by hand.
- Keystone rejects an Activity due date earlier than its creation. Historical
  contacts therefore use the `logged_by=pipeline_agent actual_date=...`
  marker. The D8 injection task is open but due today; it tests instruction
  handling, not an overdue-task state.
- Cleanup cancels recorded Activities and uses the matching stage transition
  for recorded deals. It never deletes records.

## Phase commands

Run all commands from the repository root (`pipeline_agent/`):

```bash
tasks/it01/run_it01.sh snapshot S0
tasks/it01/run_it01.sh seed
tasks/it01/run_it01.sh snapshot S1
```

Run A is read-only. Run B creates proposals only:

```bash
tasks/it01/run_it01.sh run-a
tasks/it01/run_it01.sh snapshot S2
tasks/it01/run_it01.sh run-b
```

After a person reviews Run B, record their approval. The wrapper supplies the
only permitted deal keys: D3, D8, D9a, D10, D14, and D15.

```bash
tasks/it01/run_it01.sh approve \
  "$IT01_RUN_DIR/03-run-b/<run-b-artifact>" 'approver-name'
```

Then run the write phases and snapshots:

```bash
tasks/it01/run_it01.sh run-c
tasks/it01/run_it01.sh snapshot S3
tasks/it01/run_it01.sh run-d
tasks/it01/run_it01.sh snapshot S4
tasks/it01/run_it01.sh run-e
tasks/it01/run_it01.sh snapshot S5
```

Run C reads the runner's printed pre-write pause and adds D10's party-only
Activity or closes D15 at the matching pause. Run D watches `events.jsonl` and
closes D16 after the first `contact_status` event, falling back to 30 seconds
after launch. Keep the laptop awake while either controller runs.

For Run F, replace the connection variables with the Suryodaya credential
first. The controller refuses to run if the tenant is not explicitly set to
`suryodaya`.

```bash
export AGENTSWITCH_MCP_URL='...Suryodaya MCP URL...'
export AGENTSWITCH_MCP_TOKEN='...'
export AGENTSWITCH_TENANT=suryodaya
tasks/it01/run_it01.sh run-f
tasks/it01/run_it01.sh snapshot S6-suryodaya
```

Then return to Keystone credentials, capture the other tenant's S6 view, and
perform verification and cleanup:

```bash
export AGENTSWITCH_MCP_URL='https://class.agentswitch.theschoolofai.in/api/mcp'
export AGENTSWITCH_MCP_TOKEN='...'
export AGENTSWITCH_TENANT=keystone
tasks/it01/run_it01.sh snapshot S6-keystone
tasks/it01/run_it01.sh verify
tasks/it01/run_it01.sh cleanup
tasks/it01/run_it01.sh snapshot S7
```

`verify.py` currently automates the direct Activity-diff checks implemented in
the script and records the remaining result-schema and cross-tenant checks in
`verification.json` as manual review items. A green script exit is therefore
not a substitute for the full IT-01 check table in the design document.

## Evidence layout

All generated evidence belongs beneath the chosen run directory:

```text
runs/IT-01/<run-id>/
  S0/ ... S7/              direct database snapshots
  fixtures.json            fixture IDs and adversary/cleanup state
  approval.json            recorded human consent for Run C
  02-run-a/ ... 06-run-f/  runner artifacts and controller logs
  verification.json        direct snapshot-verifier output
```

Use a new `<run-id>` for every attempt. Keep interrupted attempts and their
partial snapshots; never overwrite their fixture ledger.
