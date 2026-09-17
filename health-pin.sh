#!/usr/bin/env bash
# Pinned-node health check + self-healing.
#
# The node pinned for OpenAI traffic can drift to a Hong Kong exit over time
# (observed: JP-labelled nodes drifting to HK). Nothing detects that today.
# This script runs hourly, measures the exit country of the *pinned* node
# (ip-api.com is routed through the OpenAI group on purpose), and if it has
# drifted to HK it picks the fastest known-good node from the last chatgpt
# probe and re-pins. Codex thus heals itself.
#
# Log (only on action): /home/yang/mihomo-setup/pin-health.log
set -uo pipefail
API=http://127.0.0.1:9090
PROXY=http://127.0.0.1:7890
GROUP="%F0%9F%9A%80%20%E8%8A%82%E7%82%B9%E9%80%89%E6%8B%A9"   # 🚀 节点选择
LOG=/home/yang/mihomo-setup/pin-health.log
CAND_FILE=/home/yang/mihomo-setup/probe_result.txt

curl -s --max-time 5 "$API/version" >/dev/null 2>&1 || exit 0

now=$(curl -s --max-time 5 "$API/proxies/$GROUP" \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('now',''))" 2>/dev/null)
[ -z "$now" ] && exit 0

exit_country() {
    curl -s --max-time 12 -x "$PROXY" http://ip-api.com/json \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('countryCode',''))" 2>/dev/null
}

set_pin() {
    curl -s -X PUT "$API/proxies/$GROUP" -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys; print(json.dumps({'name': sys.argv[1]}))" "$1")" \
      -o /dev/null -w "%{http_code}"
}

cc=$(exit_country)
if [ "$cc" != "HK" ] && [ -n "$cc" ]; then
    exit 0          # healthy, stay silent
fi

{
    echo "[$(date -u '+%F %T UTC')] pinned node '$now' exit=${cc:-UNREACHABLE} - re-pinning"
    mapfile -t cands < <(awk -F' \\| ' '$3 ~ /OK/ {print $1}' "$CAND_FILE" 2>/dev/null | head -8)
    for c in "${cands[@]}"; do
        [ "$c" = "$now" ] && continue
        code=$(set_pin "$c")
        [ "$code" != "204" ] && continue      # node gone from pool, try next
        sleep 1
        cc2=$(exit_country)
        echo "[$(date -u '+%F %T UTC')]   tried '$c' -> exit=${cc2:-UNREACHABLE}"
        case "$cc2" in
            HK|"") continue ;;
            *) echo "[$(date -u '+%F %T UTC')]   re-pinned to '$c'"; break ;;
        esac
    done
} >>"$LOG" 2>&1
