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
#   tasks/run_refusal_task.sh [artifacts_dir]
#
# Before the task it sends the platform agent one tool-free canary message. If
# the platform's own LLM provider is failing (e.g. the Fireworks 401), A1 can
# only ever fail, so it warns and asks before going on. Teams cannot fix that
# key themselves (/api/agent/providers/status reports can_configure=false).
# Non-interactive runs abort unless AGENTSWITCH_ACK_LLM_DOWN=1 is exported.
#
# Exit code is the verify exit code: 0 pass, 1 fail, 3 blocked/missing.
# Exits 4 if the environment is not set up, 5 if aborted at the LLM check.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
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

# --- platform LLM canary ----------------------------------------------------
llm=$(curl -s --max-time 90 -X POST "$AGENTSWITCH_BASE_URL/api/agent/chat" \
    -H "Authorization: Bearer $AGENTSWITCH_TOKEN" -H 'Content-Type: application/json' \
    -d '{"message":"Reply with the single word OK. Do not call any tools."}' \
  | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("down|unknown|no JSON from /api/agent/chat"); sys.exit()
err = d.get("error") or ""
if err or str(d.get("content", "")).startswith("LLM error"):
    print("down|%s/%s|%s" % (d.get("provider", "?"), d.get("error_kind", "?"),
                             (err or d.get("content", ""))[:200].replace("\n", " ")))
else:
    print("up|%s|ok" % d.get("provider", "?"))
')
IFS='|' read -r llm_state llm_kind llm_detail <<< "$llm"

if [[ "$llm_state" == "up" ]]; then
    echo "[llm ok]  platform agent provider: $llm_kind"
else
    cat >&2 <<EOF

  ################################  WARNING  ################################
  The platform agent's own LLM provider is failing:
      provider/kind : $llm_kind
      error         : $llm_detail

  This is AgentSwitch's server-side key (e.g. the Fireworks 401), not your
  token. Teams cannot set it: /api/agent/providers/status -> can_configure=false.
  While it is down the agent never reaches a refusal decision, so A1 WILL FAIL
  (A2-A4 still run). Ask the platform team to fix the provider key, then re-run.
  ###########################################################################

EOF
    if [[ "${AGENTSWITCH_ACK_LLM_DOWN:-}" == "1" ]]; then
        echo "AGENTSWITCH_ACK_LLM_DOWN=1 - continuing anyway." >&2
    elif [[ -t 0 ]]; then
        read -r -p "Run the task anyway? [y/N] " ans
        [[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted - fix the platform provider first." >&2; exit 5; }
    else
        echo "Aborted (non-interactive). Export AGENTSWITCH_ACK_LLM_DOWN=1 to run anyway." >&2
        exit 5
    fi
fi

artifacts="${1:-$repo/runs/mandatory_refusal_$(date +%Y%m%d_%H%M%S)}"
py=(uv run --no-project --with requests python)

echo "[env ok] $AGENTSWITCH_BASE_URL  artifacts=$artifacts"
"${py[@]}" "$task" run --artifacts "$artifacts" || { echo "runner crashed" >&2; exit 2; }
"${py[@]}" "$task" verify --artifacts "$artifacts"
