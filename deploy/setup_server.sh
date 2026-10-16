#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server for TradeX.
# Run it as the normal "ubuntu" user from inside the cloned repo:
#   cd ~/tradex && bash deploy/setup_server.sh
# Safe to run again: each step skips what is already done.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
ME="$(whoami)"

step() { printf '\n==> %s\n' "$1"; }

step "Setting the clock to UTC (TradeX stores UTC and converts to IST itself)"
sudo timedatectl set-timezone UTC

step "Adding 2 GB of swap (extra memory on disk) so a 1 GB server doesn't run out during builds"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

step "Installing Docker"
if ! command -v docker >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y -q docker.io docker-compose-v2
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$ME"

step "Turning on automatic security updates"
sudo apt-get install -y -q unattended-upgrades >/dev/null

step "Creating .env (your settings and secrets; readable only by you)"
if [[ ! -f .env ]]; then
  cp .env.example .env
  pw="$(openssl rand -hex 24)"
  {
    echo
    echo "# --- Server only (added by deploy/setup_server.sh) ---"
    echo "POSTGRES_PASSWORD=$pw"
  } >> .env
  # On the server the browser reaches the API through the same address, so CORS isn't needed.
  sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=|' .env
fi
chmod 600 .env
mkdir -p reports

step "Scheduling auto-update (every 5 min) and the nightly backup (16:30 IST)"
sudo tee /etc/systemd/system/tradex-update.service >/dev/null <<UNIT
[Unit]
Description=Pull and deploy the latest TradeX main branch
[Service]
Type=oneshot
User=$ME
ExecStart=$REPO/deploy/update.sh
UNIT
sudo tee /etc/systemd/system/tradex-update.timer >/dev/null <<UNIT
[Unit]
Description=Check GitHub for TradeX updates every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT
sudo tee /etc/systemd/system/tradex-backup.service >/dev/null <<UNIT
[Unit]
Description=Back up the TradeX database
[Service]
Type=oneshot
User=$ME
ExecStart=$REPO/deploy/backup.sh
UNIT
sudo tee /etc/systemd/system/tradex-backup.timer >/dev/null <<UNIT
[Unit]
Description=Back up the TradeX database every evening
[Timer]
OnCalendar=*-*-* 11:00:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now tradex-update.timer tradex-backup.timer

step "Installing Tailscale (private access to the dashboard)"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

cat <<NEXT

Setup finished. Next (see docs/05_CLOUD_SERVER_SETUP.md):
  1. sudo tailscale up --hostname tradex      (open the link it prints and log in)
  2. nano $REPO/.env                          (add your Groww and Telegram keys, then Ctrl+O, Enter, Ctrl+X)
  3. Log out and back in once, so Docker works without sudo
  4. $REPO/deploy/tradex.sh start
NEXT
