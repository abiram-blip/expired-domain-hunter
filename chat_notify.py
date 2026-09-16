"""Best-effort, idempotent Google Chat notifications using only the standard library."""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def chat_text(text):
    """Retain Slack-style link labels and destinations in plain Chat text."""
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", lambda m: f"{m[2].strip('*')} ({m[1]})", text)
    return text.replace(":rotating_light:", "🚨")


def post_chat(text, *, event_key=None, url=None):
    url = url or os.environ.get("GCHAT_DOMAIN_HUNT_WEBHOOK_URL")
    if not url:
        print("Google Chat webhook is not configured; notification skipped", file=sys.stderr)
        return False
    text = chat_text(text)
    day = os.environ.get("EDH_RUN_DATE") or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    identity = ["domain-hunt-v1", event_key] if event_key else ["domain-hunt-v1", day, text]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:56]
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if k not in ("messageId", "requestId")]
        query.append(("messageId", "client-" + digest))
        endpoint = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
        request = urllib.request.Request(endpoint, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json; charset=UTF-8"}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        if error.code == 409:
            try:
                if json.loads(error.read()).get("error", {}).get("status") == "ALREADY_EXISTS":
                    return True
            except (ValueError, AttributeError):
                pass
        print(f"Google Chat notification failed (HTTP {error.code})", file=sys.stderr)
    except Exception as error:
        # Exception messages can contain the authenticated webhook URL. Never print them.
        print(f"Google Chat notification failed ({type(error).__name__})", file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    parser.add_argument("--event-key")
    args = parser.parse_args()
    # Notifications must never reverse delivery or mask the pipeline's outcome.
    post_chat(args.text, event_key=args.event_key)


if __name__ == "__main__":
    main()
