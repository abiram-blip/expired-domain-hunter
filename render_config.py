#!/usr/bin/env python3
"""Render config.json from config.template.json + environment variables.
Run this before every pipeline invocation (run_hunt.sh does this automatically).
Secrets (VT_API_KEY, SHEET_WEBAPP_URL, SHEET_WEBAPP_TOKEN, SLACK_WEBHOOK_URL) must
be set in the environment (e.g. via `.env` + `set -a; source .env; set +a`).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "config.template.json")
OUT = os.path.join(HERE, "config.json")

_PLACEHOLDER = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")


def render(value):
    if isinstance(value, str):
        m = _PLACEHOLDER.match(value)
        if m:
            var = m.group(1)
            val = os.environ.get(var)
            if val is None:
                sys.exit(f"render_config: missing required env var {var}")
            return val
        return value
    if isinstance(value, dict):
        return {k: render(v) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v) for v in value]
    return value


def main():
    template = json.load(open(TEMPLATE))
    rendered = render(template)
    with open(OUT, "w") as f:
        json.dump(rendered, f, indent=2)
    print(f"rendered {OUT} from {TEMPLATE}")


if __name__ == "__main__":
    main()
