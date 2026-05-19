# GVS Certificate Verifier

Verifies Malaysian SPM (Sijil Pelajaran Malaysia) certificates via [gvs.moe.gov.my](https://gvs.moe.gov.my).

Extracts the QR hash and Angka Giliran from a certificate image or PDF, then verifies against the official government portal.

---

## VPS Deployment (Docker)

> **Important:** Use a **Malaysia or Singapore** based VPS. US/Europe servers may be blocked by `gvs.moe.gov.my`.

### 1. SSH into your VPS

```bash
ssh root@YOUR_VPS_IP
```

### 2. Install Docker and Git

```bash
curl -fsSL https://get.docker.com | sh
apt install -y git
```

### 3. Clone the repo

```bash
git clone https://github.com/sreeekanthj57/gvs-verify.git
cd gvs-verify
```

### 4. Set your API keys

```bash
echo "OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxx" > .env
echo "APP_API_KEY=your-secret-key-here" >> .env
```

- `OPENROUTER_API_KEY` — get one at [openrouter.ai](https://openrouter.ai) (only needed for image files, PDFs work without it)
- `APP_API_KEY` — choose any secret string to protect your API (e.g. `mysecret123`)

Verify both are saved:
```bash
cat .env
```

Expected output:
```
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxx
APP_API_KEY=your-secret-key-here
```

### 5. Build and run

```bash
docker compose up -d --build
```

App is live at `http://YOUR_VPS_IP`

---

## Managing the App

| Action | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Restart | `docker compose restart` |
| View logs | `docker compose logs -f` |
| Update to latest | `git pull && docker compose up -d --build` |

### Pull latest changes

SSH into your VPS and run:

```bash
cd gvs-verify
git pull
docker compose up -d --build
```

---

## SSL (HTTPS) — After pointing a domain to your VPS

Once your domain DNS is pointed to your VPS IP:

### 1. Install Nginx and Certbot

```bash
apt install -y nginx certbot python3-certbot-nginx
```

### 2. Create Nginx config

```bash
nano /etc/nginx/sites-available/gvs-verify
```

Paste this (replace `yourdomain.com`):

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

### 3. Enable and reload Nginx

```bash
ln -s /etc/nginx/sites-available/gvs-verify /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 4. Get SSL certificate

```bash
certbot --nginx -d yourdomain.com
```

Follow the prompts — Certbot installs the cert and updates Nginx automatically.

App is now live at `https://yourdomain.com`

> SSL renews automatically. To test renewal: `certbot renew --dry-run`

---

## API Endpoints

All endpoints require the `X-API-Key` header.

### `POST /verify` — Verify via URL

```bash
curl -X POST https://yourdomain.com/verify \
  -H "X-API-Key: your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/certificate.pdf"}'
```

### `POST /upload` — Verify via file upload

```bash
curl -X POST https://yourdomain.com/upload \
  -H "X-API-Key: your-secret-key-here" \
  -F "file=@certificate.pdf"
```

### `POST /bulk` — Bulk verify from CSV/XLSX

```bash
curl -X POST https://yourdomain.com/bulk \
  -H "X-API-Key: your-secret-key-here" \
  -F "file=@urls.csv"
```

Upload a CSV or XLSX file with a `url` column. Results stream back live via SSE.

### API Docs

Visit `https://yourdomain.com/docs` for interactive Swagger documentation.

---

## Example Response

```json
{
  "verified": true,
  "name": "DUMMY NAME BIN DUMMY",
  "ic": "000000-00-0000",
  "ag": "XX000X000",
  "school": "SMK DUMMY SCHOOL",
  "year": "2024",
  "cert_no": "00000000000",
  "stats": {
    "total_subjects": 9,
    "passed": 8,
    "failed": 1,
    "pass_rate_pct": 89,
    "grade_distribution": {"A+": 2, "A": 3, "B+": 2, "C": 1, "G": 1}
  },
  "subjects": [
    {"code": "1103", "subject": "BAHASA MELAYU", "grade": "A", "description": "CEMERLANG TINGGI"},
    {"code": "1249", "subject": "MATEMATIK", "grade": "A+", "description": "CEMERLANG TERTINGGI"},
    {"code": "1119", "subject": "BAHASA INGGERIS", "grade": "B+", "description": "KEPUJIAN TINGGI"}
  ]
}
```

---

## Minimum VPS Requirements

| Spec | Minimum |
|---|---|
| CPU | 1 vCPU |
| RAM | 1 GB |
| Storage | 10 GB |
| OS | Ubuntu 22.04 / 24.04 |

> **Important:** Use a **Malaysia or Singapore** VPS — the server must reach `gvs.moe.gov.my`. US/Europe providers are often blocked by the Malaysian government portal.

---

## Tech Stack

- **FastAPI + uvicorn** — web server
- **OpenCV + zxing-cpp** — QR code scanning (7-strategy pipeline)
- **PyMuPDF (fitz)** — PDF rendering and text extraction
- **Gemini 2.5 Flash Lite** (via OpenRouter) — OCR fallback for Angka Giliran
- **curl** — HTTP requests to gvs.moe.gov.my
