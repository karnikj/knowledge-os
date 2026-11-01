#!/usr/bin/env bash
# Rychlá kontrola VPS pro asistenta: služby, disk, paměť, stav workeru, poslední chyby.
V=/home/kos/vault
echo "== služby";   systemctl is-active kos-web kos-worker kos-restart.path tailscaled 2>&1 | paste -sd' '
echo "== disk";     df -h / | tail -1
echo "== paměť";    free -m | awk 'NR<=3'
echo "== web";      curl -fsS -m 5 http://127.0.0.1:8080/api/status || echo "web neodpovídá"
echo; echo "== git";  runuser -u kos -- git -C $V log --oneline -3 2>&1; runuser -u kos -- git -C $V status --short | head -5
echo "== tailscale"; tailscale serve status 2>&1 | head -5
echo "== chyby workeru (24 h)"; journalctl -u kos-worker --since "24 hours ago" -p warning --no-pager 2>/dev/null | tail -15
echo "== aktualizace systému"; apt list --upgradable 2>/dev/null | tail -n +2 | wc -l
