#!/bin/bash
# Daily trigger for the cloud VM, fired by cron at 07:30 (primary) and 07:55 (failsafe).
# Replaces run_hunt.sh (the Mac/launchd version, kept as a documented manual fallback —
# see the "Zero-Cost Cloud Migration" plan). Differences from the Mac version:
#   - cron instead of launchd; no macOS-only osascript notifications (curl -> Slack instead)
#   - config.json is rendered from config.template.json + .env at the start of every run,
#     never committed with real secrets
#   - Chrome is a self-hosted headless instance on THIS VM (still localhost:9223 — the
#     dedicated-Chrome-on-a-different-port pattern is unchanged, just relocated)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/run.log"
TODAY=$(date +%F)
STATE="$DIR/run/$TODAY/state.json"
MARKER="$DIR/run/$TODAY/.autostart_attempted"
CLAUDE="$HOME/.local/bin/claude"

# Linux cron gives an even more minimal PATH than macOS launchd — same fix, Linux paths.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export BU_NAME=domainhunt
export BU_CDP_URL=http://127.0.0.1:9223

# Load secrets (VT_API_KEY, SHEET_WEBAPP_URL, SHEET_WEBAPP_TOKEN, SLACK_WEBHOOK_URL,
# ANTHROPIC_API_KEY, HEALTHCHECK_URL) from .env — never committed, chmod 600.
set -a
# shellcheck disable=SC1091
source "$DIR/.env"
set +a

slack_alert() {
  # Best-effort ops alert. Distinct from the daily delivery post — prefixed so it's
  # obviously not a normal shortlist message.
  [ -n "$SLACK_WEBHOOK_URL" ] || return 0
  curl -s -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"⚠️ ALERT: $1\"}" "$SLACK_WEBHOOK_URL" >/dev/null 2>>"$LOG"
}

heartbeat() {
  # Dead-man's-switch ping (healthchecks.io / Cronitor free tier) — if this never
  # fires, the external service alerts the user directly that the run never completed.
  [ -n "$HEALTHCHECK_URL" ] || return 0
  curl -fsS -m 10 "$HEALTHCHECK_URL" >/dev/null 2>>"$LOG" || true
}

echo "$(date '+%Y-%m-%d %H:%M') fired" >> "$LOG"

cd "$DIR" || exit 1
if ! python3 render_config.py >>"$LOG" 2>&1; then
  slack_alert "render_config.py failed — check .env on the VM. Run did not start."
  exit 1
fi

# Run already started (or done) today -> stay silent.
[ -f "$STATE" ] && exit 0

# First fire of the day: try a headless agent run once.
if [ ! -f "$MARKER" ] && [ -x "$CLAUDE" ]; then
  mkdir -p "$DIR/run/$TODAY"
  touch "$MARKER"
  echo "$(date '+%H:%M') launching headless claude" >> "$LOG"
  nohup "$CLAUDE" -p --model claude-sonnet-5 "Run the daily expired-domain hunt per $DIR/RUNBOOK.md. Start with 'python3 hunt.py status --brief' and the Chrome/login precheck; write state.json stages as you go; follow the resume protocol if a stage file already exists for today." \
    >> "$DIR/run/$TODAY/headless.log" 2>&1 && heartbeat &
  disown
  exit 0
fi

# Failsafe fire: run never wrote state.json -> alert via Slack (no desktop to notify).
BRIEF=$(python3 hunt.py status --brief 2>/dev/null)
slack_alert "Daily hunt did not start on the VM. Last run: $BRIEF"
