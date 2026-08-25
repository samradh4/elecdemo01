from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from v4 import admin_user, db_session, Constituency, Booth, Voter
from pdf_bulk_v42 import PdfJobRecord

router = APIRouter(prefix="/v4", tags=["v4.2.1-demo-fix"])


def _has_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in (text or ""))


def _has_latin(text: str) -> bool:
    return any(ch.isascii() and ch.isalpha() for ch in (text or ""))


def _split_name(row: dict) -> tuple[str, str]:
    raw_name = str(row.get("name") or "").strip()
    raw_local = str(row.get("localName") or "").strip()

    hindi = ""
    if _has_devanagari(raw_local):
        hindi = raw_local
    elif _has_devanagari(raw_name):
        hindi = raw_name

    english = ""
    if _has_latin(raw_name) and not _has_devanagari(raw_name):
        english = raw_name
    elif _has_latin(raw_local) and not _has_devanagari(raw_local):
        english = raw_local

    # Do not copy Hindi into the English field or English into the Hindi field.
    return english[:180], hindi[:180]


def _job_rows_path(job: PdfJobRecord) -> Path:
    if job.result_path:
        p = Path(job.result_path).with_suffix(".json")
        if p.exists():
            return p
    if job.source_path:
        p = Path(job.source_path).with_name(f"{job.job_id}.json")
        if p.exists():
            return p
    raise HTTPException(410, "Converted voter rows are no longer available on disk. Use persistent PDF storage in production.")


@router.post("/admin/pdf/jobs/{job_id}/import")
def import_pdf_job_to_booth(
    job_id: str,
    constituencyId: int = Query(..., ge=1),
    boothId: int = Query(..., ge=1),
    admin=Depends(admin_user),
    db: Session = Depends(db_session),
):
    job = db.scalar(select(PdfJobRecord).where(PdfJobRecord.job_id == job_id))
    if not job:
        raise HTTPException(404, "PDF job not found")
    if job.status != "done":
        raise HTTPException(409, "PDF conversion is not finished yet")

    constituency = db.get(Constituency, constituencyId)
    booth = db.get(Booth, boothId)
    if not constituency or not constituency.active:
        raise HTTPException(404, "Constituency not found")
    if not booth or not booth.active or booth.constituency_id != constituency.id:
        raise HTTPException(404, "Booth not found in this constituency")

    rows = json.loads(_job_rows_path(job).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise HTTPException(500, "Converted voter data is invalid")

    inserted = updated = skipped_deleted = rejected = 0
    errors: list[dict] = []

    for idx, row in enumerate(rows, start=1):
        try:
            if str(row.get("recordStatus") or "").strip().lower() == "deleted":
                skipped_deleted += 1
                continue

            epic = str(row.get("epicId") or "").strip().upper()
            if not epic:
                rejected += 1
                if len(errors) < 25:
                    errors.append({"row": idx, "error": "EPIC ID missing"})
                continue

            english_name, hindi_name = _split_name(row)
            if not english_name and not hindi_name:
                rejected += 1
                if len(errors) < 25:
                    errors.append({"row": idx, "error": "Voter name missing"})
                continue

            voter = db.scalar(select(Voter).where(Voter.booth_id == booth.id, Voter.epic_id == epic))
            if not voter:
                voter = Voter(
                    constituency_id=constituency.id,
                    booth_id=booth.id,
                    epic_id=epic,
                    survey_status="Pending",
                    version=1,
                    name="",
                    local_name="",
                )
                db.add(voter)
                inserted += 1
            else:
                updated += 1
                voter.version = int(voter.version or 0) + 1

            voter.constituency_id = constituency.id
            voter.booth_id = booth.id
            voter.serial_no = str(row.get("serialNo") or "").strip()[:30]
            voter.name = english_name
            voter.local_name = hindi_name
            voter.relation_type = str(row.get("relationType") or "").strip()[:30]
            voter.relative_name = str(row.get("relativeName") or "").strip()[:180]
            voter.house_no = str(row.get("houseNo") or "").strip()[:80]
            try:
                voter.age = int(row.get("age") or 0)
            except Exception:
                voter.age = 0
            gender = str(row.get("gender") or "Other").strip().title()
            voter.gender = gender if gender in {"Male", "Female", "Other"} else "Other"
            voter.section = str(row.get("sectionAddress") or row.get("ward") or "").strip()[:240]

            if (inserted + updated) % 500 == 0:
                db.commit()
        except Exception as exc:
            rejected += 1
            if len(errors) < 25:
                errors.append({"row": idx, "error": str(exc)})

    db.commit()
    return {
        "ok": True,
        "jobId": job_id,
        "constituencyId": constituency.id,
        "boothId": booth.id,
        "boothNo": booth.booth_no,
        "inserted": inserted,
        "updated": updated,
        "skippedDeleted": skipped_deleted,
        "rejected": rejected,
        "availableToVolunteerAfterSync": inserted + updated,
        "errors": errors,
    }


ADMIN_IMPORT_CARD = r'''
<div class="card" id="pdfImportCard">
  <h3>Import completed PDF into a booth</h3>
  <p class="muted">After conversion finishes, select its constituency and booth here. Imported voters become available to the assigned volunteer after they tap <b>Sync now</b>.</p>
  <div class="row">
    <div><label>Completed PDF</label><select id="pdfImportJob"></select></div>
    <div><label>Constituency</label><select id="pdfImportConst" onchange="fillPdfImportBooths()"></select></div>
    <div><label>Booth</label><select id="pdfImportBooth"></select></div>
  </div>
  <button id="pdfImportBtn" onclick="importPdfToBooth()">Import voters to booth</button>
  <span id="pdfImportMsg" class="muted"></span>
</div>
'''

ADMIN_IMPORT_SCRIPT = r'''
<script>
(function(){
  function optionHtml(value,label){return '<option value="'+String(value)+'">'+esc(label)+'</option>'}
  window.fillPdfImportTargets=function(){
    const csel=$('pdfImportConst'); if(!csel)return;
    csel.innerHTML=catalog.length?catalog.map(c=>optionHtml(c.id,(c.code||'')+' — '+(c.name||''))).join(''):'<option value="">No constituencies yet</option>';
    fillPdfImportBooths();
  };
  window.fillPdfImportBooths=function(){
    const c=catalog.find(x=>String(x.id)===String($('pdfImportConst')?.value));
    const b=$('pdfImportBooth'); if(!b)return;
    b.innerHTML=(c?.booths||[]).length?(c.booths||[]).map(x=>optionHtml(x.id,'Booth '+(x.boothNo||'')+' — '+(x.name||x.address||''))).join(''):'<option value="">No booths yet</option>';
  };
  window.loadPdfImportJobs=async function(){
    const s=$('pdfImportJob'); if(!s)return;
    try{
      const rows=await req('/admin/pdf/jobs?limit=150');
      const done=rows.filter(x=>x.status==='done'&&x.downloadReady);
      s.innerHTML=done.length?done.map(x=>optionHtml(x.jobId,(x.filename||'PDF')+' · '+(x.extractedRows||0)+' voters')).join(''):'<option value="">No completed PDF yet</option>';
    }catch(e){s.innerHTML='<option value="">Could not load completed PDFs</option>'}
  };
  window.importPdfToBooth=async function(){
    const job=$('pdfImportJob')?.value, cid=$('pdfImportConst')?.value, bid=$('pdfImportBooth')?.value, msg=$('pdfImportMsg');
    if(!job)return alert('Convert a PDF first, then choose the completed PDF.');
    if(!cid||!bid)return alert('Choose a constituency and booth.');
    $('pdfImportBtn').disabled=true; msg.textContent='Importing voter records…'; msg.className='muted';
    try{
      const j=await req('/admin/pdf/jobs/'+encodeURIComponent(job)+'/import?constituencyId='+encodeURIComponent(cid)+'&boothId='+encodeURIComponent(bid),{method:'POST'});
      msg.textContent='Imported '+j.inserted+', updated '+j.updated+', rejected '+j.rejected+'. Volunteer can tap Sync now.'; msg.className='ok';
      await loadAll();
    }catch(e){msg.textContent=e.message; msg.className='error'}finally{$('pdfImportBtn').disabled=false}
  };
  const oldFill=window.fillSelectors;
  window.fillSelectors=function(){oldFill();fillPdfImportTargets()};
  const pdfTab=document.querySelector('[data-tab="pdf"]');
  if(pdfTab)pdfTab.addEventListener('click',()=>{fillPdfImportTargets();loadPdfImportJobs()});
  setTimeout(()=>{fillPdfImportTargets();loadPdfImportJobs()},500);
})();
</script>
'''


@router.get("/admin-ops", response_class=HTMLResponse, include_in_schema=False)
def admin_ops_portal_fixed():
    path = Path(__file__).with_name("admin_v41.html")
    if not path.exists():
        return "<h1>Admin portal file missing</h1>"
    html = path.read_text(encoding="utf-8")
    html = html.replace("Constituency Manager 4.2", "Constituency Manager 4.2.1")
    marker = '<div id="pdf" class="section">'
    if marker in html and 'id="pdfImportCard"' not in html:
        html = html.replace(marker, marker + ADMIN_IMPORT_CARD, 1)
    if ADMIN_IMPORT_SCRIPT not in html:
        html = html.replace("</body></html>", ADMIN_IMPORT_SCRIPT + "</body></html>")
    return html
