#!/usr/bin/env bash
# Volá kos-restart.service (root), když worker stáhne nový kód: doinstalovat závislosti a restartovat služby.
set -u
VAULT="$(cd "$(dirname "$0")/.." && pwd)"
runuser -u kos -- /home/kos/venv/bin/pip install -q -r "${VAULT}/requirements.txt" || true
systemctl restart kos-web kos-worker
