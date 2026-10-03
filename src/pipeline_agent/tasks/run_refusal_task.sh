#!/usr/bin/env bash
# Runs mandatory_refusal_task.py (run, then verify) - but only once the caller
# has exported AGENTSWITCH_BASE_URL and AGENTSWITCH_TOKEN and the token works.
#
#   export AGENTSWITCH_BASE_URL=https://agentswitch.theschoolofai.in   # Suryodaya (India)
#   export AGENTSWITCH_TOKEN=$(curl -s -X POST "$AGENTSWITCH_BASE_URL/api/auth/login" \
#     -H 'Content-Type: application/json' \
#     -d '{"email":"<team email>","password":"<team password>"}' \
#     | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
#
#   src/pipeline_agent/tasks/run_refusal_task.sh [artifacts_dir]
#
# Exit code is the verify exit code: 0 pass, 1 fail, 3 blocked/missing.
# Exits 4 if the environment is not set up.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
task="$here/mandatory_refusal_task.py"

usage() {
    echo "ERROR: $1" >&2
    sed -n '5,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 4
}

[[ -n "${AGENTSWITCH_BASE_URL:-}" ]] || usage "AGENTSWITCH_BASE_URL is not set."
[[ -n "${AGENTSWITCH_TOKEN:-}" ]]    || usage "AGENTSWITCH_TOKEN is not set (did the login curl fail?)."
[[ "$AGENTSWITCH_BASE_URL" =~ ^https?:// ]] || usage "AGENTSWITCH_BASE_URL must start with http(s)://"
AGENTSWITCH_BASE_URL="${AGENTSWITCH_BASE_URL%/}"
export AGENTSWITCH_BASE_URL AGENTSWITCH_TOKEN

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -H "Authorization: Bearer $AGENTSWITCH_TOKEN" "$AGENTSWITCH_BASE_URL/api/auth/me")
[[ "$code" == "200" ]] || usage "token rejected by $AGENTSWITCH_BASE_URL/api/auth/me (HTTP $code); log in again."

artifacts="${1:-$repo/runs/mandatory_refusal_$(date +%Y%m%d_%H%M%S)}"
py=(uv run --no-project --with requests python)

echo "[env ok] $AGENTSWITCH_BASE_URL  artifacts=$artifacts"
"${py[@]}" "$task" run --artifacts "$artifacts" || { echo "runner crashed" >&2; exit 2; }
"${py[@]}" "$task" verify --artifacts "$artifacts"
