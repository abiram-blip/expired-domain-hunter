#!/bin/bash
# Daily trigger, fired by launchd at 07:30 (primary) and 07:55 (failsafe).
# 07:30: start a headless Claude Code run of the daily hunt (needs Chrome running + logged in).
# 07:55: if today's run still hasn't started (no state.json), notify the user to start manually.
DIR="/Users/test/expired-domain-hunter"
LOG="$DIR/run.log"
TODAY=$(date +%F)
STATE="$DIR/run/$TODAY/state.json"
MARKER="$DIR/run/$TODAY/.autostart_attempted"
CLAUDE="$HOME/.local/bin/claude"
# 2026-07-14 fix: launchd gives its jobs a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin) with no
# ~/.local/bin — the headless claude process itself launches fine (called by full path below),
# but its own Bash tool calls inherit this same PATH, so bare `browser-harness` was "command not
# found". Export a full PATH before launching so everything the headless session spawns inherits it.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# 2026-07-15 fix: browser-harness now targets a DEDICATED Chrome instance (its own profile,
# port 9223 — see com.user.domainhunterchrome.plist, kept running persistently via launchd),
# not the user's main day-to-day Chrome. Exporting these makes every browser-harness call the
# headless session spawns connect there automatically, without needing to guess which window
# to grab. See RUNBOOK.md "Dedicated browser" section.
export BU_NAME=domainhunt
export BU_CDP_URL=http://127.0.0.1:9223

echo "$(date '+%Y-%m-%d %H:%M') fired" >> "$LOG"

# Run already started (or done) today -> stay silent.
[ -f "$STATE" ] && exit 0

# First fire of the day: try a headless agent run once.
if [ ! -f "$MARKER" ] && [ -x "$CLAUDE" ]; then
  mkdir -p "$DIR/run/$TODAY"
  touch "$MARKER"
  echo "$(date '+%H:%M') launching headless claude" >> "$LOG"
  cd "$DIR" || exit 1  # 2026-07-13 fix: headless session must start in the project dir
  nohup "$CLAUDE" -p --model claude-sonnet-5 "Run the daily expired-domain hunt per /Users/test/expired-domain-hunter/RUNBOOK.md. Start with 'python3 hunt.py status --brief' and the Chrome/login precheck; write state.json stages as you go; follow the resume protocol if a stage file already exists for today." \
    >> "$DIR/run/$TODAY/headless.log" 2>&1 &
  disown  # belt-and-suspenders: survive even if AbandonProcessGroup isn't honored
  exit 0
fi

# Failsafe fire: run never wrote state.json -> tell the user.
BRIEF=$(cd "$DIR" && python3 hunt.py status --brief 2>/dev/null)
osascript -e "display notification \"Daily hunt did not start. Open Chrome (logged into expireddomains) and tell Claude: run the domain hunt. Last run: $BRIEF\" with title \"Domain Hunter\" sound name \"Glass\"" 2>>"$LOG"
