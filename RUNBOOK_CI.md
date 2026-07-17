# Expired Domain Hunter — CI (GitHub Actions) runbook

This is the unattended variant of `RUNBOOK.md`, run entirely by
`run_pipeline.py` inside the daily GitHub Actions workflow
(`.github/workflows/daily-hunt.yml`) — **no LLM agent loop drives this at all.**
Same pipeline, same hard gates, same tiering/name rules as `RUNBOOK.md`. The only
genuinely non-deterministic step (name-quality judgment) is a single OpenAI API call
(`name_judge.py`); everything else is plain sequenced Python.

**`RUNBOOK.md` is still the source of truth** for the tier table and the full
name-grading rule set (`VERTICAL_BAD`/`TOKENS_GOOD`/`TRADEMARK_BAD`/etc. in `hunt.py`,
plus [[feedback-domain-name-grading]] memory) — `run_pipeline.py` implements that exact
spec in code. If you change a rule in `hunt.py` or the taste calibration, update
`name_judge.py`'s `SYSTEM_PROMPT` to match (it's a from-scratch re-encoding of the same
rules for a model that can't read this repo's memory files).

## Why no `claude -p` / browser-harness here

- **`browser-harness` is an interactive tool** for an agent driving a live browser
  conversationally — not usable unattended, and not installed in this environment.
  `ci_browser.py` replaces it: a deterministic Playwright script covering the 3
  browser-dependent stages (harvest, Spamhaus, URIBL), callable exactly like any other
  `hunt.py` subcommand. See its module docstring for two hard-won fixes: (a) Chrome's
  cookie encryption is machine-specific, so session persistence uses a portable
  plaintext-cookie-JSON + `context.add_cookies()` instead of copying the raw profile;
  (b) check.spamhaus.org's Cloudflare challenge hard-blocks Playwright's headless mode
  outright, so the browser runs HEADED under a virtual display (`xvfb-run` — the
  workflow wraps `run_pipeline.py` in it, which covers every `ci_browser.py` call it
  spawns).
- **Unattended `claude -p` needs a metered `ANTHROPIC_API_KEY`** — a real recurring
  cost/account the user wanted to avoid if the sequencing itself doesn't need an LLM
  (it doesn't; it's 13 deterministic steps). The ONE place real judgment is
  irreplaceable — "does this domain name read as a credible company" — is isolated
  into a single `name_judge.py` call using an OpenAI key instead, which the user
  already had.

## How it actually runs

`run_pipeline.py` is the entire orchestrator: status/precheck → carryover →
`hunt.py plan-harvest` → `ci_browser.py harvest` (exit code 2 = session expired, see
below) → `hunt.py merge-harvest` → `hunt.py prescore` (carryover + fresh candidates) →
`name_judge.py` (the one LLM call — accept/reject verdict per candidate, same taste
rules as the interactive pipeline) → `hunt.py blocklist` → `ci_browser.py spamhaus` →
`ci_browser.py uribl` → `hunt.py archive` (retries transient errors up to twice, never
lets an errored check silently pass as clean) → `hunt.py vt` → `hunt.py tier` → rank +
build `shortlist.json` (delivery guard: <12h left excluded) → `hunt.py append` (on
non-zero exit, alerts Slack with the TSV location instead of guessing whether it
landed — exit 3 especially must never be blindly retried, risk of double-delivery) →
`hunt.py slack-post` → `hunt.py commit` (`--harvested` gets the taste-rejects, same
fix as the interactive pipeline's 2026-07-17 lesson: reject-on-name domains must be
marked `seen` too, or they resurface every day) → `hunt.py state --finish`.

Every stage still writes to `run/$EDH_RUN_DATE/` exactly as `RUNBOOK.md` describes —
resuming/carryover logic reads the same canonical filenames either way.

## Session expiry (the one thing that needs a human, occasionally)

`ci_browser.py harvest` exits 2 if expireddomains.net's login has expired. When that
happens: `run_pipeline.py` posts a Slack alert and stops cleanly (no guessed
credentials, no automated re-login attempt). To fix: run `extract_cookies.py` against
the Mac's live, already-logged-in dedicated Chrome (`browser-harness` heredoc, see that
script's own usage comment), then copy the resulting
`/tmp/expireddomains_cookies.json` into the **state repo**
(`abiram-blip/expired-domain-hunter-state`) and push. Next run picks it up
automatically — no code or workflow change needed.

## State persistence

`ledger.json` and `run/` live in a **separate private repo**
(`abiram-blip/expired-domain-hunter-state`) — checked out into `state/` at the start of
the workflow, copied into place, then copied back and committed (`if: always()`) at the
end. This is what makes state survive the otherwise-ephemeral GitHub Actions runner,
and keeps real harvested-domain data + the login session out of the public code repo.

## Notifications

Slack only (no desktop, no `osascript`) — `run_pipeline.py`'s `slack()` helper posts
directly via the webhook for every stop condition (precheck failure, session expiry,
shortfall, delivery ambiguity, crashes). The workflow's own final step posts a generic
failure alert too, as a catch-all for anything that crashes before `slack()` could run
(e.g. a Python import error).

## Known simplifications vs. the interactive pipeline

- No `URGENT <24h` sort-to-top flag for domains 12-24h from auction end (RUNBOOK.md's
  full delivery-guard nuance) — they're delivered normally, just not specially flagged.
  Worth adding to `run_pipeline.py` if it turns out to matter in practice.
- `name_judge.py`'s prompt is a best-effort re-encoding of
  [[feedback-domain-name-grading]]'s calibration, not a live read of that memory file
  (a model with no access to this project's memory can't read it) — if the interactive
  taste bar shifts, that prompt needs a manual update to stay in sync.
