# Expired Domain Hunter — runbook (v3, 2026-08-05)

Deliver **aged, clean, auction-live .com domains** to the Google Sheet + Slack #domain-hunt each
day, quality-ranked and ready to bid on. For cold-outreach mailboxes (root 301s to the client).
Bidding and payment are always the user's.

## Architecture: fully automated, no login, no browser, nothing on the Mac
Runs entirely in **GitHub Actions** (`.github/workflows/daily-hunt.yml`, cron `0 2 * * *` = 02:00
UTC / ~07:30 IST → `run_pipeline.py`). Every stage is **HTTP / DNS / API** — there is no
expireddomains login, no cookie, no dedicated Chrome, no Playwright. State (ledger, run history)
lives in the private repo `abiram-blip/expired-domain-hunter-state`, restored at the start and
pushed back at the end of each run.

History: the pipeline used to scrape expireddomains.net's member area through a logged-in browser,
which needed a cookie that kept expiring (MFA re-login, Mac uptime, session logout — recurring
outages). On 2026-08-05 harvest moved to GoDaddy's public feed and URIBL moved to DNS, removing the
last login and the last browser. See [[project-expired-domain-hunter]] #33-35.

## THREE MANDATORY RULES (never relax, checked every day)
1. **Auction-live only.** Only `auctionType == "Bid"` records from the feed (real bidding auctions
   with a countdown). `feed_harvest.py` enforces this; `tier()` also sets `tier=None` on any row
   with no `auction_ends_at`, excluding it from delivery.
2. **Abstract OR IT-based name only** — reject any named non-IT vertical (law, consulting,
   construction, recycling, staffing, accounting, real estate, roofing, HVAC, manufacturing, etc.).
   Enforced by `prescore` (`VERTICAL_BAD`/`TOKENS_GOOD`/`NEUTRAL_SUFFIX`) and, decisively, by
   `name_judge.py` (the OpenAI grader, ~94% accurate — see [[feedback-domain-name-grading]]).
3. **Price $1-$5 only** (`config.price_ceiling_usd = 5`).

## The bar (hard gates — never relaxed)
`.com`, letters-only host, ≤15 chars; auction-live (Rule 1); abstract/IT name (Rule 2); price ≤$5
(Rule 3); not adult (feed `isAdult`); 42-zone DNS blocklist all-green (includes `multi.uribl.com`
and `dbl.spamhaus.org`); VirusTotal 0 malicious; clean archive history (no adult/gambling/pharma/
parked-PBN); name grade A/B + `name_judge` accept; not already in `ledger.seen`.

## Quality ladder (tiers, assigned by `hunt.py tier()`)
| Tier | Created (WBY) | Spamhaus | Price |
|------|---------------|----------|-------|
| T1 | ≤2008 | ≥9.5 | ≤$1 |
| T2 | ≤2008 | ≥9.5 | ≤$5 |
| T3 | ≤2008 | ≥9.0 | ≤$5 |
| T4 | ≤2013 | ≥9.5 | ≤$5 |
| T5 | ≤2013 | ≥9.0 | ≤$5 |
`domainAge` comes straight from the feed (WBY = run-year − age). Spamhaus defaults to a clean 10.0
(the real DBL gate runs in the blocklist DNS stage), so tiering is effectively by age + price.

## Daily pipeline (`run_pipeline.py`)
1. **precheck** — assert SLACK_WEBHOOK_URL non-empty (warn if not). No cookie/login checks anymore.
2. **carryover** — prior run's verified-but-undelivered survivors re-enter.
3. **harvest** — `feed_harvest.py`: fetch `inventory.auctions.godaddy.com/all_expiring_auctions.json.zip`
   (~995K records), apply all gates above, write `run/<date>/harvest_new.json`. Caps to
   `feed_harvest_cap` (600, soonest-ending) since the feed exposes thousands in-window.
4. **prescore** — numeric A/B/C name pre-rank; drop fit=C; cap to `ab_cap` (200) best-scored to
   bound the expensive stages.
5. **name_judge** — the abstract-or-IT + taste gate (one OpenAI call; fail-closed to reject).
6. **blocklist** — 42-zone DNS sweep (incl `multi.uribl.com`). Drop listed.
7. **spamhaus** — default clean (DBL already checked at blocklist; the numeric score only fine-tunes
   T1-vs-T3 and isn't safety-critical).
8. **archive** — web.archive.org history; drop flagged; retry errored ≤2×.
9. **vt** — VirusTotal; drop malicious ≥1.
10. **tier + deliver** — `tier()` stamps T1-T5, rank, deliver up to the ceiling via `hunt.py
    append` (sheet webhook; exit 2 = not appended, exit 3 = ambiguous → check sheet). 12h
    delivery guard. Then `hunt.py slack-post` (best-effort). Then commit + `healthcheck_ping`.

## Targets & shortfall (recalibrated 2026-08-05)
Floor **8** (`config.target_per_run`, read as `FLOOR` in run_pipeline), ceiling **20**
(`target_max_per_day`). The genuine daily supply of names passing every rule is ~5-12 (some 0-3
days) — by design, not a failure. Below the floor: a `SHORTFALL: N/8` Slack note + boost `+1`
(cap 2); at/above: boost `-1`. Never pad below the floor by relaxing a rule; never truncate above
the ceiling. If the user wants more volume, the levers are widening age or price — a product
tradeoff (see [[project-expired-domain-hunter]] #31), never a gate relaxation.

## Config knobs (`config.json` / `config.template.json`)
`target_per_run` 8 · `target_max_per_day` 20 · `feed_harvest_cap` 600 · `ab_cap` 200 ·
`price_ceiling_usd` 5 · `harvest_window_hours` [36,120] (boosted [24,168]) · `tiers` (above).

## Monitoring & failure handling
- **Dead-man's-switch**: `run_pipeline.finish()` pings `HEALTHCHECK_PING_URL` on completion; a
  hosted monitor alerts if no ping lands in the daily window (catches "the run never fired").
  No-op until the secret is set.
- **keepalive.yml**: weekly empty commit so GitHub doesn't auto-disable the crons (daily runs push
  to the STATE repo, which doesn't reset that timer).
- **Alert on failure**: the workflow posts to Slack on any job failure.
- **Save state** runs `if: always()` with a rebase-retry push (safe against concurrent state-repo
  writers). Commit is idempotent within a day; the ledger has a `.bak` + corruption guard.

## SYNC (the learning loop)
`daily-sync.yml` (~02:45 UTC) reads the sheet via `sheet_webapp.gs` `doGet`, diffs `Payment Ststus`
against `state/sync_seen.json`, appends accept/reject to `state/taste_log.json`. A live session
reads that log to fold new taste signals into memory and, if a mechanical rule emerges, into
`hunt.py prescore`/`namegrade` or `name_judge.py`. See the `/expired-domain-hunt` skill's SYNC mode.

## Delivery sheet
`1VbNV67WJqFDKY-15h4NVNf2t5kCHTQ06Xim5MYf0RXg`, tab **Sheet1**. Columns incl Domain, Payment Ststus
(user's — read only), GoDaddy_Buy_Link, Name_Fit, Name_Note, Auction_Ends, Auction_Status,
Spamhaus_Score, Domain_Age_Yrs, First_Archived, Blocklists_49, URIBL_web, VirusTotal, History,
Acquire, Backlinks, Price, Tier.

## Files
`feed_harvest.py` (harvest) · `run_pipeline.py` (orchestrator) · `hunt.py` (prescore/blocklist/
archive/vt/tier/append/slack-post/commit/carryover) · `name_judge.py` (name grader) ·
`render_config.py` · `sync_pipeline.py` (sheet sync) · workflows `daily-hunt.yml` / `daily-sync.yml`
/ `keepalive.yml`.
