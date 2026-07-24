#!/usr/bin/env python3
"""Deterministic daily sheet-sync for CI — no browser, no local Chrome.

Reads the delivery sheet via the Apps Script webhook's doGet endpoint (added
2026-07-20 specifically to make this possible — see sheet_webapp.gs), diffs
today's Payment Ststus column against what a prior run last saw, and posts
what changed to Slack.

Also appends every observed accept/reject to a durable, git-backed log
(state/taste_log.json). A CI run has no access to the local
~/.claude/projects memory system that historically recorded this by hand —
a live Claude Code session reads that log later to fold new signals into
real cross-session taste memory and, if warranted, tighten
name_judge.py's prompt. This script never edits name_judge.py itself.

Run from the repo root with the state repo checked out at ./state.
EDH_RUN_DATE is optional (only used to stamp new taste_log entries).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("EDH_STATE_DIR", os.path.join(HERE, "state"))
SEEN_PATH = os.path.join(STATE_DIR, "sync_seen.json")
TASTE_LOG_PATH = os.path.join(STATE_DIR, "taste_log.json")

ACCEPT_STATUSES = {"submitted bid", "submitted", "accepted", "won"}
REJECT_STATUSES = {"no", "invalid", "not a good name", "rejected", "weird name"}
# Bid-outcome statuses, not name-taste signals — tracked in the Slack summary
# but not written to taste_log.json (that log is for name-quality feedback).
NEUTRAL_STATUSES = {"outbid", "priced out", "lost"}


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


def fetch_sheet():
    url = os.environ["SHEET_WEBAPP_URL"]
    token = os.environ["SHEET_WEBAPP_TOKEN"]
    q = urllib.parse.urlencode({"token": token, "tab": "Sheet1"})
    req = urllib.request.Request(f"{url}?{q}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"sheet webhook unreachable: {e}")
    if not data.get("ok"):
        raise RuntimeError(f"sheet read failed: {data.get('error')}")
    return data["values"]


def load(path, default):
    try:
        return json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=2)
    os.replace(tmp, path)


def classify(status):
    s = (status or "").strip().lower()
    if not s:
        return None  # still unreviewed — nothing to report yet
    if s in ACCEPT_STATUSES:
        return "accept"
    if s in REJECT_STATUSES:
        return "reject"
    if s in NEUTRAL_STATUSES:
        return "neutral"
    return "other"


def main():
    values = fetch_sheet()
    header = values[0]
    idx = {h: i for i, h in enumerate(header) if h}
    dom_i = idx.get("Domain")
    status_i = idx.get("Payment Ststus")
    note_i = idx.get("Name_Note")
    if dom_i is None or status_i is None:
        raise RuntimeError(f"expected columns not found in header: {header}")

    seen = load(SEEN_PATH, {})
    taste_log = load(TASTE_LOG_PATH, [])

    changes = {"accept": [], "reject": [], "neutral": [], "other": []}
    for row in values[1:]:
        dom = (row[dom_i] if dom_i < len(row) else "").strip()
        if not dom:
            continue
        status = row[status_i] if status_i < len(row) else ""
        name_note = row[note_i] if note_i is not None and note_i < len(row) else ""
        prior = seen.get(dom)
        if status == prior:
            continue
        seen[dom] = status
        kind = classify(status)
        if kind is None:
            continue
        entry = {"domain": dom, "status": status, "name_note": name_note}
        changes[kind].append(entry)
        if kind in ("accept", "reject"):
            taste_log.append({**entry, "kind": kind, "seen_on": os.environ.get("EDH_RUN_DATE", "")})

    dump(SEEN_PATH, seen)
    dump(TASTE_LOG_PATH, taste_log)

    total_new = sum(len(v) for v in changes.values())
    if total_new == 0:
        print("sync: no status changes since last run")
        return

    lines = [f"*Daily sheet sync*: {total_new} status change(s) since last check."]
    if changes["accept"]:
        lines.append(f"✅ Accepted ({len(changes['accept'])}): " + ", ".join(c["domain"] for c in changes["accept"]))
    if changes["reject"]:
        lines.append(f"❌ Rejected ({len(changes['reject'])}): " + ", ".join(c["domain"] for c in changes["reject"]))
    if changes["neutral"]:
        lines.append(f"↔️ Outbid/other bid outcome ({len(changes['neutral'])}): " + ", ".join(c["domain"] for c in changes["neutral"]))
    if changes["other"]:
        lines.append(f"❓ Other status ({len(changes['other'])}): " + ", ".join(f"{c['domain']} ({c['status']})" for c in changes["other"]))
    summary = "\n".join(lines)
    print(summary)
    slack(summary)


if __name__ == "__main__":
    main()
