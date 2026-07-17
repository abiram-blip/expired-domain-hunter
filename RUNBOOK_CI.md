# Expired Domain Hunter — CI (GitHub Actions) runbook

This is the unattended variant of `RUNBOOK.md`, run by the daily GitHub Actions
workflow (`.github/workflows/daily-hunt.yml`) via a headless `claude -p` invocation.
Same pipeline, same hard gates, same tiering/name rules as `RUNBOOK.md` — the ONLY
difference is how the 3 browser-dependent stages are driven.

**Read `RUNBOOK.md` first for the full 13-step spec, tier table, and name-grading
rules (VERTICAL_BAD/TOKENS_GOOD/TRADEMARK_BAD/etc. in `hunt.py`, plus the taste-layer
patterns in memory) — everything there still applies.** This file only overrides the
mechanics of steps 1 (precheck), 3 (harvest), 7 (Spamhaus), and 9 (URIBL).

## What's different in CI

- **No `browser-harness`.** It's an interactive tool for an agent driving a live
  browser session conversationally — not installed in this environment. Instead use
  the deterministic `ci_browser.py` script (Playwright-based, already validated):
  - Harvest: `python3 ci_browser.py harvest run/$EDH_RUN_DATE/plan.json run/$EDH_RUN_DATE/`
    (writes `harvest_<tag>.json` files directly, same schema `merge-harvest` expects).
    Exit code 2 means the expireddomains.net session expired (login wall hit) — STOP,
    do not guess credentials, this needs a human to re-run `extract_cookies.py`
    against the Mac's live dedicated Chrome and push a fresh
    `expireddomains_cookies.json` to the state repo. Post this exact situation to
    Slack and stop the run cleanly (partial harvest results already on disk are fine
    to leave for next run's carryover).
  - Spamhaus: `python3 ci_browser.py spamhaus <domains.txt> run/$EDH_RUN_DATE/spamhaus.json`
  - URIBL: `python3 ci_browser.py uribl <domains.txt> run/$EDH_RUN_DATE/uribl.json`
  - All three log into expireddomains.net (harvest only) via a saved cookie, not a
    persistent browser profile — see `ci_browser.py`'s module docstring for why (Chrome's
    cookie encryption is machine-specific; a portable plaintext-cookie-injection
    approach is used instead).
- **Chromium runs HEADED under a virtual display (`xvfb-run`), not headless.**
  Confirmed 2026-07-17: check.spamhaus.org's Cloudflare challenge hard-blocks
  Playwright's headless mode outright, even with anti-detection flags. The workflow
  already wraps every `ci_browser.py` call in `xvfb-run` — if invoking it yourself
  (e.g. resuming manually), do the same: `xvfb-run -a python3 ci_browser.py ...`.
- **Precheck** = confirm `state/expireddomains_cookies.json` exists and is non-empty
  (copied into place by the workflow before this step) rather than checking a live
  browser tab's title. The real login check happens naturally on the first harvest
  call (exit code 2 = expired, see above).
- **State lives in a separate private repo** (`abiram-blip/expired-domain-hunter-state`),
  checked out into `state/` and copied into place (`ledger.json`, `run/`) before the
  run, copied back after (`if: always()` in the workflow) — this is what makes
  `ledger.json`/`run/YYYY-MM-DD/` persist across the otherwise-ephemeral runner.
- **Notifications** go to Slack only (no desktop, no `osascript`) — the workflow
  handles this at the shell level for hard failures; for anything you detect mid-run
  (shortfall, session expiry, ambiguous delivery), post directly:
  `curl -s -X POST -H 'Content-type: application/json' --data '{"text":"..."}' "$SLACK_WEBHOOK_URL"`

## Everything else

Identical to `RUNBOOK.md`: `hunt.py`'s subcommands (`plan-harvest`, `merge-harvest`,
`prescore`, `blocklist`, `vt`, `archive`, `tier`, `append`, `slack-post`, `state`,
`status`, `carryover`, `commit`), the human-taste name-curation pass (this is still
YOUR judgment call, not skipped — see [[feedback-domain-name-grading]] memory for the
full accept/reject calibration), the shortfall policy, and the delivery guard are all
unchanged and run exactly as documented there.
