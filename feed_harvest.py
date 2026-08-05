#!/usr/bin/env python3
"""Login-free harvest from GoDaddy's PUBLIC expiring-auctions feed.

Replaces the expireddomains.net member-area browser scrape (which needed a login that
kept expiring / needing MFA). GoDaddy publishes the full expiring-auctions inventory as a
public zip — no login, no browser, no session. We fetch it, apply EXACTLY the same gates
the expireddomains _edurl+merge_harvest chain applied, and write run/<date>/harvest_new.json
in the same shape the rest of the pipeline (prescore -> funnel -> tier -> deliver) already reads.

Gate parity (see the mapping in project memory #33):
  .com | letters-only stem | <=15 chars | price<=ceiling | age->WBY | auctionType==Bid (Rule 1)
  | not ledger.seen | auction window [lo,hi]h | not isAdult

Usage: python3 feed_harvest.py <run_dir>   (EDH_RUN_DATE-dated dir; ledger read from repo root)
"""
import json, os, re, sys, time, urllib.request, zipfile, io, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_URL = "https://inventory.auctions.godaddy.com/all_expiring_auctions.json.zip"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def cfg():
    return json.load(open(os.path.join(HERE, "config.json")))


def ledger():
    try:
        return json.load(open(os.path.join(HERE, "ledger.json")))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def window(l):
    # mirror hunt.py _window(): boosted runs widen 36-120h -> 24-168h
    return [24, 168] if l.get("harvest_boost", 0) > 0 else cfg().get("harvest_window_hours", [36, 120])


def price_num(p):
    m = re.search(r"([\d,]+)", str(p) or "")
    return int(m.group(1).replace(",", "")) if m else 10 ** 9


def fetch_feed(cache_path=None):
    """Return the parsed feed. Uses a local cache file if given and present (for offline test)."""
    if cache_path and os.path.exists(cache_path):
        return json.load(open(cache_path))
    req = urllib.request.Request(FEED_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    name = next(n for n in zf.namelist() if n.endswith(".json"))
    return json.loads(zf.read(name).decode("utf-8", "replace"))


def harvest(run_dir, cache_path=None):
    l = ledger()
    seen = set(d.lower() for d in l.get("seen", []))
    lo, hi = window(l)
    ceiling = cfg().get("price_ceiling_usd", 5)
    year = int(os.environ.get("EDH_RUN_DATE", datetime.date.today().isoformat())[:4])
    now = time.time()

    feed = fetch_feed(cache_path)
    records = feed["data"] if isinstance(feed, dict) else feed

    out = {}
    stats = {"records": len(records), "gate_com_alpha_len": 0, "gate_price": 0, "gate_bid": 0,
             "gate_adult": 0, "gate_age": 0, "gate_seen": 0, "gate_window": 0, "kept": 0}
    for r in records:
        dn = (r.get("domainName") or "").lower()
        if not dn.endswith(".com"):
            continue
        stem = dn[:-4]
        if not stem.isalpha() or len(stem) > 15:
            continue
        stats["gate_com_alpha_len"] += 1
        if r.get("isAdult"):
            stats["gate_adult"] += 1; continue
        # Rule 1: live auctions only (Bid), never BuyNow/closeout
        if (r.get("auctionType") or "") != "Bid":
            stats["gate_bid"] += 1; continue
        # Rule 3: price <= ceiling
        if price_num(r.get("price")) > ceiling:
            stats["gate_price"] += 1; continue
        # age -> WBY (tiers cap at 2008/2013, i.e. age >= 13 covers T1-T5)
        age = r.get("domainAge") or 0
        wby = year - int(age)
        if wby > 2013:
            stats["gate_age"] += 1; continue
        if dn in seen:
            stats["gate_seen"] += 1; continue
        # absolute end time -> auction_ends_at; window filter
        try:
            ends = datetime.datetime.strptime(r["auctionEndTime"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except Exception:
            continue
        hrs = (ends - now) / 3600
        if hrs < lo or hrs > hi:
            stats["gate_window"] += 1; continue
        # dedupe: keep cheapest
        if dn in out and price_num(r.get("price")) >= price_num(out[dn].get("Price")):
            continue
        out[dn] = {
            "BL": str(r.get("majesticBacklinks", "")),
            "WBY": str(wby),
            "Price": f"{price_num(r.get('price'))} USD",
            "Status": "Bid",
            "Endtime": r.get("auctionEndTime", ""),
            "auction_ends_at": ends,
            "hours_left": round(hrs, 1),
            "auction_ends_local": datetime.datetime.fromtimestamp(ends).strftime("%Y-%m-%d %H:%M"),
            "list": "godaddyfeed",
            "numberOfBids": r.get("numberOfBids", 0),
        }
    stats["eligible"] = len(out)
    # The feed exposes the FULL inventory (thousands in-window) vs the small top-N slice
    # expireddomains showed. Cap to keep the expensive funnel (VT 4/min) tractable, taking
    # the SOONEST-ENDING first (most actionable; rotates daily as new auctions enter the
    # window and ledger.seen dedups). Configurable via feed_harvest_cap.
    cap = cfg().get("feed_harvest_cap", 600)
    if len(out) > cap:
        keep = sorted(out.items(), key=lambda kv: kv[1]["hours_left"])[:cap]
        out = dict(keep)
    stats["kept"] = len(out)
    stats["cap"] = cap
    os.makedirs(run_dir, exist_ok=True)
    json.dump(out, open(os.path.join(run_dir, "harvest_new.json"), "w"))
    return stats


if __name__ == "__main__":
    rd = sys.argv[1]
    cache = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(harvest(rd, cache), indent=1))
