#!/bin/bash
# Tier 1 of the autonomy hardening (see plan find-a-way-to-glistening-grove).
# Fired by launchd ~06:40 IST, BEFORE the CI hunt (07:30). Keeps CI's expireddomains
# session cookie fresh so the login-gated harvest never hits a stale-cookie login wall.
#
# Flow: ensure the dedicated Chrome (:9223) is up -> validate it is still logged into
# expireddomains.net -> if YES, extract a fresh cookie and push it (with a _captured_at
# meta sidecar) to the private state repo CI reads -> if NO, alert LOUD (Slack + desktop)
# because a real logout needs a manual MFA re-login (the one irreducible manual step).
# Every run appends one line to state/session_health.log so we learn the session TTL.
set -uo pipefail

DIR="/Users/test/expired-domain-hunter"
STATE_DIR="$HOME/edh-state"
STATE_SLUG="abiram-blip/expired-domain-hunter-state"
LOG="$DIR/refresh_cookie.log"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export BU_NAME=domainhunt
export BU_CDP_URL=http://127.0.0.1:9223

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

SLACK=$(python3 -c "import json;print(json.load(open('$DIR/config.json')).get('slack_webhook_url',''))" 2>/dev/null)
alert() {  # $1 = message
  log "ALERT: $1"
  [ -n "$SLACK" ] && curl -s -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"⚠️ $1\"}" "$SLACK" >/dev/null 2>&1
  osascript -e "display notification \"$1\" with title \"Domain Hunter — cookie refresh\" sound name \"Basso\"" 2>/dev/null
}

health_line() {  # $1 = valid|invalid  $2 = captured_at-or-dash
  [ -d "$STATE_DIR/.git" ] || return 0
  ( cd "$STATE_DIR" && git fetch -q origin main && git reset -q --hard origin/main
    echo "$(date '+%F %T') valid=$1 captured_at=$2" >> session_health.log
    git add session_health.log
    git -c user.email=cookie-bot@local -c user.name=cookie-bot commit -q -m "session-health: $(date +%F) $1" 2>/dev/null
    git push -q origin main 2>/dev/null || { git fetch -q origin main && git rebase -q origin/main && git push -q origin main; }
  )
}

log "=== refresh start ==="

# 1) Ensure dedicated Chrome is up.
if ! lsof -i :9223 -sTCP:LISTEN >/dev/null 2>&1; then
  log "Chrome :9223 down -> kickstart"
  launchctl kickstart "gui/$(id -u)/com.user.domainhunterchrome" 2>>"$LOG"
  sleep 8
fi
if ! lsof -i :9223 -sTCP:LISTEN >/dev/null 2>&1; then
  alert "dedicated Chrome (:9223) is DOWN and would not restart — domain hunt cannot harvest."
  health_line invalid -
  exit 1
fi

# 2) Validate login + (if valid) extract fresh cookie, inside one browser-harness session.
OUT=$(browser-harness <<'PY' 2>/dev/null
import json, time
tabs = list_tabs()
ed = next((t for t in tabs if "expireddomains.net" in (t.get("url","") or "")), None)
status = "INVALID"
if ed:
    switch_tab(ed["targetId"])
    js("location.href='https://member.expireddomains.net/domains/godaddyexpired/?fwhoisage=2008&fpriceto=1&o=endtime&r=a'")
    wait_for_load(); time.sleep(1.5)
    chk = json.loads(js(r"""(()=>JSON.stringify({
      logout:/log\s?out/i.test(document.body?document.body.innerText:'')||!!document.querySelector('a[href*=logout]'),
      pw:!!document.querySelector('input[type=password]')}))()"""))
    if chk["logout"] and not chk["pw"]:
        exec(open("extract_cookies.py").read())   # writes /tmp cookie + meta
        status = "VALID"
print("COOKIE_STATUS=" + status)
PY
)
log "browser-harness: $(echo "$OUT" | tr '\n' ' ')"
STATUS=$(echo "$OUT" | sed -n 's/.*COOKIE_STATUS=\([A-Z]*\).*/\1/p' | tail -1)

# 3) Act on the result.
if [ "$STATUS" = "VALID" ] && [ -s /tmp/expireddomains_cookies.json ]; then
  CAP=$(python3 -c "import json;print(json.load(open('/tmp/expireddomains_cookies.meta.json'))['_captured_at'])" 2>/dev/null)
  [ -d "$STATE_DIR/.git" ] || gh repo clone "$STATE_SLUG" "$STATE_DIR" -- -q
  (
    cd "$STATE_DIR" || exit 1
    git fetch -q origin main && git reset -q --hard origin/main
    cp /tmp/expireddomains_cookies.json      expireddomains_cookies.json
    cp /tmp/expireddomains_cookies.meta.json expireddomains_cookies.meta.json
    echo "$(date '+%F %T') valid=true captured_at=$CAP" >> session_health.log
    git add expireddomains_cookies.json expireddomains_cookies.meta.json session_health.log
    if git -c user.email=cookie-bot@local -c user.name=cookie-bot commit -q -m "cookie refresh: $(date +%F)"; then
      git push -q origin main 2>>"$LOG" || { git fetch -q origin main && git rebase -q origin/main && git push -q origin main 2>>"$LOG"; }
    fi
  )
  log "pushed fresh cookie (captured_at=$CAP)"
  echo "OK: fresh cookie pushed (captured_at=$CAP)"
else
  alert "dedicated Chrome is LOGGED OUT of expireddomains.net — re-login manually (needs MFA). CI harvest will fail until fixed."
  health_line invalid -
  echo "LOGGED OUT — alerted"
  exit 2
fi
log "=== refresh done ==="
