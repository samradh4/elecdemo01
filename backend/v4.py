from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/voter_manager.db")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "v4_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(140), default="")
    phone: Mapped[str] = mapped_column(String(30), default="", index=True)
    password_hash: Mapped[str] = mapped_column(String(220))
    role: Mapped[str] = mapped_column(String(20), default="volunteer", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Constituency(Base):
    __tablename__ = "v4_constituencies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Booth(Base):
    __tablename__ = "v4_booths"
    __table_args__ = (UniqueConstraint("constituency_id", "booth_no", name="uq_v4_booth_constituency_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    constituency_id: Mapped[int] = mapped_column(ForeignKey("v4_constituencies.id", ondelete="CASCADE"), index=True)
    booth_no: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(180), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Voter(Base):
    __tablename__ = "v4_voters"
    __table_args__ = (UniqueConstraint("booth_id", "epic_id", name="uq_v4_voter_booth_epic"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    constituency_id: Mapped[int] = mapped_column(ForeignKey("v4_constituencies.id", ondelete="CASCADE"), index=True)
    booth_id: Mapped[int] = mapped_column(ForeignKey("v4_booths.id", ondelete="CASCADE"), index=True)
    serial_no: Mapped[str] = mapped_column(String(30), default="", index=True)
    epic_id: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    local_name: Mapped[str] = mapped_column(String(180), default="")
    relation_type: Mapped[str] = mapped_column(String(30), default="")
    relative_name: Mapped[str] = mapped_column(String(180), default="", index=True)
    house_no: Mapped[str] = mapped_column(String(80), default="")
    age: Mapped[int] = mapped_column(Integer, default=0)
    gender: Mapped[str] = mapped_column(String(20), default="Other", index=True)
    section: Mapped[str] = mapped_column(String(240), default="")
    survey_status: Mapped[str] = mapped_column(String(30), default="Pending", index=True)
    survey_notes: Mapped[str] = mapped_column(Text, default="")
    survey_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    survey_updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("v4_users.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

class Assignment(Base):
    __tablename__ = "v4_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("v4_users.id", ondelete="CASCADE"), index=True)
    constituency_id: Mapped[int] = mapped_column(ForeignKey("v4_constituencies.id", ondelete="CASCADE"), index=True)
    booth_id: Mapped[int] = mapped_column(ForeignKey("v4_booths.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("v4_users.id"), nullable=True)

class SurveyLog(Base):
    __tablename__ = "v4_survey_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mutation_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    voter_id: Mapped[int] = mapped_column(ForeignKey("v4_voters.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("v4_users.id", ondelete="CASCADE"), index=True)
    booth_id: Mapped[int] = mapped_column(ForeignKey("v4_booths.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="Pending")
    notes: Mapped[str] = mapped_column(Text, default="")
    client_updated_at: Mapped[str] = mapped_column(String(60), default="")
    server_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

Base.metadata.create_all(engine)
router = APIRouter(prefix="/v4", tags=["v4"])
AUTH_SECRET = os.environ.get("V4_AUTH_SECRET") or hashlib.sha256((DB_URL + "|constituency-manager-v4").encode()).hexdigest()
TOKEN_TTL_DAYS = int(os.environ.get("V4_TOKEN_TTL_DAYS", "14"))
OFFLINE_LEASE_HOURS = int(os.environ.get("V4_OFFLINE_LEASE_HOURS", "24"))
SURVEY_STATUSES = {"Pending", "Visited", "Completed", "Not Available"}

def db_session():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def _b64(data: bytes) -> str: return base64.urlsafe_b64encode(data).decode().rstrip("=")
def _unb64(text: str) -> bytes: return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
def make_token(user: User) -> str:
    payload={"uid":user.id,"role":user.role,"exp":int(time.time())+TOKEN_TTL_DAYS*86400}
    body=_b64(json.dumps(payload,separators=(",",":")).encode()); sig=_b64(hmac.new(AUTH_SECRET.encode(),body.encode(),hashlib.sha256).digest()); return body+"."+sig

def parse_token(token: str) -> dict:
    try:
        body,sig=token.split(".",1); expected=_b64(hmac.new(AUTH_SECRET.encode(),body.encode(),hashlib.sha256).digest())
        if not hmac.compare_digest(sig,expected): raise ValueError("signature")
        data=json.loads(_unb64(body))
        if int(data.get("exp",0))<int(time.time()): raise ValueError("expired")
        return data
    except Exception: raise HTTPException(401,"Invalid or expired session")

def hash_password(password: str) -> str:
    salt=secrets.token_bytes(16); rounds=210_000; digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,rounds); return f"pbkdf2_sha256${rounds}${_b64(salt)}${_b64(digest)}"
def verify_password(password: str, stored: str) -> bool:
    try:
        algo,rounds,salt,digest=stored.split("$",3)
        if algo!="pbkdf2_sha256": return False
        check=hashlib.pbkdf2_hmac("sha256",password.encode(),_unb64(salt),int(rounds)); return hmac.compare_digest(_b64(check),digest)
    except Exception: return False

def current_user(authorization: str|None=Header(default=None),db:Session=Depends(db_session))->User:
    if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(401,"Login required")
    data=parse_token(authorization.split(" ",1)[1].strip()); user=db.get(User,int(data["uid"]))
    if not user or not user.active: raise HTTPException(401,"User is inactive")
    return user

def admin_user(user:User=Depends(current_user))->User:
    if user.role!="admin": raise HTTPException(403,"Admin access required")
    return user

def active_assignment(db:Session,user_id:int)->Optional[Assignment]:
    return db.scalar(select(Assignment).where(Assignment.user_id==user_id,Assignment.status=="active").order_by(Assignment.id.desc()))

class RegisterIn(BaseModel):
    username:str=Field(min_length=3,max_length=80); fullName:str=Field(min_length=2,max_length=140); phone:str=""; password:str=Field(min_length=6,max_length=128)
class LoginIn(BaseModel): username:str; password:str
class AssignmentRequestIn(BaseModel): constituencyId:int; boothId:int
class AdminUserIn(BaseModel): username:str=Field(min_length=3,max_length=80); fullName:str=Field(min_length=2,max_length=140); phone:str=""; password:str=Field(min_length=6,max_length=128)
class DirectAssignmentIn(BaseModel): userId:int; constituencyId:int; boothId:int
class SurveyMutation(BaseModel): mutationId:str=Field(min_length=8,max_length=80); voterId:int; status:str; notes:str=""; updatedAt:str=""
class SyncIn(BaseModel): mutations:list[SurveyMutation]=[]
class PasswordChangeIn(BaseModel): currentPassword:str; newPassword:str=Field(min_length=8,max_length=128)
class ConstituencyIn(BaseModel): code:str=Field(min_length=1,max_length=40); name:str=Field(min_length=2,max_length=180)
class BoothIn(BaseModel): constituencyId:int; boothNo:str=Field(min_length=1,max_length=40); name:str=""; address:str=""

@router.get("/health")
def v4_health(db:Session=Depends(db_session)):
    return {"ok":True,"version":"4.0.0","users":db.scalar(select(func.count()).select_from(User)) or 0,"offlineLeaseHours":OFFLINE_LEASE_HOURS}

@router.post("/auth/register")
def register(payload:RegisterIn,db:Session=Depends(db_session)):
    username=payload.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._@+-]{3,80}",username): raise HTTPException(400,"Username contains unsupported characters")
    if db.scalar(select(User).where(User.username==username)): raise HTTPException(409,"Username already exists")
    first=(db.scalar(select(func.count()).select_from(User)) or 0)==0
    user=User(username=username,full_name=payload.fullName.strip(),phone=payload.phone.strip(),password_hash=hash_password(payload.password),role="admin" if first else "volunteer")
    db.add(user);db.commit();db.refresh(user);return {"token":make_token(user),"user":user_json(user),"bootstrapAdmin":first}

@router.post("/auth/login")
def login(payload:LoginIn,db:Session=Depends(db_session)):
    user=db.scalar(select(User).where(User.username==payload.username.strip().lower()))
    if not user or not verify_password(payload.password,user.password_hash): raise HTTPException(401,"Incorrect username or password")
    if not user.active: raise HTTPException(403,"Account is inactive")
    return {"token":make_token(user),"user":user_json(user)}

@router.post("/auth/change-password")
def change_password(payload:PasswordChangeIn,user:User=Depends(current_user),db:Session=Depends(db_session)):
    u=db.get(User,user.id)
    if not verify_password(payload.currentPassword,u.password_hash): raise HTTPException(400,"Current password is incorrect")
    u.password_hash=hash_password(payload.newPassword);db.commit();return {"ok":True}

@router.get("/me")
def me(user:User=Depends(current_user),db:Session=Depends(db_session)):
    a=active_assignment(db,user.id);return {"user":user_json(user),"assignment":assignment_json(db,a) if a else None}

@router.get("/catalog")
def catalog(user:User=Depends(current_user),db:Session=Depends(db_session)):
    cs=db.scalars(select(Constituency).where(Constituency.active==True).order_by(Constituency.name)).all();bs=db.scalars(select(Booth).where(Booth.active==True).order_by(Booth.constituency_id,Booth.booth_no)).all();g={}
    for b in bs:g.setdefault(b.constituency_id,[]).append(booth_json(b))
    return [{**constituency_json(c),"booths":g.get(c.id,[])} for c in cs]

@router.post("/assignments/request")
def request_assignment(payload:AssignmentRequestIn,user:User=Depends(current_user),db:Session=Depends(db_session)):
    if user.role=="admin": raise HTTPException(400,"Admin accounts do not need booth approval")
    booth=db.get(Booth,payload.boothId)
    if not booth or booth.constituency_id!=payload.constituencyId or not booth.active: raise HTTPException(404,"Booth not found")
    existing=db.scalar(select(Assignment).where(Assignment.user_id==user.id,Assignment.status.in_(["pending","active"])).order_by(Assignment.id.desc()))
    if existing:return {"assignment":assignment_json(db,existing),"message":"An assignment request already exists"}
    a=Assignment(user_id=user.id,constituency_id=payload.constituencyId,booth_id=payload.boothId,status="pending");db.add(a);db.commit();db.refresh(a);return {"assignment":assignment_json(db,a)}

@router.get("/my/assignment")
def my_assignment(user:User=Depends(current_user),db:Session=Depends(db_session)):
    rows=db.scalars(select(Assignment).where(Assignment.user_id==user.id).order_by(Assignment.id.desc()).limit(10)).all();active=next((a for a in rows if a.status=="active"),None);pending=next((a for a in rows if a.status=="pending"),None);lease=(datetime.now(timezone.utc)+timedelta(hours=OFFLINE_LEASE_HOURS)).isoformat() if active else None
    return {"active":assignment_json(db,active) if active else None,"pending":assignment_json(db,pending) if pending else None,"offlineLeaseUntil":lease}

@router.get("/my/voters")
def my_voters(user:User=Depends(current_user),db:Session=Depends(db_session)):
    a=active_assignment(db,user.id)
    if not a: raise HTTPException(403,"No active booth assignment")
    rows=db.scalars(select(Voter).where(Voter.booth_id==a.booth_id).order_by(Voter.serial_no,Voter.id)).all();return {"assignment":assignment_json(db,a),"offlineLeaseUntil":(datetime.now(timezone.utc)+timedelta(hours=OFFLINE_LEASE_HOURS)).isoformat(),"items":[voter_json(v) for v in rows]}

@router.post("/sync")
def sync(payload:SyncIn,user:User=Depends(current_user),db:Session=Depends(db_session)):
    a=active_assignment(db,user.id)
    if not a: raise HTTPException(403,"Your booth access is no longer active")
    accepted=[];rejected=[]
    for m in payload.mutations[:5000]:
        if m.status not in SURVEY_STATUSES: rejected.append({"mutationId":m.mutationId,"error":"Invalid status"});continue
        if db.scalar(select(SurveyLog).where(SurveyLog.mutation_id==m.mutationId)): accepted.append(m.mutationId);continue
        v=db.get(Voter,m.voterId)
        if not v or v.booth_id!=a.booth_id: rejected.append({"mutationId":m.mutationId,"error":"Voter is outside assigned booth"});continue
        now=datetime.now(timezone.utc);v.survey_status=m.status;v.survey_notes=(m.notes or "")[:4000];v.survey_updated_at=now;v.survey_updated_by=user.id;v.version=(v.version or 0)+1;db.add(SurveyLog(mutation_id=m.mutationId,voter_id=v.id,user_id=user.id,booth_id=a.booth_id,status=m.status,notes=v.survey_notes,client_updated_at=m.updatedAt or ""));accepted.append(m.mutationId)
    db.commit();changed=db.scalars(select(Voter).where(Voter.booth_id==a.booth_id).order_by(Voter.serial_no,Voter.id)).all();return {"ok":True,"accepted":accepted,"rejected":rejected,"items":[voter_json(v) for v in changed],"offlineLeaseUntil":(datetime.now(timezone.utc)+timedelta(hours=OFFLINE_LEASE_HOURS)).isoformat()}

@router.get("/admin/dashboard")
def admin_dashboard(admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    total=db.scalar(select(func.count()).select_from(Voter)) or 0;completed=db.scalar(select(func.count()).select_from(Voter).where(Voter.survey_status=="Completed")) or 0
    return {"constituencies":db.scalar(select(func.count()).select_from(Constituency).where(Constituency.active==True)) or 0,"booths":db.scalar(select(func.count()).select_from(Booth).where(Booth.active==True)) or 0,"voters":total,"completed":completed,"completionPct":round(completed/total*100,1) if total else 0,"volunteers":db.scalar(select(func.count()).select_from(User).where(User.role=="volunteer",User.active==True)) or 0,"pendingRequests":db.scalar(select(func.count()).select_from(Assignment).where(Assignment.status=="pending")) or 0,"activeAssignments":db.scalar(select(func.count()).select_from(Assignment).where(Assignment.status=="active")) or 0}

@router.post("/admin/constituencies")
def create_constituency(payload:ConstituencyIn,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    code=payload.code.strip()
    if db.scalar(select(Constituency).where(Constituency.code==code)): raise HTTPException(409,"Constituency code already exists")
    c=Constituency(code=code,name=payload.name.strip());db.add(c);db.commit();db.refresh(c);return constituency_json(c)

@router.post("/admin/booths")
def create_booth(payload:BoothIn,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    c=db.get(Constituency,payload.constituencyId)
    if not c: raise HTTPException(404,"Constituency not found")
    existing=db.scalar(select(Booth).where(Booth.constituency_id==c.id,Booth.booth_no==payload.boothNo.strip()))
    if existing:return booth_json(existing)
    b=Booth(constituency_id=c.id,booth_no=payload.boothNo.strip(),name=payload.name.strip(),address=payload.address.strip());db.add(b);db.commit();db.refresh(b);return booth_json(b)

@router.get("/admin/users")
def admin_users(admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    rows=db.scalars(select(User).order_by(User.role,User.full_name)).all();out=[]
    for u in rows:
        a=active_assignment(db,u.id);out.append({**user_json(u),"assignment":assignment_json(db,a) if a else None})
    return out

@router.post("/admin/users")
def admin_create_user(payload:AdminUserIn,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    username=payload.username.strip().lower()
    if db.scalar(select(User).where(User.username==username)): raise HTTPException(409,"Username already exists")
    u=User(username=username,full_name=payload.fullName.strip(),phone=payload.phone.strip(),password_hash=hash_password(payload.password),role="volunteer",active=True);db.add(u);db.commit();db.refresh(u);return user_json(u)

@router.get("/admin/assignments")
def admin_assignments(status:str=Query(default=""),admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    stmt=select(Assignment)
    if status:stmt=stmt.where(Assignment.status==status)
    return [assignment_json(db,a,include_user=True) for a in db.scalars(stmt.order_by(Assignment.requested_at.desc()).limit(500)).all()]

@router.post("/admin/assignments/{assignment_id}/approve")
def approve_assignment(assignment_id:int,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    a=db.get(Assignment,assignment_id)
    if not a or a.status!="pending": raise HTTPException(404,"Pending assignment not found")
    now=datetime.now(timezone.utc)
    for p in db.scalars(select(Assignment).where(Assignment.user_id==a.user_id,Assignment.status=="active")).all():p.status="revoked";p.revoked_at=now
    a.status="active";a.approved_at=now;a.approved_by=admin.id;db.commit();db.refresh(a);return assignment_json(db,a,include_user=True)

@router.post("/admin/assignments/direct")
def direct_assignment(payload:DirectAssignmentIn,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    u=db.get(User,payload.userId);b=db.get(Booth,payload.boothId)
    if not u or u.role!="volunteer": raise HTTPException(404,"Volunteer not found")
    if not b or b.constituency_id!=payload.constituencyId: raise HTTPException(404,"Booth not found")
    now=datetime.now(timezone.utc)
    for p in db.scalars(select(Assignment).where(Assignment.user_id==u.id,Assignment.status.in_(["active","pending"]))).all():p.status="revoked";p.revoked_at=now
    a=Assignment(user_id=u.id,constituency_id=b.constituency_id,booth_id=b.id,status="active",approved_at=now,approved_by=admin.id);db.add(a);db.commit();db.refresh(a);return assignment_json(db,a,include_user=True)

@router.post("/admin/assignments/{assignment_id}/revoke")
def revoke_assignment(assignment_id:int,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    a=db.get(Assignment,assignment_id)
    if not a: raise HTTPException(404,"Assignment not found")
    a.status="revoked";a.revoked_at=datetime.now(timezone.utc);db.commit();return {"ok":True}

@router.get("/admin/booth-progress")
def booth_progress(constituencyId:int=0,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    stmt=select(Booth).where(Booth.active==True)
    if constituencyId:stmt=stmt.where(Booth.constituency_id==constituencyId)
    out=[]
    for b in db.scalars(stmt.order_by(Booth.constituency_id,Booth.booth_no)).all():
        total=db.scalar(select(func.count()).select_from(Voter).where(Voter.booth_id==b.id)) or 0;completed=db.scalar(select(func.count()).select_from(Voter).where(Voter.booth_id==b.id,Voter.survey_status=="Completed")) or 0;active=db.scalar(select(Assignment).where(Assignment.booth_id==b.id,Assignment.status=="active"));out.append({**booth_json(b),"total":total,"completed":completed,"pending":max(0,total-completed),"completionPct":round(completed/total*100,1) if total else 0,"assignedUser":user_json(db.get(User,active.user_id)) if active else None})
    return out

@router.post("/admin/import/voters.csv")
async def import_voters_csv(file:UploadFile=File(...),admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    raw=await file.read()
    if len(raw)>80*1024*1024: raise HTTPException(413,"CSV is too large")
    reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig",errors="replace")));inserted=updated=rejected=0;errors=[]
    for idx,row in enumerate(reader,start=2):
        try:
            ccode=(row.get("constituencyCode") or row.get("AC No") or row.get("acNo") or "").strip();cname=(row.get("constituencyName") or row.get("Constituency") or row.get("AC Name") or ccode or "Constituency").strip();booth_no=(row.get("boothNo") or row.get("Booth No") or row.get("partNo") or row.get("Part No") or "").strip()
            if not ccode or not booth_no: raise ValueError("constituencyCode and boothNo are required")
            c=db.scalar(select(Constituency).where(Constituency.code==ccode))
            if not c:c=Constituency(code=ccode,name=cname);db.add(c);db.flush()
            b=db.scalar(select(Booth).where(Booth.constituency_id==c.id,Booth.booth_no==booth_no))
            if not b:b=Booth(constituency_id=c.id,booth_no=booth_no,name=(row.get("boothName") or row.get("Booth Name") or "").strip(),address=(row.get("boothAddress") or row.get("Booth Address") or "").strip());db.add(b);db.flush()
            epic=(row.get("epicId") or row.get("EPIC ID") or row.get("EPIC") or "").strip().upper();name=(row.get("name") or row.get("Name") or row.get("नाम") or "").strip()
            if not epic or not name: raise ValueError("EPIC ID and name are required")
            v=db.scalar(select(Voter).where(Voter.booth_id==b.id,Voter.epic_id==epic))
            if not v:v=Voter(constituency_id=c.id,booth_id=b.id,epic_id=epic,name=name);db.add(v);inserted+=1
            else:updated+=1
            v.serial_no=(row.get("serialNo") or row.get("Serial No") or row.get("क्रमांक") or "").strip();v.name=name;v.local_name=(row.get("localName") or row.get("Local/Hindi Name") or name).strip();v.relation_type=(row.get("relationType") or row.get("Relation") or "").strip();v.relative_name=(row.get("relativeName") or row.get("Relative Name") or "").strip();v.house_no=(row.get("houseNo") or row.get("House No") or "").strip()
            try:v.age=int((row.get("age") or row.get("Age") or "0").strip() or 0)
            except:v.age=0
            v.gender=(row.get("gender") or row.get("Gender") or "Other").strip() or "Other";v.section=(row.get("section") or row.get("Section") or row.get("sectionAddress") or "").strip()
            if (inserted+updated)%500==0:db.commit()
        except Exception as e:
            rejected+=1
            if len(errors)<30:errors.append({"row":idx,"error":str(e)})
    db.commit();return {"ok":True,"inserted":inserted,"updated":updated,"rejected":rejected,"errors":errors}

@router.get("/admin/export/survey.csv")
def export_survey_csv(constituencyId:int=0,boothId:int=0,admin:User=Depends(admin_user),db:Session=Depends(db_session)):
    stmt=select(Voter)
    if constituencyId:stmt=stmt.where(Voter.constituency_id==constituencyId)
    if boothId:stmt=stmt.where(Voter.booth_id==boothId)
    rows=db.scalars(stmt.order_by(Voter.constituency_id,Voter.booth_id,Voter.serial_no)).all();out=io.StringIO();w=csv.writer(out);w.writerow(["Constituency","Booth No","Serial No","EPIC ID","Name","Relative Name","House No","Age","Gender","Survey Status","Survey Notes","Updated At","Updated By"])
    for v in rows:
        c=db.get(Constituency,v.constituency_id);b=db.get(Booth,v.booth_id);u=db.get(User,v.survey_updated_by) if v.survey_updated_by else None;w.writerow([c.name if c else "",b.booth_no if b else "",v.serial_no,v.epic_id,v.name,v.relative_name,v.house_no,v.age,v.gender,v.survey_status,v.survey_notes,v.survey_updated_at.isoformat() if v.survey_updated_at else "",u.full_name if u else ""])
    data="\ufeff"+out.getvalue();return StreamingResponse(iter([data.encode("utf-8")]),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=survey-export.csv"})

def user_json(u:User):return {"id":u.id,"username":u.username,"fullName":u.full_name,"phone":u.phone,"role":u.role,"active":u.active}
def constituency_json(c:Constituency):return {"id":c.id,"code":c.code,"name":c.name,"active":c.active}
def booth_json(b:Booth):return {"id":b.id,"constituencyId":b.constituency_id,"boothNo":b.booth_no,"name":b.name,"address":b.address,"active":b.active}
def assignment_json(db:Session,a:Optional[Assignment],include_user:bool=False):
    if not a:return None
    c=db.get(Constituency,a.constituency_id);b=db.get(Booth,a.booth_id);u=db.get(User,a.user_id) if include_user else None;out={"id":a.id,"userId":a.user_id,"constituency":constituency_json(c) if c else None,"booth":booth_json(b) if b else None,"status":a.status,"requestedAt":a.requested_at.isoformat() if a.requested_at else None,"approvedAt":a.approved_at.isoformat() if a.approved_at else None,"revokedAt":a.revoked_at.isoformat() if a.revoked_at else None}
    if include_user:out["user"]=user_json(u) if u else None
    return out
def voter_json(v:Voter):return {"id":v.id,"constituencyId":v.constituency_id,"boothId":v.booth_id,"serialNo":v.serial_no,"epicId":v.epic_id,"name":v.name,"localName":v.local_name,"relationType":v.relation_type,"relativeName":v.relative_name,"houseNo":v.house_no,"age":v.age,"gender":v.gender,"section":v.section,"surveyStatus":v.survey_status,"surveyNotes":v.survey_notes,"surveyUpdatedAt":v.survey_updated_at.isoformat() if v.survey_updated_at else None,"version":v.version}

@router.get("/admin",response_class=HTMLResponse,include_in_schema=False)
def admin_portal():
    path=Path(__file__).with_name("admin_v4.html")
    if path.exists():return path.read_text(encoding="utf-8")
    return "<h1>Admin portal file missing</h1>"
