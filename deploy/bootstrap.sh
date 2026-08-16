#!/usr/bin/env bash
set -euo pipefail

if ! id memlo >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin memlo
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv ca-certificates

install -d -o memlo -g memlo -m 0750 /home/memlo/app
install -d -o memlo -g memlo -m 0750 /home/memlo/backups
install -o memlo -g memlo -m 0644 /tmp/memlo-deploy/bot.py /home/memlo/app/bot.py
install -o memlo -g memlo -m 0644 /tmp/memlo-deploy/requirements.txt /home/memlo/app/requirements.txt
install -o root -g root -m 0644 /tmp/memlo-deploy/memlo.service /etc/systemd/system/memlo.service

if [ ! -x /home/memlo/app/.venv/bin/python ]; then
  sudo -u memlo python3 -m venv /home/memlo/app/.venv
fi
sudo -u memlo /home/memlo/app/.venv/bin/pip install --disable-pip-version-check -r /home/memlo/app/requirements.txt

systemctl daemon-reload
systemctl enable memlo.service
