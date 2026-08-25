from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from v4 import Booth, Constituency, Voter, admin_user, db_session
from pdf_bulk_v42 import PdfJobRecord
from v421_demo_fix import _job_rows_path, _split_name, admin_ops_portal_fixed

router = APIRouter(prefix="/v4", tags=["v4.2.3-import-diagnostics"])


@router.post("/admin/pdf/jobs/{job_id}/import")
def import_pdf_job_to_booth_diagnostic(
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

    rows_path = _job_rows_path(job)
    import json
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise HTTPException(500, "Converted voter data is invalid")

    inserted = updated = skipped_deleted = rejected = 0
    reasons: Counter[str] = Counter()
    errors: list[dict] = []

    for idx, row in enumerate(rows, start=1):
        try:
            if str(row.get("recordStatus") or "").strip().lower() == "deleted":
                skipped_deleted += 1
                reasons["Deleted record skipped"] += 1
                continue

            epic = str(row.get("epicId") or "").strip().upper()
            english_name, hindi_name = _split_name(row)

            reason = ""
            if not epic:
                reason = "EPIC ID missing"
            elif not english_name and not hindi_name:
                reason = "Voter name missing"

            if reason:
                rejected += 1
                reasons[reason] += 1
                if len(errors) < 50:
                    errors.append({
                        "row": idx,
                        "serialNo": str(row.get("serialNo") or "").strip(),
                        "sourcePage": row.get("sourcePage") or "",
                        "epicId": epic,
                        "name": str(row.get("name") or row.get("localName") or "").strip()[:120],
                        "reason": reason,
                    })
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
            reasons["Other import error"] += 1
            if len(errors) < 50:
                errors.append({
                    "row": idx,
                    "serialNo": str(row.get("serialNo") or "").strip(),
                    "sourcePage": row.get("sourcePage") or "",
                    "epicId": str(row.get("epicId") or "").strip(),
                    "name": str(row.get("name") or row.get("localName") or "").strip()[:120],
                    "reason": str(exc),
                })

    db.commit()
    imported = inserted + updated
    return {
        "ok": True,
        "jobId": job_id,
        "constituencyId": constituency.id,
        "boothId": booth.id,
        "boothNo": booth.booth_no,
        "sourceRows": len(rows),
        "verifiedRows": int(job.clean_rows or 0),
        "reviewRows": int(job.review_rows or 0),
        "inserted": inserted,
        "updated": updated,
        "skippedDeleted": skipped_deleted,
        "rejected": rejected,
        "availableToVolunteerAfterSync": imported,
        "reasonCounts": dict(reasons),
        "errors": errors,
        "qualityWarning": (
            "No verified rows were produced by OCR. Review the Excel/output before using this data."
            if int(job.clean_rows or 0) == 0 and len(rows) > 0 else ""
        ),
    }


PORTAL_DIAGNOSTICS_SCRIPT = r'''
<script>
(function(){
  function opt(value,label){return '<option value="'+String(value)+'">'+esc(label)+'</option>'}
  function ensureDetails(){
    let d=$('pdfImportDetails');
    if(!d){
      d=document.createElement('div');
      d.id='pdfImportDetails';
      d.style.marginTop='12px';
      const card=$('pdfImportCard');
      if(card) card.appendChild(d);
    }
    return d;
  }
  window.loadPdfImportJobs=async function(){
    const s=$('pdfImportJob'); if(!s)return;
    const d=ensureDetails(); if(d)d.innerHTML='';
    try{
      const rows=await req('/admin/pdf/jobs?limit=150');
      const done=rows.filter(x=>x.status==='done'&&x.downloadReady);
      window.__pdfJobsById={}; done.forEach(x=>window.__pdfJobsById[x.jobId]=x);
      s.innerHTML=done.length?done.map(x=>{
        const verified=Number(x.cleanRows||0), review=Number(x.reviewRows||0), extracted=Number(x.extractedRows||0);
        const quality=verified===0&&extracted>0?'⚠ needs review':'✓';
        return opt(x.jobId,(x.filename||'PDF')+' · '+extracted+' extracted rows · '+verified+' verified · '+review+' review · '+quality);
      }).join(''):'<option value="">No completed PDF yet</option>';
      s.onchange=showPdfQuality;
      showPdfQuality();
    }catch(e){s.innerHTML='<option value="">Could not load completed PDFs</option>'}
  };
  window.showPdfQuality=function(){
    const d=ensureDetails(), job=window.__pdfJobsById?.[$('pdfImportJob')?.value];
    if(!d)return;
    if(!job){d.innerHTML='';return;}
    const v=Number(job.cleanRows||0), r=Number(job.reviewRows||0), x=Number(job.extractedRows||0);
    if(x>0&&v===0){
      d.innerHTML='<div class="error" style="padding:10px;border:1px solid #fecaca;border-radius:10px;background:#fef2f2">⚠ OCR produced '+x+' extracted rows but <b>0 verified rows</b>. Treat this conversion as failed/review-only until the Excel is checked.</div>';
    }else{
      d.innerHTML='<div class="ok" style="padding:8px">Quality: '+v+' verified · '+r+' review rows.</div>';
    }
  };
  window.importPdfToBooth=async function(){
    const jobId=$('pdfImportJob')?.value, cid=$('pdfImportConst')?.value, bid=$('pdfImportBooth')?.value, msg=$('pdfImportMsg'), details=ensureDetails();
    if(!jobId)return alert('Convert a PDF first, then choose the completed PDF.');
    if(!cid||!bid)return alert('Choose a constituency and booth.');
    const job=window.__pdfJobsById?.[jobId];
    if(job&&Number(job.extractedRows||0)>0&&Number(job.cleanRows||0)===0){
      if(!confirm('This conversion has 0 verified rows. Importing it may add no usable voters. Continue only if you reviewed the Excel.'))return;
    }
    $('pdfImportBtn').disabled=true; msg.textContent='Importing extracted rows…'; msg.className='muted'; if(details)details.innerHTML='';
    try{
      const j=await req('/admin/pdf/jobs/'+encodeURIComponent(jobId)+'/import?constituencyId='+encodeURIComponent(cid)+'&boothId='+encodeURIComponent(bid),{method:'POST'});
      msg.textContent='Imported '+j.inserted+', updated '+j.updated+', rejected '+j.rejected+'.'+(j.availableToVolunteerAfterSync?' Volunteer can tap Sync now.':' No voter records were added.');
      msg.className=j.availableToVolunteerAfterSync?'ok':'error';
      const counts=Object.entries(j.reasonCounts||{});
      const rows=(j.errors||[]).slice(0,25);
      let html='';
      if(j.qualityWarning)html+='<div class="error" style="margin:8px 0">'+esc(j.qualityWarning)+'</div>';
      if(counts.length)html+='<div style="margin:8px 0"><b>Why rows were rejected/skipped:</b> '+counts.map(([k,v])=>esc(k)+': '+v).join(' · ')+'</div>';
      if(rows.length){
        html+='<details open><summary><b>Rejected row details</b> (showing '+rows.length+')</summary><div style="overflow:auto;margin-top:8px"><table><thead><tr><th>Row</th><th>Serial</th><th>Page</th><th>EPIC</th><th>Name</th><th>Reason</th></tr></thead><tbody>';
        html+=rows.map(x=>'<tr><td>'+esc(x.row)+'</td><td>'+esc(x.serialNo||'—')+'</td><td>'+esc(x.sourcePage||'—')+'</td><td>'+esc(x.epicId||'—')+'</td><td>'+esc(x.name||'—')+'</td><td>'+esc(x.reason||'')+'</td></tr>').join('');
        html+='</tbody></table></div></details>';
      }
      if(details)details.innerHTML=html;
      await loadAll();
    }catch(e){msg.textContent=e.message; msg.className='error'}finally{$('pdfImportBtn').disabled=false}
  };
  const pdfTab=document.querySelector('[data-tab="pdf"]');
  if(pdfTab)pdfTab.addEventListener('click',()=>setTimeout(()=>{loadPdfImportJobs();showPdfQuality()},0));
  setTimeout(()=>{loadPdfImportJobs();showPdfQuality()},700);
})();
</script>
'''


@router.get("/admin-ops", response_class=HTMLResponse, include_in_schema=False)
def admin_ops_portal_v423():
    html = admin_ops_portal_fixed()
    if not isinstance(html, str):
        html = str(html)
    html = html.replace("Constituency Manager 4.2.1", "Constituency Manager 4.2.3")
    # Avoid showing misleading "N voters" for unverified OCR output.
    html = html.replace("+(x.extractedRows||0)+' voters'", "+(x.extractedRows||0)+' extracted rows'")
    if PORTAL_DIAGNOSTICS_SCRIPT not in html:
        html = html.replace("</body></html>", PORTAL_DIAGNOSTICS_SCRIPT + "</body></html>")
    return html
