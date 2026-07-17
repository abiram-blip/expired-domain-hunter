"""Run this INSIDE a browser-harness session (via `exec(open(...).read())`), not as a
standalone script — it needs browser-harness's `cdp()` helper, pre-imported only in
that context. Extracts expireddomains.net's session cookies in PLAINTEXT from the
live, already-logged-in dedicated Chrome, for storing in the private state repo.

Usage (bootstrap, and again whenever the CI precheck reports a login failure):
  cd /Users/test/expired-domain-hunter
  export BU_NAME=domainhunt
  export BU_CDP_URL=http://127.0.0.1:9223
  browser-harness <<'PY'
  exec(open('extract_cookies.py').read())
  PY
  # then copy /tmp/expireddomains_cookies.json into the state repo and push.
"""
import json

cookies = cdp("Network.getAllCookies", {})
edh = [c for c in cookies.get("cookies", []) if "expireddomains" in c.get("domain", "")]

pw_cookies = []
for c in edh:
    pc = {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "httpOnly": c.get("httpOnly", False),
        "secure": c.get("secure", False),
    }
    same_site = c.get("sameSite")
    if same_site in ("Strict", "Lax", "None"):
        pc["sameSite"] = same_site
    exp = c.get("expires", -1)
    if exp and exp > 0:
        pc["expires"] = exp
    pw_cookies.append(pc)

json.dump(pw_cookies, open("/tmp/expireddomains_cookies.json", "w"), indent=2)
print(f"wrote {len(pw_cookies)} cookies to /tmp/expireddomains_cookies.json")
