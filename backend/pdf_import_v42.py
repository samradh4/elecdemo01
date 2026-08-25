from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from v4 import Booth, Constituency, Voter, admin_user, db_session
from pdf_bulk_v42 import PDF_STORAGE_DIR, PdfJobRecord

router = APIRouter(prefix="/v4", tags=["v4.2-pdf-import"])
DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def _rows_for_job(job: PdfJobRecord) -> list[dict]:
    p = PDF_STORAGE_DIR / f"{job.job_id}.json"
    if not p.exists():
        raise HTTPException(410, f"Extracted rows for {job.filename} are no longer on disk")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        raise HTTPException(500, f"Could not read extracted rows: {exc}")


def _split_name(row: dict) -> tuple[str, str]:
    raw = str(row.get("name") or "").strip()
    local = str(row.get("localName") or "").strip()
    # The Hindi roll parser historically places the Hindi value in both fields.
    # V4 keeps English and Hindi separate, so never duplicate Hindi into English.
    if DEVANAGARI.search(raw):
        return "", local or raw
    if DEVANAGARI.search(local):
        return raw, local
    return raw, local


def _import_job(job: PdfJobRecord, constituency_id: int, db: Session) -> dict:
    if job.status != "done":
        raise HTTPException(409, f"{job.filename}: conversion is not complete")
    c = db.get(Constituency, constituency_id)
    if not c or not c.active:
        raise HTTPException(404, "Constituency not found")
    rows = _rows_for_job(job)
    inserted = updated = skipped = 0
    booths_touched: set[str] = set()
    booth_cache: dict[str, Booth] = {}

    for row in rows:
        if row.get("recordStatus") == "Deleted":
            skipped += 1
            continue
        epic = str(row.get("epicId") or "").strip().upper()
        if not epic:
            skipped += 1
            continue
        booth_no = str(row.get("partNo") or row.get("boothNo") or "").strip()
        if not booth_no:
            skipped += 1
            continue
        booth = booth_cache.get(booth_no)
        if not booth:
            booth = db.scalar(select(Booth).where(Booth.constituency_id == c.id, Booth.booth_no == booth_no))
            if not booth:
                booth = Booth(
                    constituency_id=c.id,
                    booth_no=booth_no,
                    name=f"Booth {booth_no}",
                    address=str(row.get("boothAddress") or "").strip(),
                    active=True,
                )
                db.add(booth)
                db.flush()
            booth_cache[booth_no] = booth
        booths_touched.add(booth_no)

        name_en, name_hi = _split_name(row)
        voter = db.scalar(select(Voter).where(Voter.booth_id == booth.id, Voter.epic_id == epic))
        if not voter:
            voter = Voter(constituency_id=c.id, booth_id=booth.id, epic_id=epic, name=name_en or "", local_name=name_hi or "")
            db.add(voter)
            inserted += 1
        else:
            updated += 1
        voter.constituency_id = c.id
        voter.booth_id = booth.id
        voter.serial_no = str(row.get("serialNo") or "").strip()
        voter.epic_id = epic
        voter.name = name_en or ""
        voter.local_name = name_hi or ""
        voter.relation_type = str(row.get("relationType") or "").strip()
        voter.relative_name = str(row.get("relativeName") or "").strip()
        voter.house_no = str(row.get("houseNo") or "").strip()
        try:
            voter.age = int(row.get("age") or 0)
        except Exception:
            voter.age = 0
        voter.gender = str(row.get("gender") or "Other").strip() or "Other"
        voter.section = str(row.get("sectionAddress") or row.get("ward") or "").strip()
        if (inserted + updated) % 500 == 0:
            db.commit()
    db.commit()
    return {
        "jobId": job.job_id,
        "filename": job.filename,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "booths": sorted(booths_touched, key=lambda x: (len(x), x)),
    }


@router.post("/admin/pdf/jobs/{job_id}/import-to-v4")
def import_pdf_job_to_v4(job_id: str, constituencyId: int = Query(..., ge=1), admin=Depends(admin_user), db: Session = Depends(db_session)):
    job = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
    if not job:
        raise HTTPException(404, "PDF job not found")
    return _import_job(job, constituencyId, db)


@router.post("/admin/pdf/batches/{batch_id}/import-to-v4")
def import_pdf_batch_to_v4(batch_id: str, constituencyId: int = Query(..., ge=1), admin=Depends(admin_user), db: Session = Depends(db_session)):
    jobs = db.scalars(select(PdfJobRecord).where(PdfJobRecord.batch_id == batch_id).order_by(PdfJobRecord.created_at)).all()
    if not jobs:
        raise HTTPException(404, "PDF batch not found")
    results = []
    for job in jobs:
        if job.status == "done":
            results.append(_import_job(job, constituencyId, db))
    return {
        "ok": True,
        "batchId": batch_id,
        "jobsImported": len(results),
        "inserted": sum(x["inserted"] for x in results),
        "updated": sum(x["updated"] for x in results),
        "skipped": sum(x["skipped"] for x in results),
        "results": results,
    }


@router.get("/admin/pdf/batches/{batch_id}/zip")
def download_pdf_batch_zip(batch_id: str, admin=Depends(admin_user), db: Session = Depends(db_session)):
    jobs = db.scalars(select(PdfJobRecord).where(PdfJobRecord.batch_id == batch_id).order_by(PdfJobRecord.created_at)).all()
    if not jobs:
        raise HTTPException(404, "PDF batch not found")
    ready = []
    for job in jobs:
        if job.status != "done":
            continue
        p = Path(job.result_path or (PDF_STORAGE_DIR / f"{job.job_id}.xlsx"))
        if p.exists():
            ready.append((job, p))
    if not ready:
        raise HTTPException(409, "No Excel results are ready in this batch")
    zip_path = PDF_STORAGE_DIR / f"batch-{batch_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        used = set()
        for job, p in ready:
            stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(job.filename).stem)[:100] or job.job_id
            name = f"{stem}.xlsx"
            if name in used:
                name = f"{stem}-{job.job_id[:6]}.xlsx"
            used.add(name)
            z.write(p, arcname=name)
    return FileResponse(zip_path, filename=f"pdf-batch-{batch_id}.zip", media_type="application/zip")
