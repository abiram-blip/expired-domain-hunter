#!/usr/bin/env python3
"""Deterministic daily-hunt orchestrator for CI (no LLM in the sequencing loop —
only name_judge.py's single OpenAI call needs an LLM). Mirrors RUNBOOK.md's 13 steps
exactly; see RUNBOOK_CI.md for what's mechanically different in CI vs an interactive
agent run. Every stage's rules (Rule 1-3, tiers, shortfall policy) are unchanged.

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
    cookies_path = os.environ.get("EDH_COOKIES_JSON", os.path.expanduser("~/expireddomains_cookies.json"))
    if not os.path.exists(cookies_path) or os.path.getsize(cookies_path) < 10:
        stop("no expireddomains.net session cookie found — re-run extract_cookies.py "
             "against the Mac's live dedicated Chrome and push a fresh "
             "expireddomains_cookies.json to the state repo.")
    state("precheck", "ok", 1)

    # --- carryover ---
    carry = json.loads(run_json(["python3", "hunt.py", "carryover"]))
    dump(rfile("carryover.json"), carry)
    state("carryover", "ok", len(carry))

    # --- harvest ---
    plan = json.loads(run_json(["python3", "hunt.py", "plan-harvest"]))
    dump(rfile("plan.json"), plan)
    r = run(["python3", "ci_browser.py", "harvest", rfile("plan.json"), RUNDIR])
    if r.returncode == 2:
        stop("expireddomains.net session expired mid-harvest (login wall) — "
             "re-run extract_cookies.py and push a fresh cookie to the state repo.")
    raw_total = 0
    harvest_files = [f for f in os.listdir(RUNDIR) if f.startswith("harvest_") and f != "harvest_new.json"]
    for f in harvest_files:
        d = load(rfile(f))
        raw_total += len(d) - (1 if "_captured_at" in d else 0)
    state("harvest", "ok", raw_total)

    # --- merge ---
    merge_result = json.loads(run_json(
        ["python3", "hunt.py", "merge-harvest"] + [rfile(f) for f in harvest_files]
    ))
    state("merge", "ok", merge_result.get("new", 0))

    # --- prescore (carryover + fresh) ---
    harvest_new = load(rfile("harvest_new.json"))
    carry_doms = list(carry.keys())
    fresh_doms = list(harvest_new.keys())
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
    state("prescore", "ok", len(ab_candidates))

    if not ab_candidates:
        finish(delivered=[], taste_rejects=[], note="SHORTFALL: 0/15 — no A/B candidates after harvest")
        return

    # --- name judgment (the one LLM call) ---
    open(rfile("ab_candidates.txt"), "w").write("\n".join(ab_candidates))
    run_json(["python3", "name_judge.py", rfile("ab_candidates.txt"), rfile("name_judge.json")])
    verdicts = load(rfile("name_judge.json"))
    accepted = [d for d in ab_candidates if verdicts.get(d, {}).get("verdict") == "accept"]
    taste_rejects = [d for d in ab_candidates if d not in accepted]
    print(f"name judgment: {len(accepted)} accepted / {len(ab_candidates)} candidates")

    if not accepted:
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — all candidates rejected on name")
        return

    open(rfile("curated.txt"), "w").write("\n".join(accepted))

    # --- blocklist ---
    blocklist = json.loads(run_json(["python3", "hunt.py", "blocklist", rfile("curated.txt")]))
    dump(rfile("blocklist.json"), blocklist)
    survivors = [d for d in accepted if not blocklist.get(d)]
    state("blocklist", "ok", len(survivors))
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — all failed blocklist")
        return
    open(rfile("survivors.txt"), "w").write("\n".join(survivors))

    # --- spamhaus ---
    r = run(["python3", "ci_browser.py", "spamhaus", rfile("survivors.txt"), rfile("spamhaus.json")], capture_output=True, text=True)
    print(r.stdout)
    spamhaus_data = load(rfile("spamhaus.json"))
    pre_count = len(survivors)
    survivors = [d for d in survivors if spamhaus_data.get(d, {}).get("status") == "not_listed"]
    state("spamhaus", "ok", len(survivors))
    if "SPAMHAUS_SYSTEMIC_FAILURE" in (r.stderr or ""):
        slack("⚠️ ALERT: Spamhaus check got stuck on Cloudflare's challenge for EVERY "
              f"candidate this run ({pre_count} domains) — likely this runner's IP being "
              "challenged harder than a residential IP, not a normal per-domain flake. "
              "Worth investigating (proxy, different runner region, or a Spamhaus API tier) "
              "if this repeats.")
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — all failed Spamhaus")
        return
    open(rfile("survivors.txt"), "w").write("\n".join(survivors))

    # --- uribl ---
    run(["python3", "ci_browser.py", "uribl", rfile("survivors.txt"), rfile("uribl.json")])
    uribl_data = load(rfile("uribl.json"))
    survivors = [d for d in survivors if not uribl_data.get(d, {}).get("listed")]
    state("uribl", "ok", len(survivors))
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — all failed URIBL")
        return
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
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — all failed archive history")
        return
    open(rfile("survivors.txt"), "w").write("\n".join(survivors))

    # --- vt (pure API, no browser) ---
    vt_data = json.loads(run_json(["python3", "hunt.py", "vt", rfile("survivors.txt")]))
    dump(rfile("vt.json"), vt_data)
    survivors = [d for d in survivors if vt_data.get(d, {}).get("malicious", 1) == 0]
    state("vt", "ok", len(survivors))
    if not survivors:
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — all failed VirusTotal")
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
        finish(delivered=[], taste_rejects=taste_rejects, note="SHORTFALL: 0/15 — no live-auction survivors (Rule 1)")
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
    note = None if n >= 15 else f"SHORTFALL: {n}/15 — bottleneck: see stage counts in state.json"
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
        slack(note or "SHORTFALL: 0/15")

    # Only mark domains actually confirmed appended as "delivered" in the ledger —
    # an unconfirmed append (exit 2/3) must not be treated as delivered, or a domain
    # that never really landed in the sheet would incorrectly become permanently seen.
    ledger_delivered = delivered if appended else []
    dump(rfile("delivered.json"), ledger_delivered)
    open(rfile("taste_rejects.txt"), "w").write("\n".join(taste_rejects))

    stats = {"delivered": len(ledger_delivered)}
    dump(rfile("stats.json"), stats)

    boost_delta = -1 if len(ledger_delivered) >= 15 else 1
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

    if len(ledger_delivered) < 15:
        slack(f"Daily hunt: {len(ledger_delivered)}/15 delivered. {note or ''}")
    print(f"DONE: {len(ledger_delivered)} delivered")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        slack(f"⚠️ ALERT: daily hunt crashed — {str(e)[:300]}")
        raise
