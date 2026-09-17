#!/usr/bin/env bash
# Opportunistic node maintenance for an intermittently-powered VM.
#
# Installed in yang's crontab to run hourly. It acts ONLY when both:
#   1. the HK blacklist is older than 7 days, AND
#   2. nobody is using ChatGPT/Codex through the proxy right now (<=3 total
#      connections and zero chatgpt/openai connections)
# so the rescan (which bounces all connections for ~5 min) never interrupts
# work. On a VM that is off most of the time this beats a fixed schedule:
# whenever it happens to be powered on and idle, maintenance catches up.
#
# Log: /home/yang/mihomo-setup/rescan.log
set -uo pipefail
cd /home/yang/mihomo-setup
LOG=/home/yang/mihomo-setup/rescan.log
exec >>"$LOG" 2>&1
log() { echo "[$(date -u '+%F %T UTC')] $*"; }

AGE_LIMIT=$((7*24*3600))

# 0. mihomo must be up
curl -s --max-time 5 http://127.0.0.1:9090/version >/dev/null 2>&1 || exit 0

# 1. blacklist age gate (silent while fresh)
mtime=$(stat -c %Y hk-endpoints.txt 2>/dev/null || echo 0)
age=$(( $(date +%s) - mtime ))
[ "$age" -lt "$AGE_LIMIT" ] && exit 0

# 2. idle gate: no chatgpt/openai traffic, few connections overall
busy=$(curl -s --max-time 5 http://127.0.0.1:9090/connections 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
conns=d.get('connections') or []
ai=[c for c in conns
    if any(k in (c.get('metadata',{}).get('host') or '')
           for k in ('chatgpt.com','openai.com'))]
print(len(conns), len(ai))
" 2>/dev/null) || busy="999 999"
total=${busy%% *}; ai=${busy##* }
if [ "$ai" != "0" ] || [ "${total:-999}" -gt 3 ]; then
    log "busy (conns=$total ai=$ai), skip rescan"
    exit 0
fi

log "idle + blacklist $((age/86400))d old -> rescan start"
curl -s --max-time 10 http://127.0.0.1:9090/proxies \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['proxies']; print('\n'.join(k for k,v in d.items() if v.get('type')=='Vless'))" \
  > /tmp/nodes.txt || { log "ERROR: node list fetch failed"; exit 1; }
log "scanning $(wc -l < /tmp/nodes.txt) nodes"
python3 scan_nodes.py || { log "ERROR: scan failed"; exit 1; }
sudo -n /usr/local/bin/proxy update || { log "ERROR: proxy update failed"; exit 1; }
log "rescan finished"
