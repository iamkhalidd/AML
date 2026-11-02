#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 VM as the staging host. Run once, as root, on the server:
#   sudo bash provision.sh <deploy-user> "<ssh public key>"
# Safe to run again. Hosting provider is not yet chosen (ADR-002); this assumes a plain VM.
set -euo pipefail

DEPLOY_USER="${1:?usage: provision.sh <deploy-user> \"<ssh public key>\"}"
SSH_KEY="${2:?usage: provision.sh <deploy-user> \"<ssh public key>\"}"
APP_DIR=/opt/aml

if [[ $EUID -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl ufw unattended-upgrades fail2ban

# Docker Engine from Docker's own repository.
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# Deploy user: key-only login, may run docker, no password.
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
AUTH_KEYS="/home/$DEPLOY_USER/.ssh/authorized_keys"
touch "$AUTH_KEYS"
grep -qxF "$SSH_KEY" "$AUTH_KEYS" || echo "$SSH_KEY" >> "$AUTH_KEYS"
chown "$DEPLOY_USER:$DEPLOY_USER" "$AUTH_KEYS"
chmod 0600 "$AUTH_KEYS"

# SSH: no root login, no passwords.
cat > /etc/ssh/sshd_config.d/10-aml.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
systemctl reload ssh

# Default-deny firewall; only SSH and the edge proxy are reachable.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Security updates install themselves; the monthly patch routine covers the rest.
dpkg-reconfigure -f noninteractive unattended-upgrades

install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
echo "Staging host ready. Deploy with deploy/staging/deploy.sh from your machine."
