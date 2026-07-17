#!/usr/bin/env python3
"""LLM name-judgment pass for the CI pipeline (replaces the interactive taste-curation
step normally done by an agent). Calls OpenAI's chat completions API directly (stdlib
urllib, no extra dependency) with the full accept/reject calibration learned across
this project's sessions — see [[feedback-domain-name-grading]] memory for the source
of truth this prompt is derived from.

Usage: python3 name_judge.py <domains.txt> <out.json>
Needs OPENAI_API_KEY in the environment. Output: {domain: {"verdict": "accept"|"reject",
"reason": "..."}}
"""
import json
import os
import sys
import urllib.request

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
        "model": "gpt-4o",
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
    return json.loads(content)


if __name__ == "__main__":
    domains = open(sys.argv[1]).read().split()
    out = {}
    # Batch to keep prompts manageable and avoid one bad batch losing everything.
    for i in range(0, len(domains), 40):
        batch = domains[i:i + 40]
        try:
            result = judge(batch)
            out.update(result)
        except Exception as e:
            for d in batch:
                out[d] = {"verdict": "reject", "reason": f"judge error: {str(e)[:80]}"}
    json.dump(out, open(sys.argv[2], "w"), indent=2)
    accepted = [d for d, v in out.items() if v.get("verdict") == "accept"]
    print(json.dumps({"total": len(out), "accepted": len(accepted)}))
