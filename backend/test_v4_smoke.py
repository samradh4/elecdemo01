import os, tempfile
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mktemp(suffix='.db')
from fastapi.testclient import TestClient
from v4 import router
from fastapi import FastAPI
app=FastAPI(); app.include_router(router); c=TestClient(app)

r=c.post('/v4/auth/register',json={'username':'owner','fullName':'Owner','phone':'','password':'secret123'}); assert r.status_code==200,r.text; admin=r.json(); assert admin['user']['role']=='admin'; ah={'Authorization':'Bearer '+admin['token']}
r=c.post('/v4/admin/constituencies',headers=ah,json={'code':'AC-1','name':'Demo AC'}); assert r.status_code==200,r.text; cid=r.json()['id']
r=c.post('/v4/admin/booths',headers=ah,json={'constituencyId':cid,'boothNo':'101','name':'Demo booth','address':''}); assert r.status_code==200,r.text; bid=r.json()['id']
r=c.post('/v4/auth/register',json={'username':'vol1','fullName':'Volunteer One','phone':'','password':'secret123'}); assert r.status_code==200,r.text; vol=r.json(); vh={'Authorization':'Bearer '+vol['token']}
r=c.post('/v4/assignments/request',headers=vh,json={'constituencyId':cid,'boothId':bid}); assert r.status_code==200,r.text; aid=r.json()['assignment']['id']
r=c.post(f'/v4/admin/assignments/{aid}/approve',headers=ah); assert r.status_code==200,r.text
from v4 import SessionLocal,Voter
with SessionLocal() as db:
    v=Voter(constituency_id=cid,booth_id=bid,serial_no='1',epic_id='ABC0000001',name='Demo Voter',gender='Male'); db.add(v); db.commit(); db.refresh(v); vid=v.id
r=c.get('/v4/my/voters',headers=vh); assert r.status_code==200,r.text; assert len(r.json()['items'])==1
r=c.post('/v4/sync',headers=vh,json={'mutations':[{'mutationId':'mutation-0001','voterId':vid,'status':'Completed','notes':'done offline','updatedAt':'2026-08-25T00:00:00Z'}]}); assert r.status_code==200,r.text; assert r.json()['items'][0]['surveyStatus']=='Completed'
r=c.get('/v4/admin/dashboard',headers=ah); assert r.status_code==200,r.text; assert r.json()['completed']==1
print('v4 smoke ok')
