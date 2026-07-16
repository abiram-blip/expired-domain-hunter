#!/bin/bash
# One-time provisioning script for a fresh Oracle Cloud "Always Free" VM (Ubuntu).
# Run this once after `git clone`-ing the repo onto the VM. Not run by cron itself.
set -euo pipefail

echo "== apt packages =="
sudo apt-get update
sudo apt-get install -y python3 python3-pip git curl chromium-browser cron

echo "== Node.js (LTS) for the claude CLI =="
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs

echo "== claude CLI =="
# Same install method used on the Mac — verify this matches your local install if it
# has changed (https://docs.claude.com/en/docs/claude-code for the current instructions).
npm install -g @anthropic-ai/claude-code

echo "== .env =="
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example — fill in real secrets before the first run:"
  echo "  VT_API_KEY, SHEET_WEBAPP_URL, SHEET_WEBAPP_TOKEN, SLACK_WEBHOOK_URL,"
  echo "  ANTHROPIC_API_KEY, and (optional but recommended) HEALTHCHECK_URL."
fi

echo "== headless Chrome profile dir =="
mkdir -p "$HOME/domain-hunter-chrome-profile"

echo "Next steps (manual, not automated by this script):"
echo "1. Fill in .env with real secrets."
echo "2. Install the systemd service for headless Chrome: see chromehead.service"
echo "   (sudo cp chromehead.service /etc/systemd/system/ && sudo systemctl enable --now chromehead)"
echo "3. One-time expireddomains.net login bootstrap: either open a VNC session to this"
echo "   VM and log in through the headless Chrome's remote-debugging port with a local"
echo "   browser pointed at it, or copy the Cookies file from the existing Mac dedicated"
echo "   Chrome profile into ~/domain-hunter-chrome-profile/Default/Cookies."
echo "4. Add the cron entries: crontab -e, then add the two lines from cron.example."
echo "5. Do a manual end-to-end test run before relying on cron:"
echo "   ./run_hunt_vm.sh"
