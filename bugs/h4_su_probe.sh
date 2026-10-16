#!/usr/bin/env bash
# H4 / candidate L on Suryodaya: does "A completed activity cannot be dated in
# the future" use the UTC date instead of the company's (IST) day?
# Run between 00:00 and 05:30 IST, after exporting AS and TOKEN (Suryodaya login).
# Creates three tagged Activities on a team07 test party, prints the results,
# then closes them as cancelled tasks. Files nothing.
set -euo pipefail
: "${AS:?export AS (Suryodaya base URL)}" "${TOKEN:?export TOKEN (Suryodaya login token)}"

H="Authorization: Bearer $TOKEN"; J='Content-Type: application/json'
TAG='[team07-test BH-20261013] H4-su'
now_ist=$(TZ=Asia/Kolkata date '+%H%M')
if [ "$now_ist" -ge 0530 ]; then echo "outside the 00:00-05:30 IST window ($now_ist)"; exit 1; fi
TODAY=$(TZ=Asia/Kolkata date +%F)
YDAY=$(TZ=Asia/Kolkata date -d yesterday +%F)
echo "IST $(TZ=Asia/Kolkata date '+%F %T') | UTC $(date -u '+%F %T') | today(IST)=$TODAY"

# A team07 test party; create one if none exists.
PARTY=$(curl -s -H "$H" "$AS/api/Party?search=team07-test&limit=50" | python3 -c '
import sys,json; d=json.load(sys.stdin); r=d if isinstance(d,list) else d.get("data",d.get("items",[]))
print(next((p["id"] for p in r if "[team07-test" in str(p.get("name"))), ""))')
if [ -z "$PARTY" ]; then
  PARTY=$(curl -s -X POST -H "$H" -H "$J" "$AS/api/Party" \
    -d "{\"name\":\"[team07-test BH-20261013] H4 party\",\"roles\":[{\"role\":\"customer\"}]}" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("data",d)["id"])')
  echo "created party $PARTY"
fi
echo "party $PARTY"

IDS=()
probe() {  # label due done
  local out code body id
  out=$(curl -s -w '\n%{http_code}' -X POST -H "$H" -H "$J" "$AS/api/Activity" \
    -d "{\"subject\":\"$TAG $1\",\"type\":\"call\",\"party_id\":\"$PARTY\",\"due_date\":\"$2\",\"done\":$3}")
  code=${out##*$'\n'}; body=${out%$'\n'*}
  echo "$1 due=$2 done=$3 -> HTTP $code $(echo "$body" | head -c 300)"
  id=$(echo "$body" | python3 -c 'import sys,json
try: d=json.load(sys.stdin); print(d.get("data",d).get("id",""))
except Exception: print("")')
  [ -n "$id" ] && IDS+=("$id") || true
}
probe "P1 done, dated IST today" "$TODAY" true     # 422 "future" = bug
probe "P2 done, dated IST yesterday" "$YDAY" true  # control
probe "P3 open, due IST today" "$TODAY" false      # control

for id in "${IDS[@]}"; do
  curl -s -o /dev/null -w "cleanup $id -> %{http_code}\n" -X PUT -H "$H" -H "$J" "$AS/api/Activity/$id" \
    -d "{\"done\":true,\"type\":\"task\",\"outcome\":\"Cancelled\",\"due_date\":\"$YDAY\",\"description\":\"$TAG probe, cleaned\"}"
done
