#!/usr/bin/env bash
# Knowledge OS — instalace / aktualizace na VPS (Ubuntu 24.04). Spouštět jako root:
#     bash install_vps.sh <github-uzivatel>/<repozitar>
# Idempotentní: opakované spuštění = aktualizace (nic nesmaže, .env ani data nepřepíše).
#
# Co udělá:
#   1. balíčky (python3-venv, ffmpeg, git, deno pro yt-dlp), uživatel `kos`
#   2. SSH deploy klíč pro GitHub (přístup jen k tomuto repozitáři) a klon vaultu
#   3. Python venv + závislosti, .env s klíčem Gemini (z /root/.env, nebo se zeptá)
#   4. systemd: kos-web (127.0.0.1:8080 — zvenku nedostupné), kos-worker, automatický restart po novém kódu,
#      týdenní aktualizace yt-dlp
#   5. Tailscale + `tailscale serve` → HTTPS adresa jen v soukromé síti (telefon, PC1, PC2)
set -euo pipefail

# Název vašeho repozitáře (fork / kopie knowledge-os): první parametr nebo proměnná REPO_NAME
REPO_NAME="${1:-${REPO_NAME:-}}"
[ -n "${REPO_NAME}" ] || { echo "Použití: bash install_vps.sh <github-uzivatel>/<repozitar>"; exit 1; }
REPO_SSH="git@github.com:${REPO_NAME}.git"
KOS_USER="kos"
KOS_HOME="/home/${KOS_USER}"
VAULT="${KOS_HOME}/vault"
PORT=8080

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "Spusťte jako root (sudo bash install_vps.sh)."; exit 1; }

say "1/5 Systémové balíčky"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl ca-certificates unzip sqlite3 \
  libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core >/dev/null
if ! command -v deno >/dev/null; then      # yt-dlp potřebuje JS runtime pro YouTube
  curl -fsSL -o /tmp/deno.zip https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -o -q /tmp/deno.zip -d /usr/local/bin && chmod +x /usr/local/bin/deno || echo "(deno se nepodařilo nainstalovat — nevadí, jen yt-dlp může být méně spolehlivý)"
fi
id "${KOS_USER}" >/dev/null 2>&1 || useradd -m -s /bin/bash "${KOS_USER}"
# swap 2 GB (4 GB RAM je málo při souběhu webu, ffmpeg a analýzy)
if ! swapon --show | grep -q /swapfile; then
  [ -f /swapfile ] || { fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null; }
  swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "2/5 Přístup ke GitHubu (deploy klíč jen pro repozitář ${REPO_NAME})"
KEY="${KOS_HOME}/.ssh/id_ed25519"
if [ ! -f "${KEY}" ]; then
  runuser -u "${KOS_USER}" -- mkdir -p "${KOS_HOME}/.ssh"
  runuser -u "${KOS_USER}" -- ssh-keygen -q -t ed25519 -N "" -C "kos-vps" -f "${KEY}"
fi
runuser -u "${KOS_USER}" -- bash -c "ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null; sort -u -o ~/.ssh/known_hosts ~/.ssh/known_hosts"
cp "${KEY}.pub" /root/kos_deploy.pub
until runuser -u "${KOS_USER}" -- git ls-remote "${REPO_SSH}" >/dev/null 2>&1; do
  if [ ! -t 0 ]; then   # spuštěno asistentem (bez terminálu): klíč přidá přes gh a spustí skript znovu
    echo "DEPLOY_KEY_NEEDED $(cat "${KEY}.pub")"
    exit 10
  fi
  echo
  echo "VPS zatím nemá přístup k repozitáři. Přidejte mu tento klíč (s právem zápisu):"
  echo
  cat "${KEY}.pub"
  echo
  echo "  A) Na PC v PowerShellu (gh je přihlášené) — jeden příkaz:"
  echo "     ssh root@$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | cut -d' ' -f1) cat ${KEY}.pub | gh repo deploy-key add - --allow-write -R ${REPO_NAME} -t kos-vps"
  echo "  B) nebo ručně: github.com/${REPO_NAME} → Settings → Deploy keys → Add deploy key,"
  echo "     vložit řádek výše, zaškrtnout 'Allow write access'."
  echo
  read -r -p "Až bude klíč přidaný, stiskněte Enter… " _
done
if [ ! -d "${VAULT}/.git" ]; then
  runuser -u "${KOS_USER}" -- git clone -q "${REPO_SSH}" "${VAULT}"
else
  runuser -u "${KOS_USER}" -- git -C "${VAULT}" pull -q --rebase --autostash || echo "(git pull selhal — pokračuji s aktuální verzí)"
fi
runuser -u "${KOS_USER}" -- git -C "${VAULT}" config user.name "Knowledge OS (VPS)"
runuser -u "${KOS_USER}" -- git -C "${VAULT}" config user.email "kos-vps@localhost"
runuser -u "${KOS_USER}" -- git -C "${VAULT}" config pull.rebase true

say "3/5 Python prostředí a klíče k AI modelům"
[ -d "${KOS_HOME}/venv" ] || runuser -u "${KOS_USER}" -- python3 -m venv "${KOS_HOME}/venv"
runuser -u "${KOS_USER}" -- "${KOS_HOME}/venv/bin/pip" install -q -U pip
runuser -u "${KOS_USER}" -- "${KOS_HOME}/venv/bin/pip" install -q -r "${VAULT}/requirements.txt"
runuser -u "${KOS_USER}" -- "${KOS_HOME}/venv/bin/pip" install -q -U "yt-dlp[default]" || true
if [ ! -f "${VAULT}/.env" ]; then
  if [ -f /root/.env ]; then
    cp /root/.env "${VAULT}/.env"
  else
    [ -t 0 ] || { echo "ENV_NEEDED: chybí /root/.env s OPENROUTER_API_KEY"; exit 11; }
    read -r -s -p "Vložte OPENROUTER_API_KEY (nezobrazí se): " OK; echo
    read -r -s -p "Vložte GEMINI_API_KEY (nepovinné, Enter = přeskočit): " GK; echo
    printf 'OPENROUTER_API_KEY=%s\nGEMINI_API_KEY=%s\n' "${OK}" "${GK}" > "${VAULT}/.env"
  fi
fi
chown "${KOS_USER}:${KOS_USER}" "${VAULT}/.env"; chmod 600 "${VAULT}/.env"
runuser -u "${KOS_USER}" -- bash -c "cd '${VAULT}' && KOS_ROOT='${VAULT}' '${KOS_HOME}/venv/bin/python' -m pipeline reindex" || true

say "4/5 Služby systemd"
cat > /etc/systemd/system/kos-web.service <<UNIT
[Unit]
Description=Knowledge OS — webová aplikace (jen 127.0.0.1, ven přes tailscale serve)
After=network-online.target
Wants=network-online.target
[Service]
User=${KOS_USER}
WorkingDirectory=${VAULT}
Environment=KOS_ROOT=${VAULT} PYTHONUNBUFFERED=1 TZ=Europe/Prague
EnvironmentFile=-${VAULT}/.env
ExecStart=${KOS_HOME}/venv/bin/python -m pipeline web --host 127.0.0.1 --port ${PORT}
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/kos-worker.service <<UNIT
[Unit]
Description=Knowledge OS — worker (hlas, fronta YouTube, index, git)
After=network-online.target
Wants=network-online.target
[Service]
User=${KOS_USER}
WorkingDirectory=${VAULT}
Environment=KOS_ROOT=${VAULT} PYTHONUNBUFFERED=1 TZ=Europe/Prague PATH=/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-${VAULT}/.env
ExecStart=${KOS_HOME}/venv/bin/python -m pipeline worker
Restart=always
RestartSec=10
Nice=5
[Install]
WantedBy=multi-user.target
UNIT
# worker po `git pull` s novým kódem zapíše 99_System/logs/restart_request → reinstalace závislostí + restart
runuser -u "${KOS_USER}" -- mkdir -p "${VAULT}/99_System/logs"
cat > /etc/systemd/system/kos-restart.path <<UNIT
[Unit]
Description=Knowledge OS — restart po aktualizaci kódu
[Path]
PathChanged=${VAULT}/99_System/logs/restart_request
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/kos-restart.service <<UNIT
[Unit]
Description=Knowledge OS — reinstalace závislostí a restart služeb
[Service]
Type=oneshot
ExecStart=/bin/bash ${VAULT}/deploy/restart.sh
UNIT
cat > /etc/systemd/system/kos-ytdlp.service <<UNIT
[Unit]
Description=Knowledge OS — aktualizace yt-dlp (YouTube se často mění)
[Service]
Type=oneshot
User=${KOS_USER}
ExecStart=${KOS_HOME}/venv/bin/pip install -q -U yt-dlp[default]
UNIT
cat > /etc/systemd/system/kos-ytdlp.timer <<UNIT
[Unit]
Description=Knowledge OS — týdenní aktualizace yt-dlp
[Timer]
OnCalendar=Mon 04:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now kos-web kos-worker kos-restart.path kos-ytdlp.timer >/dev/null
systemctl restart kos-web kos-worker
sleep 3
curl -fsS "http://127.0.0.1:${PORT}/api/status" >/dev/null && echo "Web běží (lokálně na portu ${PORT})." || { echo "Web neodpovídá:"; journalctl -u kos-web -n 30 --no-pager; }

say "5/5 Tailscale (soukromá síť: telefon, PC)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! tailscale status >/dev/null 2>&1; then
  if [ -t 0 ]; then
    echo "Otevřete odkaz, který se teď objeví, a přihlaste se STEJNÝM účtem jako v Tailscale na telefonu a PC:"
    tailscale up --hostname=kos
  else
    nohup tailscale up --hostname=kos > /root/tailscale_up.log 2>&1 &
    sleep 8
    echo "TAILSCALE_LOGIN $(grep -o 'https://login.tailscale.com/[^ ]*' /root/tailscale_up.log | head -1)"
    echo "Po přihlášení spusťte skript znovu (dokončí tailscale serve)."
    exit 12
  fi
fi
timeout 30 tailscale serve --bg ${PORT} </dev/null || {
  echo
  echo "Tailscale serve potřebuje v administraci zapnuté HTTPS: https://login.tailscale.com/admin/dns"
  echo "(MagicDNS = zapnuto, HTTPS Certificates = Enable). Pak spusťte znovu:  tailscale serve --bg ${PORT}"
}
URL="$(tailscale serve status 2>/dev/null | grep -o 'https://[^ ]*' | head -1 || true)"
say "Hotovo"
echo "Aplikace:   ${URL:-https://kos.<vaše-tailnet>.ts.net}   (funguje jen v zařízeních přihlášených do Tailscale)"
echo "Stav:       systemctl status kos-web kos-worker     Logy: journalctl -u kos-worker -f"
echo "Aktualizace kódu probíhá sama (worker stahuje z GitHubu každých 5 min a po změně kódu se restartuje)."
