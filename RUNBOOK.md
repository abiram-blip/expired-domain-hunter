# Expired Domain Hunter — DAILY runbook (v2)

Goal: deliver **15-20 NEW auction .com domains per day** to the Google Sheet, quality-ranked,
ready to bid on. High reputation, clean history, for cold outreach mailboxes (root 301s to the
client). Register-direct "Normal Domain" finds do NOT count toward the total (may be appended as
`BONUS` rows). Never re-surface a ledger.seen domain. Bidding and payment are always done by the user.

## THREE MANDATORY RULES (user directive 2026-07-14 — never relax, checked every single day)
1. **Auction-live only.** A domain with no parseable auction countdown (`auction_ends_at` is
   None) is NEVER delivered, full stop — not even as a shortfall fallback. `hunt.py tier()`
   enforces this in code: a no-live-auction row gets `tier=None` unconditionally, which excludes
   it from every downstream ranking/delivery step. `carryover()` skips them at the source too.
2. **Abstract OR IT-based name only — no other vertical.** Reject any name that names a specific
   non-IT profession/trade (law, consulting, construction, recycling, staffing/recruitment,
   accounting, real estate, roofing, HVAC, manufacturing, industrial, etc. — see `VERTICAL_BAD`
   in hunt.py) even if every other check passes. Accept only: (a) clear IT/tech signal (system,
   data, cyber, cloud, digital, software, network, hosting, security, analytics, etc. — see
   `TOKENS_GOOD`), or (b) an abstract/invented lead word wrapped in a neutral corporate suffix
   (Group, Corp, Associates, Capital, Holdings, LLC — see `NEUTRAL_SUFFIX`) with no vertical word
   attached. Calibrated and code-tested 2026-07-14 against a real batch (16/17 automated match;
   the residual miss — "Surname-and-Surname" professional-firm naming, e.g. pittmanandavis — still
   needs human curation to catch, same as always).
3. **Price $1-$5 only**, not up to $10. `config.json` `price_ceiling_usd` and every tier's
   `price_max` updated accordingly; `plan_harvest()`'s Pass B and Pass C URLs use this ceiling.

Target is flexible: **15 is the floor, 20 is the ceiling** — if more than 15 domains pass every
rule above, deliver up to 20 rather than discarding good ones. Never pad below 15 with a weaker
name/price/auction-status just to hit a round number — the shortfall policy (deliver what passed,
report honestly, boost tomorrow) still applies below 15.

## Preconditions
- Mac awake at 07:25 (`pmset repeat wakeorpoweron MTWRFSU 07:25:00` — one-time user setup).
- Dedicated browser instance running and logged into expireddomains.net (see "Dedicated
  browser" below) — NOT the user's main day-to-day Chrome.
- `config.json`: VT_API_KEY set; `slack_webhook_url` + `slack_channel` set (posts to #domain-hunt,
  Prospectspulse workspace, app "Domain Hunter" — configured 2026-07-12); `sheet_webapp_url` + `sheet_webapp_token` set once the user
  deploys the Apps Script webhook (until then, delivery falls back to browser paste).
- `ledger.json` present (seen[] / delivered[] / keyword_wheel_pos / harvest_boost / stats).

## Dedicated browser (2026-07-15 — replaces "the user's main Chrome")
The pipeline no longer touches the user's day-to-day Chrome at all. A completely separate,
isolated Chrome instance runs persistently in the background, on its own profile, dedicated
solely to this pipeline:
- Launched via `~/Library/LaunchAgents/com.user.domainhunterchrome.plist`: profile dir
  `~/Library/Application Support/domain-hunter-chrome-profile`, `--remote-debugging-port=9223`,
  `RunAtLoad`+`KeepAlive` true (auto-restarts on crash/reboot, always available for 07:30).
- Logged into expireddomains.net only (member area) — that's the only site needing a real
  login for this pipeline; sheet + Slack delivery go through webhooks (no browser needed there).
- **Every browser-harness call must set `BU_NAME=domainhunt BU_CDP_URL=http://127.0.0.1:9223`**
  (or inherit them — `run_hunt.sh` exports both before launching the headless session, so the
  headless agent's own Bash calls pick them up automatically without repeating them per-call).
  Omitting these connects to the user's main Chrome instead — wrong instance, will not have the
  expireddomains.net login.
- **3 persistent tabs live in this instance, reused every day — never `new_tab()` for these:**
  expireddomains.net (member, any list page), `https://check.spamhaus.org/`,
  `https://admin.uribl.com/`. At the start of harvest/Spamhaus/URIBL stages, `list_tabs()` and
  `switch_tab()` to the matching existing tab by URL substring; only `new_tab()` if a tab is
  genuinely missing (e.g. first-ever run, or the user closed one).
- If precheck finds this instance not logged in (title shows "Login", or the instance isn't
  running at all — `lsof -i :9223` empty): the user must log in there manually, same as before;
  do not guess credentials. If the instance itself isn't running, `launchctl kickstart
  gui/$(id -u)/com.user.domainhunterchrome` should bring it back (KeepAlive normally handles this
  automatically).

## Triggers
- 07:30 launchd → `run_hunt.sh` → headless `claude -p` run of this runbook.
- 07:55 launchd → same script: silent if today's `state.json` exists, else desktop notification
  so the user starts the run manually ("run the domain hunt").
- A run can be (re)started any time; it resumes from today's `state.json` (see Resume).

## The bar (hard gates — never relaxed, any tier)
`.com`, letters-only, <=15 chars (code-level only — user explicitly asked 2026-07-12 NOT to use
their own saved-search filter, `fminhost=2&fmaxhost=12&fwhoisagemax=1990`; the agent's own
tuning is used instead, no site-level char/age-floor param at all, see Sourcing below);
**auction-live only (Rule 1)**; **abstract or IT-based name only (Rule 2)**; **price $1-$5 (Rule 3)**;
41 blocklists all-green (sheet column keeps its legacy "Blocklists_49" name); URIBL not-listed;
VT 0 malicious; no adult/gambling/pharma/PBN history; name grade A or B; Spamhaus < 9.0 = absolute drop.

## Quality ladder (fill 15-20 from T1 down; every sheet row gets its Tier)
| Tier | Created (WBY) | Spamhaus | Price | Source |
|------|--------------|----------|-------|--------|
| T1 | <=2008 | >=9.5 | <=$1 | $1 lists, fwhoisage=2008&fpriceto=1 |
| T2 | <=2008 | >=9.5 | <=$5 | closeout lists, fpriceto=5 |
| T3 | <=2008 | 9.0-9.49 | <=$5 | same harvests, score decides |
| T4 | 2009-2013 | >=9.5 | <=$5 | pass C (fwhoisage=2013), STANDARD daily pass (see below) |
| T5 | 2009-2013 | 9.0-9.49 | <=$5 | pass C |

**Sourcing strategy (redesigned 2026-07-12):** 3 days of data (629/398/164 new domains/day)
showed the strict <=2008 age filter + same-list ledger dedup shrinks fast — real countdown-
confirmed new candidates were down to 11 by day 3. Pass C (age 2009-2013) now runs EVERY day by
default (`plan-harvest`'s `include_pass_c` is always true), not just on shortfall boost — this
roughly doubles the sourcing pool while every hard gate above stays identical; only the tier
label changes. Keyword-wheel tokens/day raised from 2 to 5 (24-token wheel completes in ~5 days
instead of 12) to reach inventory outside the top-N endtime-sorted view, at near-zero extra
harvest cost. No site-level character-length or age-floor filter is applied to ANY list — the
user has a personal saved search on expireddomains.net ("GoDaddy Expired Domains",
`fminhost=2&fmaxhost=12&fwhoisagemax=1990`) but explicitly asked it NOT be used for this
pipeline; the code-level <=15 char check above is the only length gate, applied uniformly.

Rank within tier: name grade (A>B) → prescore desc → auction window (2-5d out > 5d+ > 24-48h).
**Rule 1 supersedes the old fallback policy (2026-07-14): `no_live_auction` rows are NEVER
delivered, not even on a shortfall day.** (2026-07-11 lesson that led here: 2/2 no-countdown
closeout listings delivered came back "not for auction"/"not for sale".) `hunt.py tier()` already
enforces this by setting `tier=None` on any no-live-auction row — the ranking/delivery loop's
existing `if tier is None: continue` skip is sufficient, no separate check needed. Do not
resurrect the old "VERIFY - no live auction" fallback labeling; if fewer than 15 real-auction
candidates pass, that is a shortfall (see policy below), not a reason to include a closeout row.

## Daily pipeline (agent executes; helper = hunt.py; ~75-100 min)

**Run start:** `export EDH_RUN_DATE=$(date +%F)` in every shell used for the run — this pins
all hunt.py date defaults so a run crossing midnight stays in ONE run dir. All stage files
live in `run/$EDH_RUN_DATE/`: `harvest_<list>.json`, `harvest_new.json`, `prescore.json`,
`blocklist.json`, `spamhaus.json`, `uribl.json`, `archive.json`, `vt.json`, `shortlist.json`,
`delivered.json`, `state.json`. Update `state.json` after every stage:
`python3 hunt.py state --stage <s> --status ok|partial|failed --count N`.
**File naming is load-bearing, not cosmetic** (2026-07-12 lesson): `carryover()` and `hunt.py
status` only ever read the CANONICAL names above. If VT (or any stage) is run in multiple batches
across a day, merge ALL results into the single canonical `run/$EDH_RUN_DATE/vt.json` before the
run ends — a result saved under an ad-hoc name (`vt_fresh.json`, `vt_todo.json`, etc.) is
invisible to tomorrow's carryover check, which can let an already-failed domain (e.g. VT
malicious) silently resurface as if it were never checked. If a stage runs in batches, accumulate
into one in-memory dict and write the canonical file once at the end, or read-merge-write it.
Harvest files: write a top-level `"_captured_at": <unix ts>` key at capture time (merge uses
it to anchor countdown parsing); NEVER rewrite a harvest file after capture — endtimes are
relative countdowns and re-anchoring them shifts every deadline.

1. **Status + precheck** — `python3 hunt.py status --brief` (yesterday's result). With
   `BU_NAME=domainhunt BU_CDP_URL=http://127.0.0.1:9223` set (see "Dedicated browser" above),
   `list_tabs()` and `switch_tab()` to the existing expireddomains.net tab (never `new_tab()` —
   the 3 persistent tabs already exist in this dedicated instance); check for logout link /
   absence of login form there. If the dedicated instance itself isn't reachable at all
   (`lsof -i :9223` empty), try `launchctl kickstart gui/$(id -u)/com.user.domainhunterchrome`
   once, then recheck. Fail → osascript notification + `state --stage precheck --status failed`,
   STOP — do not guess credentials, do not fall back to the user's main Chrome.
2. **Carryover** — `python3 hunt.py carryover` : yesterday's verified-but-undelivered survivors
   (auction still >14h out) re-enter at the stage after `stage_reached`; their earlier clean
   results are reused from yesterday's stage files.
3. **Harvest** — `python3 hunt.py plan-harvest` prints today's schedule (passes A/B, 2 keyword
   tokens from the wheel, pages per list, window, boost level). Browser-harness scrapes each
   listed URL, follows the "Next Page" link in the DOM up to the page count, captures
   Domain, Price, BL, WBY, Status, Endtime per row, writes `harvest_<list>.json` per list.
   **Column positions are NOT the same across lists** (2026-07-12 lesson: `dynadotexpired` has
   27 columns not 28, `namecheapauctions` 26, `gnameauctions` 25 — hardcoding `td[26]` for
   Endtime silently mis-scraped 3 lists as blank for 3 days, wrongly treating live auctions as
   closeout/no-countdown). ALWAYS read the header row (`table.base1 thead th`) once per list and
   locate `BL`/`WBY`/`Price`/`Endtime`/`Listing Type or Status` by NAME, then index by position —
   never hardcode column indices. `godaddycloseouts` and `sedobargains` genuinely have no
   Endtime column at all (confirmed) — those are real closeout/buy-now listings, not a bug.
   - Sort is `o=endtime&r=a` (self-refreshing daily). NOT `o=bl` — backlink sort surfaces junk.
   - FIRST RUN: verify `o=endtime` and the keyword filter param (`fdomain=`) actually match the
     site's URL scheme (member settings may differ); correct `_edurl()` in hunt.py if needed and
     record the confirmed URL shape here.
   - If the member "results per page" account setting exists, set it to max once (fewer loads).
4. **Merge** — `python3 hunt.py merge-harvest run/$DATE/harvest_*.json` : dedupe, ledger-seen
   filter, Endtime → absolute `auction_ends_at` (handles "10d 2h 22m" / "2d 11h" / "11h 22m"),
   window filter (36h-120h; boost widens to 24h-168h) → `harvest_new.json`. Target ~180 new/day.
5. **Prescore** — `python3 hunt.py prescore <domains>` : numeric 0-100 + A/B/C. Drop fit=C.
6. **Blocklist** — `python3 hunt.py blocklist <survivors>` (48-way parallel, ~1-2 min). Drop listed.
7. **Spamhaus** — browser-harness `http_get` of the check.spamhaus.org sia-proxy endpoint,
   batches of ~18. Keep the numeric score per domain (needed for tiering). Drop <9.0, abused, spam-tag.
   (Parallel web_fetch is the old method; Parallel credit exhausted 2026-07-10 — browser fetch is primary.)
8. **Projection checkpoint** — if survivors so far project below 15 (use trailing pass rates from
   `ledger.stats`), harvest pass C NOW (`plan-harvest` prints `pass_c` URLs) while the browser is
   warm; pass-C domains re-enter at step 5.
9. **URIBL loop** (browser-harness, automated) — batches of `uribl_batch_size` (15): one tab,
   js() fills the uribl.com checker textarea (newline-joined), submits, parses the results table
   into `{domain:{listed,lists,raw}}`; append to `uribl.json` after EVERY batch. Sleep
   `uribl_batch_gap_seconds` (25s) + jitter between batches. Block signals (403/429/captcha/empty
   results): backoff 2/5/10 min, max 3 tries; unresolved domains are HELD OUT of delivery
   (state: uribl partial) and become tomorrow's carryover. Every submitted domain must appear in
   the parsed output; if the DOM shape is unrecognized, screenshot + dump body text to
   `uribl_raw_batchN.txt` and parse manually.
10. **Archive history** — `python3 hunt.py archive <survivors>` as a BACKGROUND job overlapped
    with step 9 (independent resources; higher kill-rate than VT so it runs before VT). Agent
    eyeballs mid + origin-year snapshots for anything flagged/ambiguous. Drop bad; parked OK (flag).
11. **VirusTotal** — `python3 hunt.py vt <survivors>` LAST (4/min free tier; ~37 domains ≈ 10 min).
    Drop malicious >= 1. (pistolwimp/altwheels/precisiontops lesson: reputation-clean ≠ safe.)
12. **Grade + tier + deliver** — agent final name grade (taste layer: Rules 1-3 above, plus reject
    personal names, cryptic acronyms, consumer/quirky — see feedback memory), `python3 hunt.py tier`
    stamps T1-T5 (no_live_auction rows get tier=None automatically, dropping them), rank, take
    top 15-20 (floor 15, ceiling 20 — deliver everything that passes up to 20, don't truncate good
    candidates at 15) → `shortlist.json` (rows in sheet column order). Delivery guard on
    `auction_ends_at`: <12h left = do NOT deliver (still ledger.seen); 12-24h = flag
    `URGENT <24h` in Acquire, sort to top. Re-check hours-left at this moment (funnel takes
    1-2h; merge-time hours_left is stale). `python3 hunt.py append run/$EDH_RUN_DATE/shortlist.json`.
    Exit codes: 0 = appended; 2 = definitely NOT appended → paste the printed TSV via browser;
    3 = AMBIGUOUS (timeout/odd reply) → CHECK THE SHEET first, paste only rows that are missing,
    or you will double-deliver. Write `delivered.json`.
    Then, same shortlist, post to Slack: `python3 hunt.py slack-post run/$EDH_RUN_DATE/shortlist.json
    [--note "SHORTFALL: N/15 — bottleneck: <stage>"]` (only on shortfall days). This is
    best-effort/never blocks the run — a failed Slack post (exit 1) is logged in `state.json`
    notes and the run continues; the sheet delivery is the source of truth regardless.
13. **Commit + close** — `python3 hunt.py commit --delivered <delivered> --carryover <today's
    held/pending> --stats <stage-counts> --advance-wheel 2 --boost <-1 if >=15 delivered, +1 if
    shortfall>` ; then `state --stage commit --status ok` and `state --finish`;
    osascript notification "N domains delivered — sheet ready. Yesterday: M/15."
    **Do NOT pass `--harvested <all-new>` anymore (2026-07-15 fix).** `commit()` now derives the
    seen-additions itself from evidence of actual review — the union of every domain that
    appears in TODAY's `blocklist.json`/`spamhaus.json`/`uribl.json`/`archive.json`/`vt.json`
    (i.e. anything actually run through a verification stage). A domain that was merely one row
    among thousands in a broad harvest sweep, never selected into the curated candidate set, is
    NOT marked seen and can resurface on a later day. `--harvested` still exists as an optional
    explicit extra-blacklist list for edge cases, but the default daily call omits it entirely.
    This fixed the pool-exhaustion bug from 2026-07-10 through 07-14 where EVERY raw-harvested
    domain got permanently blacklisted regardless of whether it was ever reviewed — by day 5,
    `godaddytdnam` was returning 0 new domains. This fix is forward-only: it does not retroactively
    un-blacklist domains committed under the old behavior before 2026-07-15.
    Commit is idempotent within a day: a resume that re-runs it re-unions seen/delivered but
    skips wheel/boost/stats (last_commit_date guard) — safe to re-run, don't fight it.
    Ledger safety: commit writes ledger.json.bak first and refuses to run against a corrupt or
    suspiciously-missing ledger. If hunt.py ever exits with a "ledger.json is CORRUPT/MISSING"
    message, STOP and restore ledger.json.bak — do not delete files to "fix" it.

## Shortfall / surplus policy
Deliver only what passed all three mandatory rules plus every hard gate — never pad, never
relax past T5, never include a no_live_auction or off-price or off-vertical name just to hit a
number. If N<15: append a `SHORTFALL: N/15 — bottleneck: <worst stage>` note row to the sheet,
commit `--boost +1` (cap 2: next run = +2 wheel tokens, +2 tdnam pages, window 24h-168h, pass C
unconditional). Boost decays `-1` per day that hits >=15. If N>15 (up to 20): deliver all of
them, don't truncate. Carryover reuses verified stock; the floor stays 15, the ceiling is 20.

## Resume (same day)
Read today's `state.json`; resume at the first stage that is not `ok`, reusing existing stage
files. Failure map: CDP dead → precheck failed, notify. expireddomains logged out → harvest
failed, notify. Spamhaus fetch broken → hold delivery (score is tier-critical). VT quota → deliver
only VT-cleared. URIBL blocked → pending held out, tomorrow's carryover.

## Delivery sheet
https://docs.google.com/spreadsheets/d/1VbNV67WJqFDKY-15h4NVNf2t5kCHTQ06Xim5MYf0RXg
Tab: **Sheet1** (the only tab; "Runs" in the old runbook was wrong). Columns as per the live
header row incl. Domain, Payment Ststus (user's), GoDaddy_Buy_Link, Name_Fit, Name_Note,
Auction_Ends (absolute local datetime), Auction_Status, Spamhaus_Score, Domain_Age_Yrs,
First_Archived, Blocklists_49, URIBL_web, VirusTotal, History, Acquire, Backlinks, Price,
**Tier** (new). Build `shortlist.json` rows in the live header order — verify against row 1
before first webhook append.

## Keyword wheel (24 tokens, 2/day, ~12-day cycle)
tech soft system data cyber cloud digital logic secure solution consult group works labs global
precision machine tool steel metal engineer control automat industr
(edit in config.json `keyword_wheel`; position persisted in ledger `keyword_wheel_pos`)

## Notes
- expireddomains.net has NO API — harvest MUST use the logged-in browser.
- Prices move; Price is a snapshot at run time. Re-verify at payment.
- Every finding is only as good as the checks; do not skip VT or history.
- Old flat files in `run/` are pre-v2 history; new runs only write `run/YYYY-MM-DD/`.
