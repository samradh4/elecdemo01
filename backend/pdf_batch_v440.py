from __future__ import annotations

import io
import re
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import pdf_bulk_v42 as bulk
from v4 import admin_user, db_session

router = APIRouter(prefix="/v4", tags=["v4.4-fast-batch-pdf"])
_BATCH_LOCK = threading.Lock()
_BATCH_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _clean_batch_id(value: str | None) -> str:
    cleaned = _BATCH_RE.sub("-", str(value or "").strip()).strip("-")[:40]
    return cleaned or uuid.uuid4().hex[:12]


def _batch_rows(db: Session, batch_id: str):
    return db.scalars(
        select(bulk.PdfJobRecord)
        .where(bulk.PdfJobRecord.batch_id == batch_id)
        .order_by(bulk.PdfJobRecord.created_at.asc())
    ).all()


@router.post("/admin/pdf/bulk/single")
async def upload_one_pdf_to_batch(
    file: UploadFile = File(...),
    batchId: str | None = Form(default=None),
    admin=Depends(admin_user),
):
    """Native-mobile friendly batch intake.

    Android uploads each selected PDF with Expo FileSystem's reliable native
    multipart uploader while all files share one batch id. Conversion still
    runs through the bounded server-side OCR queue, so selecting many files
    does not create unbounded OCR processes.
    """
    bulk._recover_once()
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, f"{filename or 'File'} is not a PDF")

    batch_id = _clean_batch_id(batchId)
    job_id = uuid.uuid4().hex[:16]
    source_path = bulk.PDF_STORAGE_DIR / f"{job_id}.pdf"
    await bulk._save_upload(file, source_path)

    try:
        with _BATCH_LOCK:
            with bulk.SessionLocal() as db:
                count = db.scalar(
                    select(func.count())
                    .select_from(bulk.PdfJobRecord)
                    .where(bulk.PdfJobRecord.batch_id == batch_id)
                ) or 0
                if count >= bulk.PDF_MAX_BATCH:
                    raise HTTPException(400, f"Maximum {bulk.PDF_MAX_BATCH} PDFs per upload batch")

                row = bulk.PdfJobRecord(
                    job_id=job_id,
                    batch_id=batch_id,
                    filename=filename,
                    status="queued",
                    progress=0,
                    message="Queued",
                    source_path=str(source_path),
                    created_at=bulk._now(),
                    updated_at=bulk._now(),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                out = bulk._job_dict(row)
    except Exception:
        source_path.unlink(missing_ok=True)
        raise

    bulk._submit(job_id, source_path)
    return {
        "ok": True,
        "batchId": batch_id,
        "job": out,
        "workers": bulk.PDF_WORKERS,
        "maxBatchFiles": bulk.PDF_MAX_BATCH,
    }


@router.get("/admin/pdf/batches/{batch_id}")
def get_pdf_batch(batch_id: str, admin=Depends(admin_user), db: Session = Depends(db_session)):
    bulk._recover_once()
    batch_id = _clean_batch_id(batch_id)
    rows = _batch_rows(db, batch_id)
    if not rows:
        raise HTTPException(404, "PDF batch not found")

    jobs = []
    for row in rows:
        live = bulk._live(row.job_id)
        if live:
            bulk._sync_record(row.job_id)
            db.refresh(row)
        jobs.append(bulk._job_dict(row, live))

    counts = {
        "total": len(jobs),
        "queued": sum(1 for j in jobs if j["status"] == "queued"),
        "processing": sum(1 for j in jobs if j["status"] == "processing"),
        "done": sum(1 for j in jobs if j["status"] == "done"),
        "error": sum(1 for j in jobs if j["status"] == "error"),
    }
    return {
        "batchId": batch_id,
        "jobs": jobs,
        "counts": counts,
        "complete": counts["done"] == counts["total"] and counts["total"] > 0,
        "workers": bulk.PDF_WORKERS,
    }


@router.get("/admin/pdf/batches/{batch_id}/xlsx.zip")
def download_pdf_batch_zip(batch_id: str, admin=Depends(admin_user), db: Session = Depends(db_session)):
    batch_id = _clean_batch_id(batch_id)
    rows = _batch_rows(db, batch_id)
    if not rows:
        raise HTTPException(404, "PDF batch not found")

    if any(row.status in {"queued", "processing"} for row in rows):
        raise HTTPException(409, "Batch is still converting")
    if any(row.status == "error" for row in rows):
        raise HTTPException(409, "One or more PDFs failed. Retry failed jobs before downloading the batch.")
    if any(row.status != "done" for row in rows):
        raise HTTPException(409, "Batch is not ready")

    zip_path = bulk.PDF_STORAGE_DIR / f"batch-{batch_id}.zip"
    used: dict[str, int] = {}
    missing = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
        for row in rows:
            result = Path(row.result_path or "")
            if not result.exists():
                missing.append(row.filename or row.job_id)
                continue
            stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(row.filename).stem).strip()[:100] or row.job_id
            used[stem] = used.get(stem, 0) + 1
            suffix = f"-{used[stem]}" if used[stem] > 1 else ""
            zf.write(result, arcname=f"{stem}{suffix}-converted.xlsx")

    if missing:
        zip_path.unlink(missing_ok=True)
        raise HTTPException(410, "Some converted files are no longer on disk. Persistent PDF storage is required for production batches.")

    return FileResponse(
        zip_path,
        filename=f"constituency-pdf-batch-{batch_id}.zip",
        media_type="application/zip",
    )
