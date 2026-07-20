#!/usr/bin/env python3
"""Deterministic Playwright-based browser steps for the unattended CI run.

Replaces the interactively-scripted browser-harness calls (which need a live agent
loop deciding what to do) with fixed, previously-validated automation for the 3
stages that need a real logged-in/interactive browser: harvest, Spamhaus, URIBL.

Login is restored via a PLAINTEXT cookie JSON (state repo's expireddomains_cookies.json),
injected with Playwright's context.add_cookies() — NOT by copying Chrome's raw Cookies
SQLite file. Confirmed 2026-07-17: Chrome encrypts cookie VALUES at rest using an
OS-keychain-derived key, so a copied Cookies file only decrypts on the exact same
machine/keychain it was written on — it silently fails (LOGIN-WALL) on a fresh Linux CI
runner. The correct portable approach: read decrypted values via CDP's
Network.getAllCookies against the live, already-logged-in browser (see
extract_cookies() below, run manually/periodically on the Mac, not part of the daily
CI flow), store as plaintext JSON in the private state repo, inject via add_cookies()
each run (works cross-machine since there's no keychain involved at the API level).

Subcommands (each does one CI-callable job, JSON in/out like the rest of hunt.py):
  ci_browser.py harvest <plan.json> <outdir>   -> scrapes every list/page in the plan
  ci_browser.py spamhaus <domains.txt> <out.json>
  ci_browser.py uribl <domains.txt> <out.json>
  ci_browser.py extract-cookies <out.json>     -> run via browser-harness's CDP session
                                                   against the live logged-in Chrome,
                                                   NOT via this script directly (see
                                                   comment on extract_cookies())

IMPORTANT: check.spamhaus.org's Cloudflare challenge hard-blocks Playwright's headless
mode outright (confirmed 2026-07-17: 6+ retries, never clears) even with
--disable-blink-features=AutomationControlled applied. The SAME flag + a HEADED
(headless=False) browser clears the challenge in ~2s. Since CI runners have no real
display, this script must be invoked under a virtual framebuffer (`xvfb-run` on the
GitHub Actions Linux runner — see .github/workflows/daily-hunt.yml) so `headless=False`
still works unattended. Do not "simplify" this back to headless=True.
"""
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.environ.get("CHROME_PROFILE_DIR", os.path.expanduser("~/domain-hunter-chrome-profile"))
COOKIES_JSON = os.environ.get("EDH_COOKIES_JSON", os.path.expanduser("~/expireddomains_cookies.json"))


def _browser(pw):
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=False,
        viewport={"width": 1920, "height": 1080},
        args=[
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
        ],
    )
    if os.path.exists(COOKIES_JSON):
        ctx.add_cookies(json.load(open(COOKIES_JSON)))
    return ctx


def _save_cookies(ctx):
    # Persist any session refresh (e.g. a rotated sessid) back for next run.
    edh = [c for c in ctx.cookies() if "expireddomains" in c.get("domain", "")]
    json.dump(edh, open(COOKIES_JSON, "w"), indent=2)


def harvest(plan_path, outdir):
    plan = json.load(open(plan_path))
    entries = plan["passes"] + plan["pass_c"]
    os.makedirs(outdir, exist_ok=True)
    summary = []
    with sync_playwright() as pw:
        ctx = _browser(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for e in entries:
            tag = f"KW_{e['list']}_{e['token']}" if e["pass"] == "KW" else f"{e['pass']}_{e['list']}"
            outpath = os.path.join(outdir, f"harvest_{tag}.json")
            if os.path.exists(outpath):
                existing = json.load(open(outpath))
                if existing.keys() - {"_captured_at"}:
                    summary.append((tag, "skip-exists"))
                    continue
                # Stub from a prior failed pass (LOGIN-WALL / no-table) — no real
                # rows were captured, so this list still needs harvesting.
            rows_out = {}
            for pageno in range(e["pages"]):
                url = e["url"] if pageno == 0 else e["url"] + f"&start={pageno*25}"
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                title = page.title()
                if "Login" in title:
                    summary.append((tag, "LOGIN-WALL"))
                    break
                headers = page.eval_on_selector_all(
                    "table.base1 thead th", "els => els.map(e => e.textContent.trim())"
                )
                if not headers:
                    summary.append((tag, f"no-table-page{pageno}"))
                    break
                idx = {h: i for i, h in enumerate(headers) if h}
                rows = page.eval_on_selector_all(
                    "table.base1 tbody tr",
                    "trs => trs.map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim()))",
                )
                for r in rows or []:
                    if not r or "." not in r[0]:
                        continue
                    def col(name, default=""):
                        i = idx.get(name)
                        return r[i] if i is not None and i < len(r) else default
                    rows_out[r[0]] = {
                        "BL": col("BL"), "WBY": col("WBY"), "Price": col("Price"),
                        "Status": col("Listing Type"), "Endtime": col("Endtime"), "list": e["list"],
                    }
                time.sleep(1.2)
            rows_out["_captured_at"] = time.time()
            json.dump(rows_out, open(outpath, "w"))
            summary.append((tag, len(rows_out) - 1))
        _save_cookies(ctx)
        ctx.close()
    print(json.dumps(summary))
    return any(v == "LOGIN-WALL" for _, v in summary)


def spamhaus(domains_path, out_path):
    domains = open(domains_path).read().split()
    out = {}
    unknown_count = 0
    with sync_playwright() as pw:
        ctx = _browser(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for d in domains:
            page.goto(f"https://check.spamhaus.org/results?query={d}", timeout=30000)
            title, body = "", ""
            # Cloudflare's JS challenge can take longer from some IPs than others
            # (confirmed 2026-07-17: consistently slower on a GitHub Actions runner
            # than a home IP) — poll with increasing waits rather than one fixed pause.
            for wait_s in (2, 4, 6, 10, 15):
                page.wait_for_timeout(wait_s * 1000)
                title = page.title()
                body = page.inner_text("body")
                stripped = body.strip()
                # Past the Cloudflare challenge AND the SPA has actually rendered content —
                # an empty/near-empty body means the client-side fetch hasn't finished yet,
                # NOT that the check is done (confirmed 2026-07-17: exiting early on empty
                # body silently produced "unknown" for a domain that had actually cleared
                # the challenge, just hadn't rendered its result text yet).
                if "Just a moment" not in title and len(stripped) > 20 and not stripped.startswith("Loading"):
                    break
            if "Not Listed" in title or "no issues" in body:
                out[d] = {"status": "not_listed", "score": 10.0}
            elif "Listed" in title:
                out[d] = {"status": "listed", "score": 0.0, "title": title}
            else:
                out[d] = {"status": "unknown", "title": title, "body_snippet": body[:150]}
                unknown_count += 1
            time.sleep(2)
        ctx.close()
    json.dump(out, open(out_path, "w"))
    # If EVERY domain came back unknown, this is very likely a systemic block (e.g.
    # this environment's IP being challenged harder than a residential IP), not normal
    # per-request flakiness — surface that distinction so it isn't read as "0 delivered,
    # nothing wrong" on a quiet failure.
    if domains and unknown_count == len(domains):
        print("SPAMHAUS_SYSTEMIC_FAILURE: all domains stuck on Cloudflare challenge", file=sys.stderr)
    print(json.dumps({d: v["status"] for d, v in out.items()}))


def uribl(domains_path, out_path):
    domains = open(domains_path).read().split()
    out = {}
    with sync_playwright() as pw:
        ctx = _browser(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i in range(0, len(domains), 15):
            batch = domains[i:i+15]
            for attempt in range(4):
                page.goto("https://admin.uribl.com/", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                page.fill("textarea[name=domains]", "\n".join(batch))
                page.click("input[type=submit]")
                page.wait_for_timeout(3000)
                body = page.inner_text("body")
                if "System is busy" not in body:
                    for d in batch:
                        m = re.search(re.escape(d) + r"\s+(NOT Listed on URIBL|.*Listed on)", body)
                        out[d] = {"listed": bool(m and "NOT" not in m.group(1)), "raw": m.group(0) if m else "unparsed"}
                    break
                time.sleep(40)
            time.sleep(2)
        ctx.close()
    json.dump(out, open(out_path, "w"))
    print(json.dumps({d: v["listed"] for d, v in out.items()}))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "harvest":
        login_failed = harvest(sys.argv[2], sys.argv[3])
        if login_failed:
            # Distinct exit code so the workflow/headless claude -p treats this as a
            # hard stop (session expired, needs re-bootstrap) rather than silently
            # continuing with zero rows.
            sys.exit(2)
    elif cmd == "spamhaus":
        spamhaus(sys.argv[2], sys.argv[3])
    elif cmd == "uribl":
        uribl(sys.argv[2], sys.argv[3])
    else:
        sys.exit(f"unknown subcommand {cmd}")
