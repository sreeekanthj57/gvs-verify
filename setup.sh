#!/bin/bash
# GVS Verifier — VPS Setup Script
# Tested on Ubuntu 22.04 / 24.04
# Usage: bash setup.sh

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_USER="$(whoami)"
SERVICE_NAME="gvs-verifier"
PORT=8000

echo ""
echo "========================================"
echo "  GVS Verifier — Setup"
echo "  Directory : $APP_DIR"
echo "  User      : $APP_USER"
echo "========================================"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-full \
    python3-venv \
    python3-pip \
    nginx \
    curl \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    unzip

echo "      Done."

# ── 2. Python virtual environment ─────────────────────────────────────────────
echo "[2/6] Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
source "$APP_DIR/venv/bin/activate"
pip install --upgrade pip --quiet
echo "      Done."

# ── 3. Python dependencies ────────────────────────────────────────────────────
echo "[3/6] Installing Python dependencies (this may take a minute)..."
pip install -r "$APP_DIR/requirements.txt" --quiet
echo "      Done."

# ── 4. Environment file ───────────────────────────────────────────────────────
echo "[4/6] Setting up .env file..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "  >>> Enter your OpenRouter API key (get one at https://openrouter.ai):"
    read -r -p "  OPENROUTER_API_KEY: " API_KEY
    sed -i "s|your-openrouter-api-key-here|$API_KEY|" "$APP_DIR/.env"
    echo "      .env created."
else
    echo "      .env already exists, skipping."
fi

# ── 5. systemd service ────────────────────────────────────────────────────────
echo "[5/6] Creating systemd service..."
sudo tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null <<EOF
[Unit]
Description=GVS Verifier FastAPI
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/uvicorn api:app --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME --quiet
sudo systemctl restart $SERVICE_NAME
echo "      Service started."

# ── 6. Nginx ──────────────────────────────────────────────────────────────────
echo "[6/6] Configuring Nginx..."

# Get server IP for display
SERVER_IP=$(hostname -I | awk '{print $1}')

sudo tee /etc/nginx/sites-available/$SERVICE_NAME > /dev/null <<EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
EOF

# Remove default site if present
sudo rm -f /etc/nginx/sites-enabled/default

# Enable our site
sudo ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/$SERVICE_NAME

sudo nginx -t
sudo systemctl reload nginx
echo "      Nginx configured."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Setup complete!"
echo ""
echo "  App URL : http://$SERVER_IP"
echo "  API Docs: http://$SERVER_IP/docs"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME   # check status"
echo "    sudo journalctl -u $SERVICE_NAME -f   # live logs"
echo "    sudo systemctl restart $SERVICE_NAME  # restart"
echo "========================================"
echo ""
