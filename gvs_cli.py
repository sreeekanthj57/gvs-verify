#!/usr/bin/env python3
"""
GVS Certificate Verifier CLI
Auto-extracts QR hash and Angka Giliran from image or PDF URLs.

Usage:
    gvs_cli <url>                          single image or PDF URL
    gvs_cli --file urls.csv               CSV with a 'url' column (or one URL per line)
    gvs_cli --file urls.xlsx              XLSX with a 'url' column
    gvs_cli --file urls.csv --out results.xlsx   save results to file

Output flags:
    --out <file>     save results to .json / .csv / .xlsx
    --pretty         pretty-print JSON to terminal (default for single)
"""

import sys
import os
import re
import csv
import json
import time
import tempfile
import argparse
import urllib.request
import urllib.parse
import urllib.error
import subprocess
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Optional progress bar ────────────────────────────────────────────────────
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── Optional XLSX support ────────────────────────────────────────────────────
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# ── Config ───────────────────────────────────────────────────────────────────
REQUEST_DELAY    = 0.6
FETCH_TIMEOUT    = 30
GVS_TIMEOUT_GET  = 20
GVS_TIMEOUT_POST = 30
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# OpenRouter / Gemini — set OPENROUTER_API_KEY in your .env file
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GEMINI_MODEL       = "google/gemini-2.5-flash-lite"


# ════════════════════════════════════════════════════════════════════════════
# FILE FETCH
# ════════════════════════════════════════════════════════════════════════════

def fetch_file(url: str) -> tuple[bytes, str]:
    """Download URL or local file:// URI, return (bytes, content_type)."""
    try:
        if url.startswith("file://"):
            path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
            with open(path, "rb") as f:
                data = f.read()
            import mimetypes
            ctype, _ = mimetypes.guess_type(path)
            return data, ctype or ""
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")
    except Exception as e:
        raise RuntimeError(f"fetch failed: {e}")


# ════════════════════════════════════════════════════════════════════════════
# EXTRACTION  (QR hash + Angka Giliran)
# ════════════════════════════════════════════════════════════════════════════

def extract_from_bytes(data: bytes, content_type: str, source_url: str) -> dict:
    """Extract qr_hash and ag from raw file bytes. Returns dict with keys or raises."""
    is_pdf = b"%PDF" in data[:10] or "pdf" in content_type.lower() or source_url.lower().endswith(".pdf")

    with tempfile.NamedTemporaryFile(suffix=".pdf" if is_pdf else ".jpg", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        return _extract_from_file(tmp_path, is_pdf)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _img_to_b64(img_bytes: bytes) -> str:
    import base64
    return base64.b64encode(img_bytes).decode()


def gemini_ocr(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    """
    Send image to Gemini via OpenRouter.
    Returns {"ag": ..., "qr_url": ...} — both may be None if not found.
    """
    if not OPENROUTER_API_KEY:
        return {"ag": None, "qr_url": None, "error": "no api key — set OPENROUTER_API_KEY in .env"}

    prompt = (
        "This is a Malaysian SPM (Sijil Pelajaran Malaysia) certificate image.\n"
        "Extract the ANGKA GILIRAN — it is printed as text on the certificate.\n"
        "Format: 2 uppercase letters + 3 digits + 1 uppercase letter + 3 digits (e.g. PF001A004, NE201A037).\n\n"
        "Reply ONLY with JSON, no markdown:\n"
        "{\"ag\": \"PF001A004\"}\n"
        "Use null if not found. Do NOT guess or invent values."
    )

    payload = json.dumps({
        "model": GEMINI_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{_img_to_b64(image_bytes)}"
                }},
            ],
        }],
        "max_tokens": 100,
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://gvs-verifier.local",
        },
        method="POST",
    )
    import socket
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw  = resp.read()
                data = json.loads(raw)
                if "error" in data:
                    last_err = f"Gemini API error: {data['error'].get('message', data['error'])}"
                    time.sleep(2)
                    continue
                if "choices" not in data or not data["choices"]:
                    last_err = f"Gemini bad response: {raw.decode()[:200]}"
                    time.sleep(2)
                    continue
                text   = data["choices"][0]["message"]["content"].strip()
                text   = re.sub(r"^```[a-z]*\n?|\n?```$", "", text.strip())
                parsed = json.loads(text)
                ag     = parsed.get("ag")
                if ag and not re.match(r"^[A-Z]{2}\d{3}[A-Z]\d{3}$", str(ag).strip().upper()):
                    ag = None
                return {"ag": ag.strip().upper() if ag else None}
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            last_err = f"Gemini API error {e.code}: {e.reason} — {body}"
            time.sleep(2)
        except (socket.timeout, urllib.error.URLError) as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            last_err = f"Gemini timeout (attempt {attempt+1}/3): {reason}"
            time.sleep(2)
        except Exception as e:
            last_err = f"Gemini unexpected error: {str(e)}"
            time.sleep(2)
    return {"ag": None, "error": last_err}


def _extract_from_file(path: str, is_pdf: bool) -> dict:
    import cv2
    import numpy as np
    import zxingcpp

    result = {"qr_hash": None, "ag": None, "qr_raw": None}

    # ── Collect images to scan (PDF → rendered pages, image → direct) ────────
    images = []     # list of (cv2 img, raw bytes, mime)

    if is_pdf:
        try:
            import fitz
            doc = fitz.open(path)
            for page in doc:
                mat = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
                images.append((img, buf.tobytes(), "image/jpeg"))
        except Exception as e:
            result["qr_error"] = str(e)

        # AG from PDF text layer first (fast, free)
        try:
            import fitz
            doc = fitz.open(path)
            text = "".join(p.get_text() for p in doc)
            matches = re.findall(r"\b[A-Z]{2}\d{3}[A-Z]\d{3}\b", text)
            if matches:
                result["ag"] = matches[0]
        except Exception:
            pass

    else:
        raw_bytes = open(path, "rb").read()
        img = cv2.imread(path)
        if img is None:
            result["qr_error"] = "image decode failed"
            return result
        mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
        images.append((img, raw_bytes, mime))

    # ── QR scan with progressive preprocessing ────────────────────────────────
    for img, raw_bytes, mime in images:
        qr = _scan_qr_enhanced(img, zxingcpp)
        if qr:
            result["qr_raw"]  = qr
            result["qr_hash"] = _parse_hash(qr)
            break

    # ── Gemini fallback: if QR or AG still missing, ask Gemini ───────────────
    needs_gemini = (not result["qr_hash"]) or (not result["ag"])
    if needs_gemini and images and OPENROUTER_API_KEY:
        _, raw_bytes, mime = images[0]
        gemini = gemini_ocr(raw_bytes, mime)
        if not result["ag"] and gemini.get("ag"):
            result["ag"]        = gemini["ag"]
            result["ag_source"] = "gemini"
        elif not result["ag"] and gemini.get("error"):
            result["gemini_error"] = gemini["error"]
        # QR hash is NOT taken from Gemini — vision models hallucinate QR data

    return result


def _locate_qr_region(img):
    """
    Ask Gemini where the QR code is in the image.
    Returns (x, y, w, h) as fractions of image size, or None.
    """
    if not OPENROUTER_API_KEY:
        return None
    import base64
    _, buf = __import__("cv2").imencode(".jpg", img, [__import__("cv2").IMWRITE_JPEG_QUALITY, 80])
    b64 = base64.b64encode(buf.tobytes()).decode()
    prompt = (
        "Locate the QR code in this certificate image.\n"
        "Reply ONLY with JSON (no markdown) giving the bounding box as fractions (0.0–1.0) of image dimensions:\n"
        "{\"x\": 0.35, \"y\": 0.82, \"w\": 0.15, \"h\": 0.12}\n"
        "x,y = top-left corner. If no QR code visible, reply {\"x\":null}."
    )
    payload = json.dumps({
        "model": GEMINI_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        "max_tokens": 60,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            text = data["choices"][0]["message"]["content"].strip()
            text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text.strip())
            box  = json.loads(text)
            if box.get("x") is None:
                return None
            return box
    except Exception:
        return None


def _scan_qr_enhanced(img, zxingcpp):
    """Try multiple preprocessing strategies + Gemini-located crop."""
    import cv2
    import numpy as np

    def try_scan(image):
        results = zxingcpp.read_barcodes(image)
        return results[0].text if results else None

    # Strategy 1: original full image
    if r := try_scan(img):
        return r

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Strategy 2: upscale bottom-centre crop (QR usually bottom)
    crop = img[int(h * 0.75):, int(w * 0.25):int(w * 0.75)]
    up   = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    if r := try_scan(up):
        return r

    # Strategy 3: CLAHE contrast enhancement
    clahe    = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    up2      = cv2.resize(enhanced, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    if r := try_scan(up2):
        return r

    # Strategy 4: Otsu binarization (good for low contrast)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    up3     = cv2.resize(otsu, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    if r := try_scan(up3):
        return r

    # Strategy 5: Adaptive threshold on bottom crop
    crop_g   = gray[int(h * 0.75):, int(w * 0.25):int(w * 0.75)]
    adaptive = cv2.adaptiveThreshold(crop_g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 11, 2)
    up4      = cv2.resize(adaptive, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    if r := try_scan(up4):
        return r

    # Strategy 6: Sharpen full image
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp  = cv2.filter2D(gray, -1, kernel)
    up5    = cv2.resize(sharp, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    if r := try_scan(up5):
        return r

    # Strategy 7: Ask Gemini where the QR is → crop exactly → scan
    box = _locate_qr_region(img)
    if box:
        pad = 0.01
        x1  = max(0, int((box["x"] - pad) * w))
        y1  = max(0, int((box["y"] - pad) * h))
        x2  = min(w, int((box["x"] + box["w"] + pad) * w))
        y2  = min(h, int((box["y"] + box["h"] + pad) * h))
        qr_crop = img[y1:y2, x1:x2]
        if qr_crop.size > 0:
            for scale in [6, 8, 10]:
                up6 = cv2.resize(qr_crop, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_NEAREST)
                if r := try_scan(up6):
                    return r
            gray_crop = cv2.cvtColor(qr_crop, cv2.COLOR_BGR2GRAY)
            _, bin_crop = cv2.threshold(gray_crop, 0, 255,
                                        cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            up7 = cv2.resize(bin_crop, None, fx=8, fy=8,
                             interpolation=cv2.INTER_NEAREST)
            if r := try_scan(up7):
                return r

    return None


def _parse_hash(qr_text: str):
    m = re.search(r"/qr/([A-F0-9]{38,44})", qr_text, re.IGNORECASE)
    return m.group(1).upper() if m else None


# ════════════════════════════════════════════════════════════════════════════
# GVS VERIFICATION
# ════════════════════════════════════════════════════════════════════════════

def _curl(cmd: list) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout


def verify_gvs(ag: str, qr_hash: str) -> dict:
    target  = f"https://gvs.moe.gov.my/qr/{qr_hash}"
    cookie  = tempfile.mktemp(suffix=".txt")
    base_cmd = ["curl", "-s", "-k", "-L", "-A", UA]

    html = _curl(base_cmd + ["-m", str(GVS_TIMEOUT_GET), "-c", cookie, target])

    if "Angka Giliran" not in html and "Semakan" not in html:
        return {"verified": False, "error": "gvs_unreachable"}

    vs  = re.search(r'name="__VIEWSTATE"[^>]*value="([^"]*)"', html)
    vsg = re.search(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html)
    ev  = re.search(r'name="__EVENTVALIDATION"[^>]*value="([^"]*)"', html)

    post_data = urllib.parse.urlencode({
        "ag":                   ag.upper(),
        "__VIEWSTATE":          vs.group(1)  if vs  else "",
        "__VIEWSTATEGENERATOR": vsg.group(1) if vsg else "",
        "__EVENTVALIDATION":    ev.group(1)  if ev  else "",
    })

    result_html = _curl(base_cmd + [
        "-m", str(GVS_TIMEOUT_POST), "-b", cookie,
        "-X", "POST", "-d", post_data,
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-H", f"Referer: {target}", target,
    ])

    try:
        os.unlink(cookie)
    except Exception:
        pass

    match = re.search(r"var rec\s*=\s*(\{.*?\});", result_html, re.DOTALL)
    if not match:
        return {"verified": False, "error": "no_result — ag may not match qr"}

    rec = json.loads(match.group(1))

    subjects = []
    for s in rec.get("subj", []):
        if s.get("c1"):
            subjects.append({"code": s["c1"], "subject": s["s1"], "grade": s["g1"], "description": s["d1"]})
        if s.get("c2"):
            subjects.append({"code": s["c2"], "subject": s["s2"], "grade": s["g2"], "description": s["d2"]})

    def clean(val):
        return re.sub(r"<br\s*/?>", "\n", val or "").strip()

    PASS_GRADES = {"A+", "A", "A-", "B+", "B", "C+", "C", "D", "E"}
    FAIL_GRADES = {"G"}
    GRADE_ORDER = ["A+", "A", "A-", "B+", "B", "C+", "C", "D", "E", "G"]

    grades = [s["grade"] for s in subjects]
    total  = len(grades)
    passed = sum(1 for g in grades if g in PASS_GRADES)
    failed = sum(1 for g in grades if g in FAIL_GRADES)
    pass_pct = round(passed / total * 100) if total else 0

    grade_dist = {g: grades.count(g) for g in GRADE_ORDER if grades.count(g) > 0}

    return {
        "verified":        True,
        "name":            rec.get("cdd"),
        "ic":              rec.get("ic"),
        "ag":              rec.get("idx"),
        "school":          rec.get("sch"),
        "year":            rec.get("exam"),
        "cert_no":         rec.get("certNo"),
        "cert_status":     rec.get("certRem"),
        "cert_status_code":rec.get("certStt"),
        "doc_type":        rec.get("docTyp"),
        "reg_type":        rec.get("regTyp"),
        "prev_exam":       rec.get("prvExam"),
        "subject_count":   rec.get("subjCntDesc"),
        "overall_remark":  clean(rec.get("overallRem")),
        "islamic_remark":  clean(rec.get("islamRem")),
        "gceo_remark":     clean(rec.get("gceoRem")),
        "lcci_remark":     clean(rec.get("lcciRem")),
        "notes":           clean(rec.get("sumRem")),
        "stats": {
            "total_subjects":   total,
            "passed":           passed,
            "failed":           failed,
            "pass_rate_pct":    pass_pct,
            "grade_distribution": grade_dist,
        },
        "subjects":        subjects,
    }


# ════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE  (single URL)
# ════════════════════════════════════════════════════════════════════════════

def process_url(url: str) -> dict:
    out = {"url": url, "status": "success"}

    try:
        data, ctype = fetch_file(url)
    except RuntimeError as e:
        return {**out, "status": "fetch_failed", "error": str(e)}

    try:
        extracted = extract_from_bytes(data, ctype, url)
    except Exception as e:
        return {**out, "status": "extract_failed", "error": str(e)}

    qr_hash = extracted.get("qr_hash")
    ag      = extracted.get("ag")

    if not qr_hash:
        out["status"] = "qr_not_clear"
        out["error"]  = "QR code could not be scanned from this file"
        out["ag"]     = ag
        return out

    if not ag:
        out["status"] = "ag_not_found"
        out["error"]  = "Angka Giliran not found (image files require manual AG)"
        out["qr_hash"]= qr_hash
        return out

    out["qr_hash"] = qr_hash
    out["ag"]      = ag

    try:
        result = verify_gvs(ag, qr_hash)
    except Exception as e:
        return {**out, "status": "verify_failed", "error": str(e)}

    if not result.get("verified"):
        out["status"] = "verify_failed"
        out["error"]  = result.get("error", "unknown")
        return out

    return {**out, **result}


# ════════════════════════════════════════════════════════════════════════════
# INPUT READERS
# ════════════════════════════════════════════════════════════════════════════

def read_urls_from_file(path: str) -> list[str]:
    suffix = Path(path).suffix.lower()

    if suffix in (".xlsx", ".xls"):
        if not HAS_OPENPYXL:
            print("ERROR: openpyxl not installed. Run: pip install openpyxl")
            sys.exit(1)
        wb  = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws  = wb.active
        rows = list(ws.iter_rows(values_only=True))
        header = [str(c).strip().lower() if c else "" for c in rows[0]]
        col = header.index("url") if "url" in header else 0
        return [str(r[col]).strip() for r in rows[1:] if r[col]]

    urls = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(1024)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
            reader  = csv.DictReader(f, dialect=dialect)
            for row in reader:
                url = row.get("url") or row.get("URL") or list(row.values())[0]
                if url and url.strip():
                    urls.append(url.strip())
        except csv.Error:
            f.seek(0)
            urls = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    return urls


# ════════════════════════════════════════════════════════════════════════════
# OUTPUT WRITERS
# ════════════════════════════════════════════════════════════════════════════

def save_results(results: list[dict], out_path: str):
    suffix = Path(out_path).suffix.lower()

    if suffix == ".json":
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    elif suffix in (".xlsx", ".xls"):
        if not HAS_OPENPYXL:
            print("ERROR: openpyxl not installed.")
            sys.exit(1)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Results"
        cols = ["url","status","name","ic","ag","school","year","cert_no",
                "cert_status","doc_type","reg_type","prev_exam","subject_count",
                "overall_remark","islamic_remark","gceo_remark","lcci_remark",
                "notes","subjects_summary","qr_hash","error"]
        ws.append(cols)
        for r in results:
            subj_summary = "; ".join(
                f"{s['code']}:{s['grade']}" for s in r.get("subjects", [])
            )
            ws.append([
                r.get("url"), r.get("status"), r.get("name"), r.get("ic"),
                r.get("ag"), r.get("school"), r.get("year"), r.get("cert_no"),
                r.get("cert_status"), r.get("doc_type"), r.get("reg_type"),
                r.get("prev_exam"), r.get("subject_count"),
                r.get("overall_remark"), r.get("islamic_remark"),
                r.get("gceo_remark"), r.get("lcci_remark"),
                r.get("notes"), subj_summary, r.get("qr_hash"), r.get("error"),
            ])
        wb.save(out_path)

    else:  # CSV default
        cols = ["url","status","name","ic","ag","school","year","cert_no",
                "cert_status","doc_type","reg_type","prev_exam","subject_count",
                "overall_remark","islamic_remark","gceo_remark","lcci_remark",
                "notes","subjects_summary","qr_hash","error"]
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in results:
                row = dict(r)
                row["subjects_summary"] = "; ".join(
                    f"{s['code']}:{s['grade']}" for s in r.get("subjects", [])
                )
                w.writerow(row)

    print(f"Saved → {out_path}")


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="GVS Malaysian Certificate Verifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", nargs="?", help="Single certificate URL (image or PDF)")
    parser.add_argument("--file", "-f", help="CSV or XLSX file with URLs")
    parser.add_argument("--out",  "-o", help="Output file (.json / .csv / .xlsx)")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help=f"Delay between requests in seconds (default {REQUEST_DELAY})")
    args = parser.parse_args()

    if not args.url and not args.file:
        parser.print_help()
        sys.exit(1)

    if args.url:
        print(f"Processing: {args.url}", file=sys.stderr)
        result = process_url(args.url)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.out:
            save_results([result], args.out)
        return

    urls = read_urls_from_file(args.file)
    if not urls:
        print("No URLs found in file.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(urls)} URLs in {args.file}", file=sys.stderr)

    results = []
    iterator = tqdm(urls, desc="Verifying", unit="cert") if HAS_TQDM else urls

    for i, url in enumerate(iterator):
        if not HAS_TQDM:
            print(f"[{i+1}/{len(urls)}] {url}", file=sys.stderr)

        result = process_url(url)
        results.append(result)

        status = result.get("status")
        name   = result.get("name", "")
        if not HAS_TQDM:
            print(f"  → {status}  {name}", file=sys.stderr)

        if i < len(urls) - 1:
            time.sleep(args.delay)

    success = sum(1 for r in results if r.get("status") == "success")
    print(f"\nDone: {success}/{len(results)} verified successfully", file=sys.stderr)

    if args.out:
        save_results(results, args.out)
    else:
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
