#!/usr/bin/env bash
# One-shot root install for mihomo + systemd + proxy toggle.
# Run:  SUB_URL="https://example.com/sub?token=xxx" sudo bash install.sh
set -euo pipefail

: "${SUB_URL:?set SUB_URL to your subscription url (e.g. SUB_URL=... sudo bash install.sh)}"

echo "[1/7] installing mihomo binary..."
install -m 755 /tmp/mihomo /usr/local/bin/mihomo

echo "[2/7] setting up /etc/mihomo..."
mkdir -p /etc/mihomo
cp update.py /etc/mihomo/update.py
chmod 644 /etc/mihomo/update.py
install -m 600 /dev/null /etc/mihomo/sub-url
echo "$SUB_URL" > /etc/mihomo/sub-url

echo "[3/7] generating config (fetch subscription, drop HK nodes)..."
python3 /etc/mihomo/update.py
/usr/local/bin/mihomo -t -f /etc/mihomo/config.yaml 2>&1 | tail -1

echo "[4/7] installing systemd unit..."
install -m 644 /home/yang/mihomo-setup/mihomo.service /etc/systemd/system/mihomo.service
systemctl daemon-reload

echo "[5/7] installing proxy toggle script..."
install -m 755 /home/yang/mihomo-setup/proxy /usr/local/bin/proxy

echo "[6/7] installing sudoers NOPASSWD rule..."
install -m 440 /home/yang/mihomo-setup/proxy-nopasswd /etc/sudoers.d/proxy-nopasswd
visudo -cf /etc/sudoers.d/proxy-nopasswd

echo "[7/7] enabling mihomo at boot..."
systemctl enable mihomo.service

echo
echo "INSTALL OK. Now: sudo -n /usr/local/bin/proxy on"
