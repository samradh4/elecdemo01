from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import DateTime, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

import main as legacy
from v4 import Base, SessionLocal, admin_user, db_session

# Bulk OCR should use a fixed worker budget. Unbounded per-upload threads make all
# conversions slower and can crash a small server when several PDFs arrive together.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

PDF_WORKERS = max(1, min(4, int(os.environ.get("PDF_WORKERS", "2"))))
PDF_MAX_BATCH = max(1, min(100, int(os.environ.get("PDF_MAX_BATCH", "50"))))
PDF_STORAGE_DIR = Path(os.environ.get("PDF_STORAGE_DIR", str(legacy.DATA_DIR / "pdf-v42")))
PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# 2.0 is a balanced production default: substantially fewer pixels than 2.5,
# while the existing targeted 4x/6x fallback OCR remains available for weak fields.
legacy.OCR_SCALE = float(os.environ.get("ROLL_OCR_SCALE", "2.0"))
legacy.JOBS_DIR = PDF_STORAGE_DIR


class PdfJobRecord(Base):
    __tablename__ = "v42_pdf_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    batch_id: Mapped[str] = mapped_column(String(40), index=True)
    filename: Mapped[str] = mapped_column(String(260), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="Queued")
    source_path: Mapped[str] = mapped_column(Text, default="")
    result_path: Mapped[str] = mapped_column(Text, default="")
    extracted_rows: Mapped[int] = mapped_column(Integer, default=0)
    clean_rows: Mapped[int] = mapped_column(Integer, default=0)
    review_rows: Mapped[int] = mapped_column(Integer, default=0)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


Base.metadata.create_all(legacy.engine)
router = APIRouter(prefix="/v4", tags=["v4.2-bulk-pdf"])
EXECUTOR = ThreadPoolExecutor(max_workers=PDF_WORKERS, thread_name_prefix="pdf-ocr")
RECOVERY_LOCK = threading.Lock()
RECOVERY_STARTED = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_dict(row: PdfJobRecord, live: Optional[dict] = None) -> dict:
    live = live or {}
    status = str(live.get("status") or row.status)
    progress = int(live.get("progress", row.progress) or 0)
    message = str(live.get("message") or row.message or "")
    warnings = live.get("warnings")
    if warnings is None:
        try:
            warnings = json.loads(row.warnings_json or "[]")
        except Exception:
            warnings = []
    return {
        "jobId": row.job_id,
        "batchId": row.batch_id,
        "filename": row.filename,
        "status": status,
        "progress": progress,
        "message": message,
        "extractedRows": int(live.get("extractedRows", row.extracted_rows) or 0),
        "cleanRows": int(live.get("cleanRows", row.clean_rows) or 0),
        "reviewRows": int(live.get("reviewRows", row.review_rows) or 0),
        "summaryMatch": live.get("summaryMatch"),
        "warnings": warnings,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "downloadReady": status == "done" and bool(row.result_path or live.get("xlsx")),
    }


def _live(job_id: str) -> dict:
    with legacy.JOBS_LOCK:
        return dict(legacy.JOBS.get(job_id) or {})


def _sync_record(job_id: str) -> None:
    live = _live(job_id)
    with SessionLocal() as db:
        row = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
        if not row:
            return
        if live:
            row.status = str(live.get("status") or row.status)
            row.progress = int(live.get("progress", row.progress) or 0)
            row.message = str(live.get("message") or row.message or "")[:4000]
            row.extracted_rows = int(live.get("extractedRows", row.extracted_rows) or 0)
            row.clean_rows = int(live.get("cleanRows", row.clean_rows) or 0)
            row.review_rows = int(live.get("reviewRows", row.review_rows) or 0)
            row.warnings_json = json.dumps(live.get("warnings") or [], ensure_ascii=False)
            if live.get("xlsx"):
                row.result_path = str(live["xlsx"])
        row.updated_at = _now()
        db.commit()


def _run_job(job_id: str, source_path: Path) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
        if row:
            row.status = "processing"
            row.progress = max(1, row.progress)
            row.message = "Starting OCR"
            row.updated_at = _now()
            db.commit()
    try:
        with legacy.JOBS_LOCK:
            legacy.JOBS[job_id] = {
                "id": job_id,
                "status": "queued",
                "progress": 0,
                "message": "Queued",
                "filename": source_path.name,
                "rows": [],
            }
        legacy.extract_pdf_job(job_id, source_path)
        _sync_record(job_id)
        # Large extracted row arrays are already persisted to the job JSON file.
        # Drop them from RAM so a large batch does not grow the web process forever.
        with legacy.JOBS_LOCK:
            if job_id in legacy.JOBS and legacy.JOBS[job_id].get("status") == "done":
                legacy.JOBS[job_id]["rows"] = []
    except Exception as exc:
        with SessionLocal() as db:
            row = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
            if row:
                row.status = "error"
                row.progress = 100
                row.message = f"Conversion failed: {exc}"[:4000]
                row.updated_at = _now()
                db.commit()


def _submit(job_id: str, source_path: Path) -> None:
    EXECUTOR.submit(_run_job, job_id, source_path)


async def _save_upload(upload: UploadFile, path: Path) -> int:
    size = 0
    limit = legacy.MAX_PDF_BYTES
    with path.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"{upload.filename}: PDF is too large")
            out.write(chunk)
    return size


def _recover_once() -> None:
    global RECOVERY_STARTED
    with RECOVERY_LOCK:
        if RECOVERY_STARTED:
            return
        RECOVERY_STARTED = True
    with SessionLocal() as db:
        rows = db.scalars(select(PdfJobRecord).where(PdfJobRecord.status.in_(["queued", "processing"]))).all()
        for row in rows:
            p = Path(row.source_path)
            if p.exists():
                row.status = "queued"
                row.message = "Recovered after server restart"
                row.updated_at = _now()
                db.commit()
                _submit(row.job_id, p)
            else:
                row.status = "error"
                row.progress = 100
                row.message = "Source PDF is unavailable after server restart. Configure PDF_STORAGE_DIR on persistent storage for production."
                row.updated_at = _now()
        db.commit()


@router.get("/admin/pdf/config")
def pdf_config(admin=Depends(admin_user), db: Session = Depends(db_session)):
    _recover_once()
    queued = db.scalar(select(func.count()).select_from(PdfJobRecord).where(PdfJobRecord.status == "queued")) or 0
    processing = db.scalar(select(func.count()).select_from(PdfJobRecord).where(PdfJobRecord.status == "processing")) or 0
    return {
        "version": "4.2.0",
        "workers": PDF_WORKERS,
        "maxBatchFiles": PDF_MAX_BATCH,
        "maxPdfMb": int(legacy.MAX_PDF_BYTES / 1024 / 1024),
        "ocrScale": legacy.OCR_SCALE,
        "queued": queued,
        "processing": processing,
        "storageDir": str(PDF_STORAGE_DIR),
    }


@router.post("/admin/pdf/bulk")
async def bulk_pdf_upload(files: list[UploadFile] = File(...), admin=Depends(admin_user)):
    _recover_once()
    if not files:
        raise HTTPException(400, "Choose at least one PDF")
    if len(files) > PDF_MAX_BATCH:
        raise HTTPException(400, f"Maximum {PDF_MAX_BATCH} PDFs per upload batch")
    batch_id = uuid.uuid4().hex[:12]
    created = []
    for upload in files:
        filename = (upload.filename or "").strip()
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"{filename or 'File'} is not a PDF")
        job_id = uuid.uuid4().hex[:16]
        source_path = PDF_STORAGE_DIR / f"{job_id}.pdf"
        await _save_upload(upload, source_path)
        with SessionLocal() as db:
            row = PdfJobRecord(
                job_id=job_id,
                batch_id=batch_id,
                filename=filename,
                status="queued",
                progress=0,
                message="Queued",
                source_path=str(source_path),
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            created.append(_job_dict(row))
        _submit(job_id, source_path)
    return {"ok": True, "batchId": batch_id, "jobs": created, "workers": PDF_WORKERS}


@router.get("/admin/pdf/jobs")
def list_pdf_jobs(limit: int = Query(default=100, ge=1, le=500), admin=Depends(admin_user), db: Session = Depends(db_session)):
    _recover_once()
    rows = db.scalars(select(PdfJobRecord).order_by(PdfJobRecord.created_at.desc()).limit(limit)).all()
    out = []
    for row in rows:
        live = _live(row.job_id)
        if live:
            row.status = str(live.get("status") or row.status)
            row.progress = int(live.get("progress", row.progress) or 0)
            row.message = str(live.get("message") or row.message or "")[:4000]
            row.updated_at = _now()
        out.append(_job_dict(row, live))
    db.commit()
    return out


@router.get("/admin/pdf/jobs/{job_id}")
def get_pdf_job(job_id: str, admin=Depends(admin_user), db: Session = Depends(db_session)):
    row = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
    if not row:
        raise HTTPException(404, "PDF job not found")
    live = _live(job_id)
    if live:
        _sync_record(job_id)
    return _job_dict(row, live)


@router.get("/admin/pdf/jobs/{job_id}/xlsx")
def download_pdf_xlsx(job_id: str, admin=Depends(admin_user), db: Session = Depends(db_session)):
    row = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
    if not row:
        raise HTTPException(404, "PDF job not found")
    live = _live(job_id)
    result = Path(str(live.get("xlsx") or row.result_path or ""))
    if row.status != "done" and live.get("status") != "done":
        raise HTTPException(409, "Excel file is not ready")
    if not result.exists():
        raise HTTPException(410, "Excel result is no longer on disk. Configure persistent PDF_STORAGE_DIR for production.")
    safe = Path(row.filename).stem[:120] or job_id
    return FileResponse(result, filename=f"{safe}-converted.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.post("/admin/pdf/jobs/{job_id}/retry")
def retry_pdf_job(job_id: str, admin=Depends(admin_user), db: Session = Depends(db_session)):
    row = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
    if not row:
        raise HTTPException(404, "PDF job not found")
    source = Path(row.source_path)
    if not source.exists():
        raise HTTPException(410, "Source PDF is no longer available")
    if row.status in {"queued", "processing"}:
        return _job_dict(row, _live(job_id))
    row.status = "queued"
    row.progress = 0
    row.message = "Queued for retry"
    row.updated_at = _now()
    db.commit()
    _submit(job_id, source)
    return _job_dict(row)
