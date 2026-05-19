# GVS Certificate Verifier

Verifies Malaysian SPM (Sijil Pelajaran Malaysia) certificates via [gvs.moe.gov.my](https://gvs.moe.gov.my).

Extracts the QR hash and Angka Giliran from a certificate image or PDF, then verifies the result against the official government portal.

---

## Quick Start (without Docker)

**Requirements:** Python 3.11+, `curl` installed, Linux/macOS recommended (Windows works too)

```bash
# 1. Clone or copy this folder to your server
git clone <repo-url>
cd gvs-verifier

# 2. Install system dependencies (Linux)
sudo apt-get install -y libgl1 libglib2.0-0 curl

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set your API key
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY

# 5. Run the server
uvicorn api:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

---

## Quick Start (with Docker)

```bash
# Build
docker build -t gvs-verifier .

# Run
docker run -d --name gvs-verifier -p 8000:8000 \
  -e OPENROUTER_API_KEY=your-key-here \
  gvs-verifier
```

Open http://localhost:8000 in your browser.

---

## API Key

This app uses [OpenRouter](https://openrouter.ai) to access **Gemini 2.5 Flash Lite** for OCR fallback (reading the Angka Giliran from image certificates).

1. Sign up at https://openrouter.ai
2. Create an API key
3. Add credits (very small usage — a few USD for thousands of certificates)
4. Put the key in your `.env` file as `OPENROUTER_API_KEY=sk-or-v1-...`

> PDFs work without the API key (text is extracted directly). The API key is only needed for image files where the Angka Giliran cannot be read as text.

---

## API Endpoints

### `POST /verify` — Verify via URL
```bash
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/certificate.pdf"}'
```

### `POST /upload` — Verify via file upload
```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@certificate.pdf"
```

### `POST /bulk` — Bulk verify from CSV/XLSX
Upload a CSV or XLSX file with a `url` column. Results stream back live via SSE.

### API Docs
Visit http://localhost:8000/docs for interactive Swagger documentation.

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

## CLI Usage

```bash
# Single certificate
python gvs_cli.py https://example.com/certificate.pdf

# Bulk from CSV
python gvs_cli.py --file urls.csv --out results.xlsx
```

---

## Tech Stack

- **FastAPI + uvicorn** — web server
- **OpenCV + zxing-cpp** — QR code scanning (7-strategy pipeline)
- **PyMuPDF (fitz)** — PDF rendering and text extraction
- **Gemini 2.5 Flash Lite** (via OpenRouter) — OCR fallback for Angka Giliran
- **curl** — HTTP requests to gvs.moe.gov.my (handles ASP.NET session/cookie flow)

---

## Hosting Notes

- The server needs to be able to reach `gvs.moe.gov.my`. Some cloud providers (US-based) may be blocked by the Malaysian government portal.
- Singapore or Southeast Asia VPS providers typically work well.
- Minimum server: 1 vCPU, 1GB RAM.
