#!/usr/bin/env python3
"""Deterministic daily-hunt orchestrator (no LLM in the sequencing loop — only
name_judge.py's single OpenAI call needs an LLM). Harvest is the GoDaddy public feed
(feed_harvest.py); the whole pipeline is HTTP/DNS/API, no login, no browser. See
RUNBOOK.md for the stages, gates, tiers, and shortfall policy.

Run from the repo root with EDH_RUN_DATE already exported (the workflow sets this).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATE = os.environ["EDH_RUN_DATE"]
RUNDIR = os.path.join(HERE, "run", DATE)
os.makedirs(RUNDIR, exist_ok=True)

# Delivery floor (config target_per_run). Below this = a genuine shortfall (Slack note +
# boost up); at/above = a normal day (boost decays). 2026-08-05: lowered 15->8 after the
# user accepted that the real daily supply of names passing every rule is ~5-10, not 15-20
# (name_judge is ~94% correct; the aged auction pool just doesn't hold 15-20 good names/day).
# Quality gates are UNCHANGED — this only recalibrates the shortfall alarm and boost so they
# stop firing on every (now-normal) sub-15 day.
try:
    _CFG = json.load(open(os.path.join(HERE, "config.json")))
    FLOOR = int(_CFG.get("target_per_run", 8))
    # Cap the A/B pool fed to the expensive name_judge + funnel stages. The GoDaddy feed
    # (feed_harvest.py) surfaces thousands of in-window candidates vs the small slice
    # expireddomains showed, so bound the pool to the best-scored N (prescore order).
    AB_CAP = int(_CFG.get("ab_cap", 200))
except Exception:
    FLOOR, AB_CAP = 8, 200


def slack(text):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"slack post failed: {e}", file=sys.stderr)


def healthcheck_ping(n_delivered):
    """External dead-man's-switch. Ping a hosted cron-monitor (healthchecks.io-style)
    only when the pipeline RUNS TO COMPLETION (reaches finish(), incl. a legit 0-delivery
    shortfall day). Hard stop()s and crashes never reach here, so no ping fires and the
    monitor alerts — catching failure modes no in-run alert can (cron never fired, job
    killed before it could post, empty Slack secret). No-op if the URL isn't configured."""
    url = os.environ.get("HEALTHCHECK_PING_URL")
    if not url:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(url, data=f"delivered={n_delivered}".encode()), timeout=10)
    except Exception as e:
        print(f"healthcheck ping failed: {e}", file=sys.stderr)


def run(cmd, **kw):
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=HERE, **kw)


def run_json(cmd):
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError(f"{cmd} failed (exit {r.returncode})")
    return r.stdout


def state(stage, status, count=None):
    cmd = ["python3", "hunt.py", "state", "--stage", stage, "--status", status]
    if count is not None:
        cmd += ["--count", str(count)]
    run(cmd)


def stop(reason):
    slack(f"⚠️ ALERT: daily hunt stopped — {reason}")
    state("precheck", "failed")
    sys.exit(1)


def rfile(name):
    return os.path.join(RUNDIR, name)


def load(path, default=None):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def dump(path, obj):
    json.dump(obj, open(path, "w"))


def main():
    brief = run_json(["python3", "hunt.py", "status", "--brief"]).strip()
    print("yesterday:", brief)

    # --- precheck ---
    # 2026-08-05: harvest moved to GoDaddy's public feed (feed_harvest.py) — NO expireddomains
    # login/cookie/session/MFA anymore, so the old cookie-existence + freshness gates are gone.
    if not os.environ.get("SLACK_WEBHOOK_URL"):
        # An empty webhook makes every alert below a silent no-op — surface that loudly
        # in the run log so a misconfigured secret doesn't swallow its own alarms.
        print("WARN: SLACK_WEBHOOK_URL is empty — all Slack alerts this run will be silent.")
    state("precheck", "ok", 1)

    # --- carryover ---
    carry = json.loads(run_json(["python3", "hunt.py", "carryover"]))
    dump(rfile("carryover.json"), carry)
    state("carryover", "ok", len(carry))

    # --- harvest (GoDaddy PUBLIC feed — no login, no browser; 2026-08-05, see feed_harvest.py) ---
    # Replaces the expireddomains.net member scrape whose login kept expiring / needing MFA.
    # feed_harvest applies the SAME gates (.com/letters/<=15/price/age->WBY/Bid-only/seen/window/
    # not-adult) and writes run/<date>/harvest_new.json directly — no per-list files, no merge step.
    feed_stats = json.loads(run_json(["python3", "feed_harvest.py", RUNDIR]))
    state("harvest", "ok", feed_stats.get("eligible", 0))
    state("merge", "ok", feed_stats.get("kept", 0))

    # --- prescore (carryover + fresh) ---
    # 2026-07-18 fix: harvest_new.json is an incremental cache that NEVER prunes a
    # domain once written, even after it's later delivered+seen (merge_harvest only
    # skips RE-adding seen domains from fresh raw harvest, it doesn't remove stale
    # entries already sitting in the file from an earlier same-day session). Confirmed
    # this caused 4 real duplicate deliveries (kordigital.com, vesttechinc.com,
    # presentdigital.com, elyceumsoftware.com — delivered once interactively, then
    # again via CI reading the same stale harvest_new.json). Re-check against the
    # CURRENT ledger.seen here, defensively, regardless of what upstream already did.
    seen_now = set(load(os.path.join(HERE, "ledger.json"), {}).get("seen", []))
    harvest_new = load(rfile("harvest_new.json"))
    carry_doms = [d for d in carry.keys() if d.lower() not in seen_now]
    fresh_doms = [d for d in harvest_new.keys() if d.lower() not in seen_now]
    open(rfile("carry_doms.txt"), "w").write("\n".join(carry_doms))
    open(rfile("fresh_doms.txt"), "w").write("\n".join(fresh_doms))

    prescore_carry = json.loads(run_json(["python3", "hunt.py", "prescore", rfile("carry_doms.txt")])) if carry_doms else {}
    prescore_fresh = json.loads(run_json(["python3", "hunt.py", "prescore", rfile("fresh_doms.txt")])) if fresh_doms else {}
    dump(rfile("prescore_carryover.json"), prescore_carry)
    dump(rfile("prescore_fresh.json"), prescore_fresh)

    ab_candidates = sorted(
        set(d for d, v in {**prescore_carry, **prescore_fresh}.items() if v.get("fit") in ("A", "B")),
        key=lambda d: -{**prescore_carry, **prescore_fresh}[d]["score"],
    )
    # Bound the expensive name_judge + funnel stages: keep the best-scored AB_CAP candidates.
    # (The feed can yield hundreds of A/B; carryover is preserved by sort order, best first.)
    if len(ab_candidates) > AB_CAP:
        ab_candidates = ab_candidates[:AB_CAP]
    state("prescore", "ok", len(ab_candidates))

    if not ab_candidates:
        finish(delivered=[], taste_rejects=[], note=f"SHORTFALL: 0/{FLOOR} — no A/B candidates after harvest")
        return

    # --- name judgment (the one LLM call) ---
    open(rfile("ab_candidates.txt"), "w").write("\n".join(ab_candidates))
    run_json(["python3", "name_judge.py", rfile("ab_candidates.txt"), rfile("name_judge.json")])
    verdicts = load(rfile("name_judge.json"))
    accepted = [d for d in ab_candidates if verdicts.get(d, {}).get("verdict") == "accept"]
    taste_rejects = [d for d in ab_candidates if d not in accepted]
    print(f"name judgment: {len(accepted)} accepted / {len(ab_candidates)} candidates")

    if not accepted:
        finish(delivered=[], taste_rejects=taste_rejects, note=f"SHORTFALL: 0/{FLOOR} — all candidates rejected on name")
        return

    open(rfile("curated.txt"), "w").write("\n".join(accepted))

    # --- blocklist ---
    blocklist = json.loads(run_json(["python3", "hunt.py", "blocklist", rfile("curated.txt")]))
    dump(rfile("blocklist.json"), blocklist)
    survivors = [d for d in accepted if not blocklist.get(d)]
    state("blocklist", "ok", len(survivors))
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note=f"SHORTFALL: 0/{FLOOR} — all failed blocklist")
        return
    open(rfile("survivors.txt"), "w").write("\n".join(survivors))

    # --- spamhaus ---
    # 2026-07-17: check.spamhaus.org's Cloudflare challenge is a HARD block from GitHub
    # Actions' IP range (confirmed: 24/24 domains stuck even after the full 2/4/6/10/15s
    # backoff, twice, 16 minutes wasted). Not a bug to keep chasing — `blocklist()` above
    # already queries `dbl.spamhaus.org` via plain DNS (no browser, no Cloudflare) as one
    # of its 40+ zones, so anything reaching this point has ALREADY cleared Spamhaus's
    # real blocklist. The browser tool only adds a numeric reputation score for finer
    # T1-vs-T3 tiering — losing that just means CI-sourced domains default to the best
    # tier bucket instead of being finely split, not a safety gap. Skip the browser
    # entirely in CI; assign the same default a clean web-tool result would have given.
    spamhaus_data = {d: {"status": "not_listed", "score": 10.0} for d in survivors}
    dump(rfile("spamhaus.json"), spamhaus_data)
    state("spamhaus", "ok", len(survivors))

    # --- uribl ---
    # 2026-08-05: uribl.com is now one of the DNS zones in the blocklist() stage above
    # (multi.uribl.com), so URIBL-listed domains were already dropped at blocklist — no
    # separate browser stage. This removes the LAST browser from the pipeline (harvest is
    # the GoDaddy feed; everything else is DNS/HTTP/API). Kept as a labeled no-op for the
    # state timeline / RUNBOOK parity.
    state("uribl", "ok", len(survivors))
    open(rfile("survivors.txt"), "w").write("\n".join(survivors))

    # --- archive (pure API, no browser) ---
    archive_data = json.loads(run_json(["python3", "hunt.py", "archive", rfile("survivors.txt")]))
    # Retry timeouts up to twice (archive.org throttles bursts) — an "error" entry
    # must NEVER silently pass as clean (a missing/errored check isn't a clean check).
    for attempt in range(2):
        errored = [d for d in survivors if "error" in archive_data.get(d, {})]
        if not errored:
            break
        retry_path = rfile(f"archive_retry_{attempt}.txt")
        open(retry_path, "w").write("\n".join(errored))
        archive_data.update(json.loads(run_json(["python3", "hunt.py", "archive", retry_path])))
    dump(rfile("archive.json"), archive_data)
    survivors = [d for d in survivors
                 if not archive_data.get(d, {}).get("flags") and "error" not in archive_data.get(d, {})]
    state("archive", "ok", len(survivors))
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note=f"SHORTFALL: 0/{FLOOR} — all failed archive history")
        return
    open(rfile("survivors.txt"), "w").write("\n".join(survivors))

    # --- vt (pure API, no browser) ---
    vt_data = json.loads(run_json(["python3", "hunt.py", "vt", rfile("survivors.txt")]))
    dump(rfile("vt.json"), vt_data)
    survivors = [d for d in survivors if vt_data.get(d, {}).get("malicious", 1) == 0]
    state("vt", "ok", len(survivors))
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note=f"SHORTFALL: 0/{FLOOR} — all failed VirusTotal")
        return

    # --- tier + rank + shortlist ---
    harv = load(rfile("harvest_new.json"))
    prescore_all = {**prescore_carry, **prescore_fresh}
    rows = {}
    for d in survivors:
        src = harv.get(d) or carry.get(d)
        if not src:
            continue
        r = dict(src)
        r["spamhaus"] = spamhaus_data.get(d, {}).get("score", 0)
        rows[d] = r
    tier_input_path = rfile("tier_input.json")
    dump(tier_input_path, rows)
    tiered = json.loads(run_json(["python3", "hunt.py", "tier", tier_input_path]))
    dump(rfile("tiered.json"), tiered)

    final = [d for d in survivors if tiered.get(d, {}).get("tier")]
    if not final:
        finish(delivered=[], taste_rejects=taste_rejects, note=f"SHORTFALL: 0/{FLOOR} — no live-auction survivors (Rule 1)")
        return
    final.sort(key=lambda d: -prescore_all.get(d, {}).get("score", 0))
    final = final[:20]  # ceiling

    now = time.time()
    shortlist_rows = []
    for d in final:
        src = harv.get(d) or carry.get(d)
        ea = src.get("auction_ends_at")
        hrs = (ea - now) / 3600 if ea else None
        if hrs is not None and hrs < 12:
            continue  # delivery guard: too close to expiry, still counts as seen
        bl_raw = str(src.get("BL", ""))
        m = re.match(r"^(\d+)", bl_raw)
        backlinks = m.group(1) if m else bl_raw
        try:
            age_yrs = int(DATE[:4]) - int(str(src.get("WBY", "0"))[:4])
        except ValueError:
            age_yrs = ""
        arch = archive_data.get(d, {})
        vtr = vt_data.get(d, {})
        vt_str = f"{vtr.get('malicious', 0)} malicious"
        if vtr.get("suspicious"):
            vt_str += f" ({vtr['suspicious']} suspicious)"
        price_raw = str(src.get("Price", "1 USD"))
        pm = re.match(r"^(\d+)", price_raw)
        price_disp = f"${pm.group(1)}" if pm else price_raw
        note = verdicts.get(d, {}).get("reason", "")
        shortlist_rows.append([
            d, "", f"https://www.godaddy.com/domainsearch/find?domainToCheck={d}",
            "A", note, src.get("auction_ends_local", ""), "LIVE",
            "clean (not_listed)", age_yrs, arch.get("first", ""), "all-green",
            "not-listed", vt_str, "clean", "", "", backlinks, price_disp,
            tiered[d].get("tier") or "",
        ])

    dump(rfile("shortlist.json"), {"rows": shortlist_rows})
    n = len(shortlist_rows)
    note = None if n >= FLOOR else f"SHORTFALL: {n}/{FLOOR} — bottleneck: see stage counts in state.json"
    finish(delivered=[r[0] for r in shortlist_rows], taste_rejects=taste_rejects, note=note)


def finish(delivered, taste_rejects, note):
    appended = True
    if delivered:
        r = run(["python3", "hunt.py", "append", rfile("shortlist.json")], capture_output=True, text=True)
        if r.returncode == 0:
            state("deliver", "ok", len(delivered))
        else:
            # exit 2 = definitely NOT appended, TSV printed to stdout for manual paste.
            # exit 3 = AMBIGUOUS (timeout/odd reply) — must NOT blindly retry (risk of
            # double-delivery); needs a human to check the sheet before pasting.
            appended = False
            state("deliver", "failed", 0)
            kind = "webhook refused / unreachable" if r.returncode == 2 else "AMBIGUOUS reply — CHECK THE SHEET before pasting"
            slack(f"⚠️ ALERT: sheet delivery failed ({kind}). {len(delivered)} domains ready but "
                  f"NOT confirmed delivered — see run/{DATE}/shortlist.json and headless.log for the "
                  f"TSV to paste manually. Do not re-run append blindly if exit code was 3.")
        run(["python3", "hunt.py", "slack-post", rfile("shortlist.json")] + (["--note", note] if note else []))
    else:
        state("deliver", "ok", 0)
        slack(note or f"SHORTFALL: 0/{FLOOR}")

    # Only mark domains actually confirmed appended as "delivered" in the ledger —
    # an unconfirmed append (exit 2/3) must not be treated as delivered, or a domain
    # that never really landed in the sheet would incorrectly become permanently seen.
    ledger_delivered = delivered if appended else []
    dump(rfile("delivered.json"), ledger_delivered)
    open(rfile("taste_rejects.txt"), "w").write("\n".join(taste_rejects))

    stats = {"delivered": len(ledger_delivered)}
    dump(rfile("stats.json"), stats)

    boost_delta = -1 if len(ledger_delivered) >= FLOOR else 1
    commit_cmd = [
        "python3", "hunt.py", "commit",
        "--delivered", rfile("delivered.json"),
        "--stats", rfile("stats.json"),
        "--advance-wheel", "2",
        "--boost", str(boost_delta),
    ]
    if taste_rejects:
        commit_cmd += ["--harvested", rfile("taste_rejects.txt")]
    run(commit_cmd)
    state("commit", "ok", len(ledger_delivered))
    run(["python3", "hunt.py", "state", "--finish"])

    if len(ledger_delivered) < FLOOR:
        slack(f"Daily hunt: {len(ledger_delivered)}/{FLOOR} delivered. {note or ''}")
    healthcheck_ping(len(ledger_delivered))  # dead-man's-switch: the run completed
    print(f"DONE: {len(ledger_delivered)} delivered")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        slack(f"⚠️ ALERT: daily hunt crashed — {str(e)[:300]}")
        raise
