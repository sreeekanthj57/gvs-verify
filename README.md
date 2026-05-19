# GVS Certificate Verifier

Verifies Malaysian SPM (Sijil Pelajaran Malaysia) certificates via [gvs.moe.gov.my](https://gvs.moe.gov.my).

Extracts the QR hash and Angka Giliran from a certificate image or PDF, then verifies against the official government portal.

---

## VPS Deployment (Docker)

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

### 4. Set your API key

```bash
cp .env.example .env
nano .env
```

Add your key:
```
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxx
```

Get a key at [openrouter.ai](https://openrouter.ai) — only needed for image files (PDFs work without it).

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

---

## API Key

This app uses [OpenRouter](https://openrouter.ai) to access **Gemini 2.5 Flash Lite** for OCR fallback (reading Angka Giliran from image certificates).

- PDFs work **without** the API key (text extracted directly)
- Image files (JPG, PNG) **require** the API key

---

## API Endpoints

### `POST /verify` — Verify via URL
```bash
curl -X POST http://YOUR_VPS_IP/verify \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/certificate.pdf"}'
```

### `POST /upload` — Verify via file upload
```bash
curl -X POST http://YOUR_VPS_IP/upload \
  -F "file=@certificate.pdf"
```

### `POST /bulk` — Bulk verify from CSV/XLSX
Upload a CSV or XLSX file with a `url` column. Results stream back live via SSE.

### API Docs
Visit `http://YOUR_VPS_IP/docs` for interactive Swagger documentation.

---

## Example Response

```json
{
  "verified": true,
  "name": "AHMAD BIN ALI",
  "ic": "050101-14-1234",
  "ag": "PF001A004",
  "school": "SMK CONTOH",
  "year": "2024",
  "cert_no": "24011234567",
  "stats": {
    "total_subjects": 9,
    "passed": 8,
    "failed": 1,
    "pass_rate_pct": 89,
    "grade_distribution": {"A+": 2, "A": 3, "B+": 2, "C": 1, "G": 1}
  },
  "subjects": [
    {"code": "1103", "subject": "BAHASA MELAYU", "grade": "A", "description": "CEMERLANG TINGGI"}
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

> **Important:** The server must be able to reach `gvs.moe.gov.my`. Use a **Singapore or Southeast Asia** VPS — US/Europe providers may be blocked by the Malaysian government portal.

---

## Tech Stack

- **FastAPI + uvicorn** — web server
- **OpenCV + zxing-cpp** — QR code scanning (7-strategy pipeline)
- **PyMuPDF (fitz)** — PDF rendering and text extraction
- **Gemini 2.5 Flash Lite** (via OpenRouter) — OCR fallback for Angka Giliran
- **curl** — HTTP requests to gvs.moe.gov.my
