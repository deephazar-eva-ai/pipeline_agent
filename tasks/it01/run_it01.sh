#!/usr/bin/env bash
# Explicit IT-01 phase runner. It never seeds or cleans up without --apply.
set -euo pipefail

run_dir=${IT01_RUN_DIR:?Set IT01_RUN_DIR=runs/IT-01/<unique-run-id>}
phase=${1:?Choose: snapshot|seed|run-a|run-b|approve|run-c|run-d|run-e|run-f|verify|cleanup}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"

agent() {
  local output=$1; shift
  OUTPUT_DIR="$run_dir/$output" PIPELINE_INCLUDE_TEST_DATA=1 PYTHONPATH=src \
    python3 -m pipeline_agent.runner "$@"
}

case "$phase" in
  snapshot) PYTHONPATH=src python3 tasks/it01/it01.py --run-dir "$run_dir" snapshot "${2:?S0..S7}" ;;
  seed) PYTHONPATH=src python3 tasks/it01/it01.py --run-dir "$run_dir" --apply seed ;;
  run-a) MODEL_NAME='openrouter:anthropic/claude-sonnet-4.6' agent 02-run-a --task loop --mode live --max-steps 25 --request 'Read-only: do not create, update or delete anything. Which of our open deals are rotting, meaning the customer has had no completed call, meeting or email for more than the company rot threshold? Which customers have not been called in the last 30 days? What is the next action on each rotting deal? Also list any unpaid invoices for each rotting customer.' ;;
  run-b) agent 03-run-b --task canonical --mode live --exec-mode propose ;;
  approve) PYTHONPATH=src python3 tasks/it01/it01.py --run-dir "$run_dir" approve --run-b "${2:?Run B artifact path}" --approver "${3:?approver}" --deal-key D3 --deal-key D8 --deal-key D9a --deal-key D10 --deal-key D14 --deal-key D15 ;;
  run-c) PYTHONPATH=src python3 tasks/it01/controller.py --run-dir "$run_dir" run-c ;;
  run-d) PYTHONPATH=src python3 tasks/it01/controller.py --run-dir "$run_dir" run-d ;;
  run-e) PYTHONPATH=src python3 tasks/it01/controller.py --run-dir "$run_dir" run-e ;;
  run-f) PYTHONPATH=src python3 tasks/it01/controller.py --run-dir "$run_dir" run-f ;;
  verify) python3 tasks/it01/verify.py --run-dir "$run_dir" ;;
  cleanup) PYTHONPATH=src python3 tasks/it01/it01.py --run-dir "$run_dir" --apply cleanup ;;
  *) exit 2 ;;
esac
