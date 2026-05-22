#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/gunchinam/canslim-quant-scanner.git}"
APP_NAME="${APP_NAME:-canslim-quant-scanner}"
SERVICE_NAME="${SERVICE_NAME:-scanner}"
PORT="${PORT:-5000}"
WORKER_CLASS="${WORKER_CLASS:-geventwebsocket.gunicorn.workers.GeventWebSocketWorker}"

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as the default cloud user with sudo access, not as root."
  exit 1
fi

OS_ID=""
OS_LIKE=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  OS_ID="${ID:-}"
  OS_LIKE="${ID_LIKE:-}"
fi

HOME_DIR="${HOME:-/home/${USER}}"
APP_DIR="${APP_DIR:-${HOME_DIR}/${APP_NAME}}"

install_packages() {
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf -y update
    sudo dnf -y install python3 python3-pip python3-devel git nginx certbot python3-certbot-nginx
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get upgrade -y
    sudo apt-get install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx
  else
    echo "Unsupported package manager. Install Python, git, nginx, and certbot manually."
    exit 1
  fi
}

echo "==> Updating packages"
install_packages

echo "==> Fetching repository"
mkdir -p "${HOME_DIR}"
cd "${HOME_DIR}"
if [[ -d "${APP_DIR}/.git" ]]; then
  cd "${APP_DIR}"
  git pull origin main
else
  git clone "${REPO_URL}" "${APP_DIR}"
  cd "${APP_DIR}"
fi

echo "==> Creating virtual environment"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Creating local environment file if missing"
if [[ ! -f .env ]]; then
  cat > .env <<'EOF'
FINNHUB_API_KEY=
EOF
  echo "Created .env. Fill in API keys before using live data sources."
fi

echo "==> Writing systemd unit"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=Stock Scanner Web App
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${APP_DIR}
Environment=PATH=${APP_DIR}/venv/bin:/usr/bin
ExecStart=${APP_DIR}/venv/bin/gunicorn --worker-class ${WORKER_CLASS} --workers 1 --threads 4 --bind 127.0.0.1:${PORT} --timeout 120 web_app.app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "==> Writing nginx site"
sudo tee /etc/nginx/sites-available/scanner >/dev/null <<EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/scanner /etc/nginx/sites-enabled/scanner
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

echo "==> Enabling services"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl restart nginx

if command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --permanent --add-service=http || true
  sudo firewall-cmd --permanent --add-port="${PORT}"/tcp || true
  sudo firewall-cmd --reload || true
fi

echo "==> Optional firewall note"
echo "Open TCP 80 in OCI security rules. Also allow 22 from your IP for SSH."

echo "==> Done"
echo "Check status with:"
echo "  sudo systemctl status ${SERVICE_NAME} --no-pager"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo "Then open:"
echo "  http://<PUBLIC_IP>/healthz"
