#!/usr/bin/env python3
"""LLM name-judgment pass for the CI pipeline (replaces the interactive taste-curation
step normally done by an agent). Calls OpenAI's chat completions API directly (stdlib
urllib, no extra dependency) with the full accept/reject calibration learned across
this project's sessions — see [[feedback-domain-name-grading]] memory for the source
of truth this prompt is derived from.

Usage: python3 name_judge.py <domains.txt> <out.json>
Needs OPENAI_API_KEY in the environment. Output: {domain: {"verdict": "accept"|"reject",
"reason": "..."}}

Also tracks estimated cumulative spend against a starting balance and alerts Slack when
the estimated remaining balance drops below a threshold. OpenAI has no reliable "check my
balance" API for modern project-scoped keys (the old /v1/dashboard/billing/credit_grants
endpoint is undocumented and widely reported broken for these — confirmed via research
2026-07-17, not assumed) — so this tracks spend ourselves from the token counts every
chat completion response already includes, which IS reliably documented. This is an
ESTIMATE (assumes gpt-4o pricing stays constant, and that nothing else spends from the
same key) — glance at the real OpenAI dashboard occasionally as a sanity check. When you
top up, ask me to reset SPEND_FILE (or just delete it — a missing file resets to a fresh
$0 spent against OPENAI_STARTING_BALANCE_USD).
"""
import json
import os
import sys
import urllib.request

# 2026-07-18: switched gpt-4o -> gpt-4o-mini after a real side-by-side test against 20
# known accept/reject cases from actual sheet feedback: 14/20 vs gpt-4o's 15/20 (not a
# meaningful gap — the 4 misses were IDENTICAL on both models, a prompt-calibration
# issue, not a model-capability one) at ~15x lower cost ($0.00045 vs $0.00662 for the
# same 20-domain test batch). Pricing per million tokens, confirmed 2026-07-17
# (openai.com pricing page) — update here if OpenAI reprices.
MODEL = "gpt-4o-mini"
PRICE_PER_1M_INPUT = 0.15
PRICE_PER_1M_OUTPUT = 0.60
STARTING_BALANCE_USD = float(os.environ.get("OPENAI_STARTING_BALANCE_USD", "5.0"))
LOW_BALANCE_THRESHOLD_USD = float(os.environ.get("OPENAI_LOW_BALANCE_THRESHOLD_USD", "2.0"))
SPEND_FILE = os.environ.get("OPENAI_SPEND_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "openai_spend.json"))


def _load_spend():
    try:
        return json.load(open(SPEND_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"spent_usd": 0.0}


def _save_spend(spend):
    json.dump(spend, open(SPEND_FILE, "w"), indent=2)


def _slack(text):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

SYSTEM_PROMPT = """You are the final human-taste name-quality gate for an expired-domain \
acquisition pipeline. Every domain you see has ALREADY passed: live-auction check, \
blocklist/Spamhaus/URIBL/VirusTotal, archive.org history, and a mechanical filter for \
IT-vocabulary or an abstract-word+neutral-suffix pattern (Group/Corp/Capital/etc). Your \
job is the part that filter CANNOT do: does this specific name read as a real, credible \
company a buyer would actually want to send cold outreach from?

REJECT any of these, even though the mechanical filter let it through:
- Personal names (first+last, "Surname-and-Surname" professional-firm pattern like \
pittmanandavis, or a single unfamiliar word that reads more like a surname than an \
invented brand word, e.g. sieglesecurity where "Siegle" isn't a dictionary word)
- Cryptic acronyms/abbreviations/geo-codes a normal buyer can't parse at a glance
- Trademarked/competitor product names embedded (cpanel, wordpress, shopify, etc.)
- Generic descriptive phrases that read like a product FEATURE description, not a \
brand a founder would pick (e.g. databasemailer = "a mailer for databases", \
inboxsecurity, viaworldnetwork — two+ generic nouns glued together with no invented \
or distinctive lead word)
- Job-title/freelancer-portfolio phrasing, not a company (vbdotnetcoder = "VB.NET Coder")
- Ambiguous meaning that needs real guesswork to parse (spokesnetwork — bicycle \
spokes? PR spokespeople? unclear)
- Broken pluralization of a service noun (asphostings, optimalclouds — "hosting"/\
"cloud" as a service category isn't normally pluralized this way)
- Invented suffix that reads as a joke/pun rather than professional (cyberistan — \
"-istan" evokes country names)
- Redundant/tautological compounds (rooftopsroofing — both halves say the same thing)
- Unnatural adjective-noun word order (sealingfastener — natural order would differ)
- Event/show names, not a company (geartradeshow)
- Foreign consumer/retail terms with no business context (marikacalzature = Italian \
for "shoes")
- Vulgar, bad-connotation, or brand-unsafe words anywhere in the name (suicidewall, \
foodfartnetwork) — reject unconditionally regardless of any other signal
- Consumer/hobby verticals (beautypageants) or any named non-IT trade/profession that \
slipped through (construction, law, staffing, medical, real estate, automotive, etc.)

ACCEPT only names that read as a genuine, would-actually-exist company: clear IT/tech \
signal used naturally (not just token-stuffed) OR an abstract/invented lead word \
wrapped in a real neutral corporate suffix, with no red flag above.

When genuinely unsure, REJECT — false negatives (a good name held back) cost nothing; \
false positives (a bad name delivered) waste the buyer's review time and this pipeline's \
entire reason for having a taste gate.

Return ONLY a JSON object mapping each input domain to {"verdict": "accept" or \
"reject", "reason": "<one short phrase>"}. No prose outside the JSON."""


def judge(domains):
    api_key = os.environ["OPENAI_API_KEY"]
    body = json.dumps({
        "model": MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Domains to judge:\n" + "\n".join(domains)},
        ],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    resp = json.load(urllib.request.urlopen(req, timeout=60))
    content = resp["choices"][0]["message"]["content"]
    usage = resp.get("usage", {})
    cost = (usage.get("prompt_tokens", 0) * PRICE_PER_1M_INPUT
            + usage.get("completion_tokens", 0) * PRICE_PER_1M_OUTPUT) / 1_000_000
    return json.loads(content), cost


if __name__ == "__main__":
    domains = open(sys.argv[1]).read().split()
    out = {}
    spend = _load_spend()
    # Batch to keep prompts manageable and avoid one bad batch losing everything.
    for i in range(0, len(domains), 40):
        batch = domains[i:i + 40]
        try:
            result, cost = judge(batch)
            out.update(result)
            spend["spent_usd"] = spend.get("spent_usd", 0.0) + cost
        except Exception as e:
            for d in batch:
                out[d] = {"verdict": "reject", "reason": f"judge error: {str(e)[:80]}"}
    _save_spend(spend)
    json.dump(out, open(sys.argv[2], "w"), indent=2)
    accepted = [d for d, v in out.items() if v.get("verdict") == "accept"]

    remaining = STARTING_BALANCE_USD - spend.get("spent_usd", 0.0)
    if remaining < LOW_BALANCE_THRESHOLD_USD:
        _slack(f"⚠️ ALERT: estimated OpenAI balance is low (~${remaining:.2f} left of "
               f"${STARTING_BALANCE_USD:.2f}, based on tracked token usage — top up at "
               "platform.openai.com/settings/billing or the name-judgment step will start "
               "failing and every domain will default to reject). Once topped up, tell "
               "Claude the new balance so it can reset the spend tracker.")

    print(json.dumps({"total": len(out), "accepted": len(accepted),
                       "estimated_spent_usd": round(spend.get("spent_usd", 0.0), 4),
                       "estimated_remaining_usd": round(remaining, 4)}))
