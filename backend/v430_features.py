from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from v4 import Assignment, User, active_assignment, admin_user, assignment_json, db_session, user_json
from v423_import_diagnostics import admin_ops_portal_v423

router = APIRouter(prefix="/v4", tags=["v4.3-access-approval"])


@router.get("/admin/access-queue")
def access_queue(admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    volunteers = db.scalars(
        select(User)
        .where(User.role == "volunteer", User.active == True)
        .order_by(User.created_at.desc(), User.id.desc())
    ).all()
    out = []
    for u in volunteers:
        if active_assignment(db, u.id):
            continue
        pending = db.scalar(
            select(Assignment)
            .where(Assignment.user_id == u.id, Assignment.status == "pending")
            .order_by(Assignment.requested_at.desc(), Assignment.id.desc())
        )
        uj = user_json(u)
        uj["createdAt"] = u.created_at.isoformat() if u.created_at else None
        out.append({
            "user": uj,
            "pendingAssignment": assignment_json(db, pending) if pending else None,
            "needsAccess": True,
        })
    return out


ACCESS_CSS = r'''
<style>
#accessToast{display:none;position:fixed;right:22px;top:84px;z-index:1000;width:min(390px,calc(100vw - 30px));background:#fff;border:1px solid #d8c8ff;border-left:5px solid #6d28d9;border-radius:14px;box-shadow:0 16px 45px rgba(35,20,65,.18);padding:14px}
#accessToast.show{display:block}.access-row{display:grid;grid-template-columns:minmax(180px,1.3fr) minmax(180px,1fr) auto;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid #eee}.access-row:last-child{border-bottom:0}.access-new{display:inline-block;background:#f3e8ff;color:#6d28d9;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:900;margin-left:6px}.access-actions{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.access-badge{display:none;min-width:20px;height:20px;padding:0 6px;border-radius:999px;background:#dc2626;color:white;font-size:11px;font-weight:900;align-items:center;justify-content:center;margin-left:5px}.access-badge.show{display:inline-flex}.access-stat{cursor:pointer}.access-stat:hover{border-color:#c4b5fd}.assign-focus{outline:3px solid #c4b5fd;outline-offset:3px}
@media(max-width:760px){.access-row{grid-template-columns:1fr}.access-actions{justify-content:flex-start}}
</style>
'''

ACCESS_CARD = r'''
<div class="card" id="accessQueueCard">
  <div class="row" style="align-items:center">
    <div><h3 style="margin-bottom:4px">New registrations awaiting access <span id="accessCount" class="access-new">0</span></h3><p class="muted" style="margin:0">Anyone who registers in the app appears here automatically. Approve a requested booth, or assign a booth directly.</p></div>
    <div style="text-align:right"><button class="secondary" onclick="loadAccessQueue(true)">Refresh</button></div>
  </div>
  <div id="accessQueue" style="margin-top:8px"></div>
</div>
'''

ACCESS_TOAST = r'''
<div id="accessToast">
  <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
    <div><b id="accessToastTitle">New volunteer joined</b><div id="accessToastText" class="muted" style="margin-top:4px"></div></div>
    <button class="small secondary" onclick="hideAccessToast()">×</button>
  </div>
  <div style="margin-top:10px"><button id="accessToastBtn" onclick="openLatestAccess()">Approve access</button></div>
</div>
'''

ACCESS_SCRIPT = r'''
<script>
(function(){
  let queueRows=[];
  let latestAccessUserId=null;
  let firstAccessLoad=true;
  let seenIds=new Set();
  try{seenIds=new Set(JSON.parse(sessionStorage.getItem('cm43-access-seen')||'[]').map(String))}catch{}
  function saveSeen(){try{sessionStorage.setItem('cm43-access-seen',JSON.stringify(Array.from(seenIds).slice(-200)))}catch{}}
  function peopleTab(){return document.querySelector('[data-tab="people"]')}
  function accessLabel(row){const a=row.pendingAssignment;if(a?.booth)return 'Requested: '+(a.constituency?.name||'')+' · Booth '+(a.booth.boothNo||'');return 'Registered — booth not selected yet'}
  function joinedLabel(row){const s=row.user?.createdAt;if(!s)return '';try{return 'Joined '+new Date(s).toLocaleString()}catch{return ''}}
  window.hideAccessToast=function(){$('accessToast')?.classList.remove('show')};
  function showAccessToast(row,count){latestAccessUserId=row.user.id;$('accessToastTitle').textContent=count>1?count+' volunteers waiting for access':'New volunteer joined';$('accessToastText').textContent=(row.user.fullName||row.user.username)+' · '+accessLabel(row);$('accessToast')?.classList.add('show')}
  window.openLatestAccess=function(){if(latestAccessUserId!=null)prepareAccess(latestAccessUserId);hideAccessToast()};
  window.prepareAccess=function(userId){
    const row=queueRows.find(x=>String(x.user?.id)===String(userId));
    if(row?.pendingAssignment?.id){approve(row.pendingAssignment.id);return}
    peopleTab()?.click();
    setTimeout(()=>{
      const sel=$('auser');if(sel){sel.value=String(userId);sel.dispatchEvent(new Event('change'))}
      const target=$('assignAccessPanel')||sel?.closest('.card')||sel?.parentElement;
      if(target){target.classList.add('assign-focus');target.scrollIntoView({behavior:'smooth',block:'center'});setTimeout(()=>target.classList.remove('assign-focus'),2500)}
      const name=row?.user?.fullName||row?.user?.username||'Volunteer';
      const hint=$('accessAssignHint');if(hint){hint.textContent='Approving access for '+name+'. Choose constituency + booth, then click Assign booth.';hint.className='ok'}
    },80)
  };
  function renderAccessQueue(){
    const box=$('accessQueue'), count=$('accessCount'), badge=$('accessBadge');
    if(count)count.textContent=String(queueRows.length);
    if(badge){badge.textContent=String(queueRows.length);badge.classList.toggle('show',queueRows.length>0)}
    if(!box)return;
    if(!queueRows.length){box.innerHTML='<p class="ok">No volunteers are waiting for access.</p>';return}
    box.innerHTML=queueRows.map(row=>{
      const u=row.user||{}, a=row.pendingAssignment;
      const action=a?.id?'<button class="small" onclick="approve('+Number(a.id)+')">Approve requested booth</button> <button class="small danger" onclick="revoke('+Number(a.id)+')">Reject</button>':'<button class="small" onclick="prepareAccess('+Number(u.id)+')">Approve access / assign booth</button>';
      return '<div class="access-row"><div><b>'+esc(u.fullName||u.username||'Volunteer')+'</b><span class="access-new">NEW</span><div class="muted">@'+esc(u.username||'')+(u.phone?' · '+esc(u.phone):'')+'</div><div class="muted">'+esc(joinedLabel(row))+'</div></div><div><b>'+esc(accessLabel(row))+'</b></div><div class="access-actions">'+action+'</div></div>'
    }).join('')
  }
  function ensureStatsCard(){const stats=$('stats');if(!stats)return;let card=$('accessStatCard');if(!card){card=document.createElement('div');card.id='accessStatCard';card.className='card stat access-stat';card.onclick=()=>peopleTab()?.click();stats.appendChild(card)}card.innerHTML='<span class="muted">Awaiting access</span><b>'+queueRows.length+'</b>'}
  window.loadAccessQueue=async function(notify=false){
    if(!token)return;
    try{
      const rows=await req('/admin/access-queue');queueRows=Array.isArray(rows)?rows:[];renderAccessQueue();ensureStatsCard();
      const unseen=queueRows.filter(r=>!seenIds.has(String(r.user?.id)));
      if((notify||!firstAccessLoad)&&unseen.length){showAccessToast(unseen[0],unseen.length);unseen.forEach(r=>seenIds.add(String(r.user?.id)));saveSeen()}
      else if(firstAccessLoad&&queueRows.length){const firstUnseen=queueRows.find(r=>!seenIds.has(String(r.user?.id)));if(firstUnseen){showAccessToast(firstUnseen,unseen.length||1);queueRows.forEach(r=>seenIds.add(String(r.user?.id)));saveSeen()}}
      firstAccessLoad=false
    }catch(e){console.warn('Could not refresh access queue',e)}
  };
  const oldPending=window.renderPending;if(typeof oldPending==='function')window.renderPending=function(rows){oldPending(rows);setTimeout(()=>loadAccessQueue(false),0)};
  const oldAll=window.loadAll;if(typeof oldAll==='function')window.loadAll=async function(){const out=await oldAll();await loadAccessQueue(false);return out};
  const oldAssign=window.directAssign;if(typeof oldAssign==='function')window.directAssign=async function(){const out=await oldAssign();setTimeout(()=>loadAccessQueue(false),100);return out};
  const oldApprove=window.approve;if(typeof oldApprove==='function')window.approve=async function(id){const out=await oldApprove(id);setTimeout(()=>loadAccessQueue(false),100);return out};
  const tab=peopleTab();if(tab&&!$('accessBadge')){const b=document.createElement('span');b.id='accessBadge';b.className='access-badge';tab.appendChild(b)}
  setTimeout(()=>loadAccessQueue(false),700);setInterval(()=>{if(token&&$('appPanel')?.style.display!=='none')loadAccessQueue(false)},10000)
})();
</script>
'''


@router.get("/admin-ops", response_class=HTMLResponse, include_in_schema=False)
def admin_ops_portal_v430():
    html = admin_ops_portal_v423()
    if not isinstance(html, str):
        html = str(html)
    html = html.replace("Constituency Manager 4.2.3", "Constituency Manager 4.3")
    html = html.replace("</style>", "</style>" + ACCESS_CSS, 1)
    people_marker = '<div id="people" class="section">'
    if people_marker in html and 'id="accessQueueCard"' not in html:
        html = html.replace(people_marker, people_marker + ACCESS_CARD, 1)
    html = html.replace('<div><h3>Assign / reassign booth</h3>', '<div id="assignAccessPanel"><h3>Assign / reassign booth</h3><div id="accessAssignHint" class="muted"></div>', 1)
    if 'id="accessToast"' not in html:
        html = html.replace("</main>", "</main>" + ACCESS_TOAST, 1)
    if ACCESS_SCRIPT not in html:
        html = html.replace("</body></html>", ACCESS_SCRIPT + "</body></html>")
    return html
