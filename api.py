#!/usr/bin/env python3
"""SPM Results Verification API"""

import asyncio, io, csv, time, json, os
from fastapi import FastAPI, UploadFile, File, Form, Security, Depends, HTTPException, Request
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

# Load .env before importing gvs_cli (which reads env vars at import time)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from gvs_cli import fetch_file, extract_from_bytes, verify_gvs, read_urls_from_file, process_url

APP_API_KEY = os.environ.get("APP_API_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(key: str = Security(api_key_header)):
    if APP_API_KEY and key != APP_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")

app = FastAPI(
    title="SPM Results Verification",
    description="Verify Malaysian SPM certificates via Lembaga Peperiksaan Malaysia (gvs.moe.gov.my).",
    version="1.0",
)


async def _extract(data: bytes, ctype: str, source: str) -> tuple:
    try:
        extracted = extract_from_bytes(data, ctype, source)
    except Exception as e:
        return None, None, str(e), None
    return extracted.get("ag"), extracted.get("qr_hash"), None, extracted.get("gemini_error")


class VerifyRequest(BaseModel):
    url:     Optional[str] = Field(default=None, description="Public URL of the certificate image or PDF")
    ag:      Optional[str] = Field(default=None, description="Angka Giliran — leave empty to auto-extract")
    qr_hash: Optional[str] = Field(default=None, description="QR hash — leave empty to auto-extract")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://example.com/certificate.pdf"
                },
                {
                    "ag": "PF001A004",
                    "qr_hash": "1C31FD4A78EC4DE5A9282AE148B07A132CB8F3D8CC"
                }
            ]
        }
    }


@app.post("/verify", summary="Verify via URL", dependencies=[Depends(verify_api_key)])
async def verify(req: VerifyRequest):
    ag      = req.ag.strip().upper()      if req.ag      else None
    qr_hash = req.qr_hash.strip().upper() if req.qr_hash else None

    if ag and qr_hash:
        return verify_gvs(ag, qr_hash)
    if not req.url:
        return JSONResponse({"error": "Provide a url, or both ag and qr_hash"}, 400)

    try:
        data, ctype = fetch_file(req.url)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, 500)

    ext_ag, ext_hash, err, gemini_err = await _extract(data, ctype, req.url)
    if err:
        return JSONResponse({"error": err}, 500)

    ag      = ag      or ext_ag
    qr_hash = qr_hash or ext_hash

    if not qr_hash:
        return JSONResponse({"error": "QR code could not be scanned"}, 422)
    if not ag:
        msg = f"Angka Giliran not found — {gemini_err}" if gemini_err else "Angka Giliran not found"
        return JSONResponse({"error": msg}, 422)

    return verify_gvs(ag, qr_hash)


@app.post("/upload", summary="Verify via File Upload", dependencies=[Depends(verify_api_key)])
async def upload(
    file: UploadFile = File(..., description="Certificate PDF or image (JPG, PNG, PDF)"),
    ag:   Optional[str] = Form(default=None, description="Angka Giliran — optional"),
):
    data  = await file.read()
    ctype = file.content_type or ""

    ext_ag, qr_hash, err, gemini_err = await _extract(data, ctype, file.filename or "")
    if err:
        return JSONResponse({"error": err}, 500)

    resolved_ag = (ag.strip().upper() if ag else None) or ext_ag

    if not qr_hash:
        return JSONResponse({"error": "QR code could not be scanned"}, 422)
    if not resolved_ag:
        msg = f"Angka Giliran not found — {gemini_err}" if gemini_err else "Angka Giliran not found"
        return JSONResponse({"error": msg}, 422)

    return verify_gvs(resolved_ag, qr_hash)


@app.post("/bulk", summary="Bulk verify — streams results as SSE", dependencies=[Depends(verify_api_key)])
async def bulk(
    file: UploadFile = File(..., description="CSV or XLSX with a 'url' column"),
):
    import tempfile, os
    data   = await file.read()
    suffix = (file.filename or "").rsplit(".", 1)[-1].lower()

    with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as tmp:
        tmp.write(data); tmp_path = tmp.name

    try:
        urls = read_urls_from_file(tmp_path)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)
    finally:
        try: os.unlink(tmp_path)
        except: pass

    if not urls:
        return JSONResponse({"error": "No URLs found in file"}, 400)

    async def stream():
        yield f"data: {json.dumps({'type':'total','total':len(urls)})}\n\n"
        for i, url in enumerate(urls):
            result = await asyncio.to_thread(process_url, url)
            yield f"data: {json.dumps({'type':'result','i':i,'result':result})}\n\n"
            if i < len(urls) - 1:
                await asyncio.sleep(0.6)
        yield f"data: {json.dumps({'type':'done'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/export-csv", summary="Export results as CSV", include_in_schema=False)
async def export_csv(results: list[dict]):
    cols = ["url","status","name","ic","ag","school","year","cert_no","overall_remark","notes","subjects_summary","error"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in results:
        row = dict(r)
        row["subjects_summary"] = "; ".join(f"{s['code']}:{s['grade']}" for s in r.get("subjects", []))
        w.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=spm_results.csv"},
    )


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")
