from __future__ import annotations

import csv
import concurrent.futures
import math

import cv2
import numpy as np
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fitz
import pytesseract
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
import xlsxwriter
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("constituency-manager")

# Windows Tesseract auto-detection; TESSERACT_CMD can override this.
tesseract_cmd = os.environ.get("TESSERACT_CMD", "").strip()
if not tesseract_cmd and os.name == "nt":
    candidate = Path(r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe")
    if candidate.exists():
        tesseract_cmd = str(candidate)
if tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'voter_manager.db'}")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Voter(Base):
    __tablename__ = "voters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial_no: Mapped[str] = mapped_column(String(30), default="", index=True)
    epic_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    local_name: Mapped[str] = mapped_column(String(160), default="")
    relation_type: Mapped[str] = mapped_column(String(30), default="")
    relative_name: Mapped[str] = mapped_column(String(160), default="", index=True)
    house_no: Mapped[str] = mapped_column(String(80), default="")
    age: Mapped[int] = mapped_column(Integer, default=0)
    gender: Mapped[str] = mapped_column(String(20), default="Other", index=True)
    ac_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    part_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    booth_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    booth_serial_no: Mapped[str] = mapped_column(String(40), default="")
    ward: Mapped[str] = mapped_column(String(100), default="", index=True)
    section_address: Mapped[str] = mapped_column(Text, default="")
    booth_address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(30), default="", index=True)
    family_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    assigned_to: Mapped[str] = mapped_column(String(120), default="", index=True)
    record_status: Mapped[str] = mapped_column(String(30), default="Active", index=True)
    data_quality: Mapped[str] = mapped_column(String(30), default="Review", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source_page: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class TeamMember(Base):
    __tablename__ = "team_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    role: Mapped[str] = mapped_column(String(80), default="Field Staff")
    area: Mapped[str] = mapped_column(String(120), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)

APP_API_KEY = os.environ.get("APP_API_KEY", "").strip()

def require_api_key(x_app_key: str | None = Header(default=None), key: str | None = Query(default=None)):
    supplied = x_app_key or key
    if APP_API_KEY and supplied != APP_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid app key")

app = FastAPI(title="Constituency Manager API", version="3.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    log.exception("Unhandled backend error")
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": "Server error", "errorId": str(uuid.uuid4())[:8]})

class VoterIn(BaseModel):
    serialNo: str = ""
    epicId: str
    name: str
    localName: str = ""
    relationType: str = ""
    relativeName: str = ""
    houseNo: str = ""
    age: int = 0
    gender: str = "Other"
    acNo: str = ""
    partNo: str = ""
    boothNo: str = ""
    boothSerialNo: str = ""
    ward: str = ""
    sectionAddress: str = ""
    boothAddress: str = ""
    phone: str = ""
    familyKey: str = ""
    assignedTo: str = ""
    recordStatus: str = "Active"
    dataQuality: str = "Review"
    notes: str = ""
    sourcePage: int = 0

    @field_validator("epicId")
    @classmethod
    def normalize_epic(cls, value: str):
        value = re.sub(r"\s+", "", value or "").upper().strip()
        if not value:
            raise ValueError("EPIC ID is required")
        if not re.fullmatch(r"[A-Z0-9]{10}", value) or not re.search(r"[A-Z]", value) or not re.search(r"\d", value):
            raise ValueError("EPIC ID must be a 10-character alphanumeric voter ID")
        return value

    @field_validator("serialNo")
    @classmethod
    def validate_serial(cls, value: str):
        value = str(value or "").strip()
        if value and not value.isdigit():
            raise ValueError("Serial number must contain digits only")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str):
        value = re.sub(r"\s+", " ", value or "").strip()
        if len(value) < 2:
            raise ValueError("Name is required")
        return value[:160]

    @field_validator("age", mode="before")
    @classmethod
    def validate_age(cls, value):
        if value in (None, ""):
            return 0
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("Age must be a whole number")
        if number and not 18 <= number <= 120:
            raise ValueError("Age must be between 18 and 120")
        return number

    @field_validator("gender")
    @classmethod
    def normalize_gender_field(cls, value: str):
        v = str(value or "").strip().lower()
        if v in {"male", "m", "पुरुष"}:
            return "Male"
        if v in {"female", "f", "महिला"}:
            return "Female"
        if v in {"other", "o", "अन्य", ""}:
            return "Other"
        raise ValueError("Gender must be Male, Female, or Other")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str):
        value = re.sub(r"[^0-9+]", "", value or "")
        if value and len(re.sub(r"\D", "", value)) < 7:
            raise ValueError("Phone number is too short")
        return value[:30]

class TeamIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = ""
    role: str = "Field Staff"
    area: str = ""
    active: bool = True


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def voter_dict(v: Voter):
    return {
        "id": v.id, "serialNo": v.serial_no, "epicId": v.epic_id, "name": v.name,
        "localName": v.local_name, "relationType": v.relation_type, "relativeName": v.relative_name,
        "houseNo": v.house_no, "age": v.age, "gender": v.gender, "acNo": v.ac_no,
        "partNo": v.part_no, "boothNo": v.booth_no, "boothSerialNo": v.booth_serial_no,
        "ward": v.ward, "sectionAddress": v.section_address, "boothAddress": v.booth_address,
        "phone": v.phone, "familyKey": v.family_key, "assignedTo": v.assigned_to,
        "recordStatus": v.record_status, "dataQuality": v.data_quality, "notes": v.notes,
        "sourcePage": v.source_page,
        "createdAt": v.created_at.isoformat() if v.created_at else "",
        "updatedAt": v.updated_at.isoformat() if v.updated_at else "",
    }


def apply_voter(v: Voter, data: VoterIn):
    mapping = {
        "serial_no": data.serialNo, "epic_id": data.epicId, "name": data.name,
        "local_name": data.localName, "relation_type": data.relationType, "relative_name": data.relativeName,
        "house_no": data.houseNo, "age": data.age, "gender": data.gender, "ac_no": data.acNo,
        "part_no": data.partNo, "booth_no": data.boothNo, "booth_serial_no": data.boothSerialNo,
        "ward": data.ward, "section_address": data.sectionAddress, "booth_address": data.boothAddress,
        "phone": data.phone, "family_key": data.familyKey, "assigned_to": data.assignedTo,
        "record_status": data.recordStatus, "data_quality": data.dataQuality, "notes": data.notes,
        "source_page": data.sourcePage,
    }
    for k, value in mapping.items():
        setattr(v, k, value)

@app.get("/")
def root():
    return {"ok": True, "service": "Constituency Manager API", "version": "3.5.0"}

@app.get("/health")
def health(db: Session = Depends(db_session)):
    try:
        db.execute(select(func.count()).select_from(Voter)).scalar_one()
        return {"ok": True, "database": "ok", "pdf": True, "version": "3.5.0"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")

@app.get("/dashboard", dependencies=[Depends(require_api_key)])
def dashboard(db: Session = Depends(db_session)):
    total = db.scalar(select(func.count()).select_from(Voter)) or 0
    def count_where(*conds):
        return db.scalar(select(func.count()).select_from(Voter).where(*conds)) or 0
    return {
        "total": total,
        "male": count_where(Voter.gender == "Male"),
        "female": count_where(Voter.gender == "Female"),
        "other": count_where(Voter.gender == "Other"),
        "verified": count_where(Voter.data_quality == "Verified"),
        "review": count_where(Voter.data_quality != "Verified"),
        "booths": db.scalar(select(func.count(func.distinct(Voter.booth_no))).where(Voter.booth_no != "")) or 0,
        "parts": db.scalar(select(func.count(func.distinct(Voter.part_no))).where(Voter.part_no != "")) or 0,
        "team": db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.active == True)) or 0,
    }

@app.get("/voters", dependencies=[Depends(require_api_key)])
def list_voters(
    query: str = "", firstName: str = "", lastName: str = "", relativeName: str = "",
    acNo: str = "", partNo: str = "", serialNo: str = "", epicId: str = "", phone: str = "",
    gender: str = "", dataQuality: str = "", limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(db_session)
):
    stmt = select(Voter)
    conds = []
    if query:
        q = f"%{query.strip()}%"
        conds.append(or_(Voter.name.ilike(q), Voter.local_name.ilike(q), Voter.epic_id.ilike(q), Voter.relative_name.ilike(q), Voter.phone.ilike(q), Voter.house_no.ilike(q)))
    if firstName: conds.append(Voter.name.ilike(f"%{firstName.strip()}%"))
    if lastName: conds.append(Voter.name.ilike(f"%{lastName.strip()}%"))
    if relativeName: conds.append(Voter.relative_name.ilike(f"%{relativeName.strip()}%"))
    if acNo: conds.append(Voter.ac_no == acNo.strip())
    if partNo: conds.append(Voter.part_no == partNo.strip())
    if serialNo: conds.append(Voter.serial_no == serialNo.strip())
    if epicId: conds.append(Voter.epic_id.ilike(f"%{epicId.strip()}%"))
    if phone: conds.append(Voter.phone.ilike(f"%{phone.strip()}%"))
    if gender: conds.append(Voter.gender == gender)
    if dataQuality: conds.append(Voter.data_quality == dataQuality)
    if conds: stmt = stmt.where(*conds)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Voter.part_no, Voter.serial_no, Voter.name).limit(limit).offset(offset)).all()
    return {"total": total, "items": [voter_dict(v) for v in rows]}

@app.get("/voters/{voter_id}", dependencies=[Depends(require_api_key)])
def get_voter(voter_id: int, db: Session = Depends(db_session)):
    v = db.get(Voter, voter_id)
    if not v: raise HTTPException(404, "Voter not found")
    data = voter_dict(v)
    if v.family_key:
        family = db.scalars(select(Voter).where(Voter.family_key == v.family_key, Voter.id != v.id).limit(25)).all()
        data["family"] = [voter_dict(x) for x in family]
    else:
        data["family"] = []
    return data

@app.post("/voters", dependencies=[Depends(require_api_key)])
def create_voter(payload: VoterIn, db: Session = Depends(db_session)):
    v = Voter()
    apply_voter(v, payload)
    db.add(v)
    try:
        db.commit(); db.refresh(v)
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "A voter with this EPIC ID already exists")
    return voter_dict(v)

@app.put("/voters/{voter_id}", dependencies=[Depends(require_api_key)])
def update_voter(voter_id: int, payload: VoterIn, db: Session = Depends(db_session)):
    v = db.get(Voter, voter_id)
    if not v: raise HTTPException(404, "Voter not found")
    apply_voter(v, payload)
    try:
        db.commit(); db.refresh(v)
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "A voter with this EPIC ID already exists")
    return voter_dict(v)

@app.delete("/voters/{voter_id}", dependencies=[Depends(require_api_key)])
def delete_voter(voter_id: int, db: Session = Depends(db_session)):
    v = db.get(Voter, voter_id)
    if not v: raise HTTPException(404, "Voter not found")
    db.delete(v); db.commit(); return {"ok": True}

@app.get("/team", dependencies=[Depends(require_api_key)])
def list_team(db: Session = Depends(db_session)):
    rows = db.scalars(select(TeamMember).order_by(TeamMember.active.desc(), TeamMember.name)).all()
    return [{"id": x.id, "name": x.name, "phone": x.phone, "role": x.role, "area": x.area, "active": x.active} for x in rows]

@app.post("/team", dependencies=[Depends(require_api_key)])
def add_team(payload: TeamIn, db: Session = Depends(db_session)):
    x = TeamMember(name=payload.name, phone=payload.phone, role=payload.role, area=payload.area, active=payload.active)
    db.add(x); db.commit(); db.refresh(x)
    return {"id": x.id, "name": x.name, "phone": x.phone, "role": x.role, "area": x.area, "active": x.active}

@app.delete("/team/{member_id}", dependencies=[Depends(require_api_key)])
def delete_team(member_id: int, db: Session = Depends(db_session)):
    x = db.get(TeamMember, member_id)
    if not x: raise HTTPException(404, "Team member not found")
    db.delete(x); db.commit(); return {"ok": True}

CSV_HEADERS = ["serialNo","epicId","name","localName","relationType","relativeName","houseNo","age","gender","acNo","partNo","boothNo","boothSerialNo","ward","sectionAddress","boothAddress","phone","familyKey","assignedTo","recordStatus","dataQuality","notes","sourcePage"]

@app.get("/export/voters.csv", dependencies=[Depends(require_api_key)])
def export_csv(db: Session = Depends(db_session)):
    rows = db.scalars(select(Voter).order_by(Voter.part_no, Voter.serial_no)).all()
    sio = io.StringIO(); writer = csv.DictWriter(sio, fieldnames=CSV_HEADERS); writer.writeheader()
    for v in rows: writer.writerow({k: voter_dict(v).get(k, "") for k in CSV_HEADERS})
    data = "\ufeff" + sio.getvalue()
    return StreamingResponse(iter([data.encode("utf-8")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=voters.csv"})

@app.get("/export/voters.xlsx", dependencies=[Depends(require_api_key)])
def export_xlsx(db: Session = Depends(db_session)):
    rows = db.scalars(select(Voter).order_by(Voter.part_no, Voter.serial_no)).all()
    out = DATA_DIR / f"voters-{int(time.time())}.xlsx"
    wb = xlsxwriter.Workbook(out, {"strings_to_urls": False, "strings_to_formulas": False})
    ws = wb.add_worksheet("Voters")
    header_fmt = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#6D28D9", "align": "center", "valign": "vcenter", "border": 1})
    text_fmt = wb.add_format({"valign": "top", "text_wrap": True})
    headers = ["Serial No","EPIC ID","Name","Local/Hindi Name","Relation","Relative Name","House No","Age","Gender","AC No","Part No","Booth No","Booth Serial No","Ward / Area","Section Address","Booth Address","Phone","Family Key","Assigned To","Record Status","Data Quality","Notes","Source Page"]
    keys = CSV_HEADERS
    for col, h in enumerate(headers): ws.write(0, col, h, header_fmt)
    for row_idx, voter in enumerate(rows, 1):
        data = voter_dict(voter)
        for col, key in enumerate(keys):
            value = data.get(key, "")
            ws.write(row_idx, col, value if value is not None else "", text_fmt)
    if rows:
        ws.autofilter(0, 0, len(rows), len(headers)-1)
    ws.freeze_panes(1, 0)
    widths = [11,18,24,24,12,24,12,8,10,10,10,10,14,16,30,30,16,18,18,14,14,30,11]
    for col, width in enumerate(widths): ws.set_column(col, col, width)
    ws.set_row(0, 24)
    ws.activate()
    wb.close()
    return FileResponse(out, filename="voters.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

CSV_ALIASES = {
    "serial no":"serialNo", "serial":"serialNo", "क्रमांक":"serialNo",
    "epic id":"epicId", "epic":"epicId", "voter id":"epicId",
    "name":"name", "नाम":"name", "local name":"localName",
    "relation":"relationType", "relative name":"relativeName", "संबंधित व्यक्ति का नाम":"relativeName",
    "house no":"houseNo", "मकान संख्या":"houseNo", "age":"age", "आयु":"age",
    "gender":"gender", "लिंग":"gender", "ac":"acNo", "ac no":"acNo",
    "part no":"partNo", "भाग संख्या":"partNo", "booth no":"boothNo", "बूथ संख्या":"boothNo",
    "ward":"ward", "phone":"phone", "mobile":"phone", "notes":"notes", "source page":"sourcePage"
}

def canonical_csv_row(row: dict) -> dict:
    out = {k: "" for k in CSV_HEADERS}
    for raw_key, value in row.items():
        key = (raw_key or "").strip()
        simple = re.sub(r"\s+", " ", key.lower())
        target = key if key in out else CSV_ALIASES.get(simple)
        if target in out:
            out[target] = value or ""
    return out

@app.post("/import/csv", dependencies=[Depends(require_api_key)])
async def import_csv(file: UploadFile = File(...), db: Session = Depends(db_session)):
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024: raise HTTPException(413, "CSV is too large")
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    inserted = updated = rejected = 0; errors = []
    for idx, row in enumerate(reader, start=2):
        try:
            normalized = canonical_csv_row(row)
            normalized["age"] = int(normalized.get("age") or 0)
            normalized["sourcePage"] = int(normalized.get("sourcePage") or 0)
            payload = VoterIn(**normalized)
            existing = db.scalar(select(Voter).where(Voter.epic_id == payload.epicId))
            if existing:
                apply_voter(existing, payload); updated += 1
            else:
                v = Voter(); apply_voter(v, payload); db.add(v); inserted += 1
            if (inserted + updated) % 500 == 0: db.commit()
        except Exception as e:
            rejected += 1
            if len(errors) < 25: errors.append({"row": idx, "error": str(e)})
    db.commit()
    return {"ok": True, "inserted": inserted, "updated": updated, "rejected": rejected, "errors": errors}

# ---------- Electoral-roll PDF extraction ----------
# Tuned for the Uttar Pradesh Hindi 3-column voter-card roll layout used by
# EROLLGEN/SIR final-roll PDFs.  The key design choice is positional OCR:
# each page is OCR'd in Hindi and English, then tokens are assigned to one
# voter card by coordinates.  This prevents text from neighbouring voters
# bleeding into the same Excel row.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOBS_DIR = DATA_DIR / "jobs"; JOBS_DIR.mkdir(exist_ok=True)
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_MB", "80")) * 1024 * 1024
OCR_SCALE = float(os.environ.get("ROLL_OCR_SCALE", "2.5"))

# Canonical card geometry on the 595 x 842 point source pages.
ROLL_BASE_W = 595.0
ROLL_BASE_H = 842.0
ROLL_X0 = [14.0, 204.0, 394.0]
ROLL_Y0 = [27.5, 106.5, 186.0, 265.5, 345.0, 424.5, 504.0, 583.0, 662.5, 741.5]
ROLL_CARD_W = 185.5
ROLL_CARD_H = 75.0

EPIC_STD_RE = re.compile(r"^[A-Z]{3}\d{7}$")
EPIC_LEGACY_RE = re.compile(r"^[A-Z]{2}/\d{2}/\d{3}/\d{7}$")


def _ocr_conf(value) -> float:
    try: return float(value)
    except Exception: return -1.0


def _ocr_tokens(data: dict, scale: float) -> list[dict]:
    rows=[]
    n=len(data.get("text", []))
    for i in range(n):
        text=str(data["text"][i] or "").strip()
        if not text: continue
        left=float(data["left"][i]); top=float(data["top"][i])
        width=float(data["width"][i]); height=float(data["height"][i])
        rows.append({
            "text":text, "conf":_ocr_conf(data["conf"][i]),
            "left":left, "top":top, "width":width, "height":height,
            "cx":(left+width/2)/scale, "cy":(top+height/2)/scale,
            "x":left/scale, "y":top/scale,
        })
    return rows


def _card_rect(page: fitz.Page, row: int, col: int) -> fitz.Rect:
    sx=page.rect.width/ROLL_BASE_W; sy=page.rect.height/ROLL_BASE_H
    return fitz.Rect(
        ROLL_X0[col]*sx, ROLL_Y0[row]*sy,
        (ROLL_X0[col]+ROLL_CARD_W)*sx,
        (ROLL_Y0[row]+ROLL_CARD_H)*sy,
    )


def _tokens_in_rect(tokens: list[dict], rect: fitz.Rect) -> list[dict]:
    out=[]
    for t in tokens:
        if rect.x0 <= t["cx"] <= rect.x1 and rect.y0 <= t["cy"] <= rect.y1:
            q=dict(t)
            q["rx"]=t["cx"]-rect.x0; q["ry"]=t["cy"]-rect.y0
            out.append(q)
    return out


def _line_text(tokens: list[dict], y0: float, y1: float, x1: float=135.0) -> str:
    line=[t for t in tokens if y0 <= t.get("ry",999) < y1 and t.get("rx",999) < x1]
    line.sort(key=lambda t:t["left"])
    return " ".join(t["text"] for t in line).strip()


def _after_label(text: str, labels: list[str]) -> str:
    text=re.sub(r"\s+", " ", text or "").strip()
    for label in labels:
        pos=text.find(label)
        if pos >= 0:
            tail=text[pos+len(label):]
            return re.sub(r"^[\s:：\-|]+", "", tail).strip()
    if ":" in text: return text.split(":",1)[1].strip()
    return ""


def _clean_person(text: str) -> str:
    text=re.sub(r"\s+", " ", text or "").strip(" :|-\t")
    text=re.split(r"\s+(?:पिता|पति|माता|मकान|आयु|लिंग)\b", text)[0]
    return text[:160].strip()


def _normalize_epic(raw: str) -> str:
    s=re.sub(r"\s+", "", raw or "").upper().replace("\\", "/").replace("|", "/")
    s=re.sub(r"[^A-Z0-9/]", "", s)
    s=re.sub(r"/+", "/", s)
    if not s: return ""
    if "/" in s:
        parts=s.split("/")
        prefix=re.sub(r"[^A-Z]", "", parts[0])
        nums=[]
        trans=str.maketrans({"O":"0","Q":"0","D":"0","I":"1","L":"1","S":"5","B":"8","G":"6"})
        for part in parts[1:]: nums.append(re.sub(r"\D", "", part.translate(trans)))
        s="/".join([prefix]+nums)
        return s if EPIC_LEGACY_RE.fullmatch(s) else ""
    # Standard EPIC is 3 letters + 7 digits. OCR often reads O as 0 in prefix.
    windows=[s] if len(s)==10 else [s[i:i+10] for i in range(max(0,len(s)-9))]
    pre_map={"0":"O","1":"I","5":"S","8":"B","6":"G"}
    num_map={"O":"0","Q":"0","D":"0","I":"1","L":"1","S":"5","B":"8","G":"6"}
    for c in windows:
        if len(c)!=10: continue
        prefix="".join(pre_map.get(ch,ch) for ch in c[:3])
        digits="".join(num_map.get(ch,ch) for ch in c[3:])
        out=prefix+digits
        if EPIC_STD_RE.fullmatch(out): return out
    return ""


def _extract_part_no(tokens: list[dict]) -> str:
    # Part number is printed in the upper-right header on voter pages.
    cands=[]
    for t in tokens:
        if t["cy"] < 18 and t["cx"] > 500:
            m=re.search(r"\d{1,4}", t["text"])
            if m: cands.append((t["conf"],m.group(0)))
    return max(cands, default=(-1,""))[1]


def _extract_ac_no(tokens: list[dict]) -> str:
    # The AC number appears after the constituency label on the first header line.
    # Restricting by coordinates avoids accidentally picking up the year 2026.
    cands=[]
    for t in tokens:
        if t["cy"] < 16 and 155 < t["cx"] < 260:
            m=re.search(r"(?<!\d)(\d{1,3})(?!\d)", t["text"])
            if m: cands.append((t["conf"],m.group(1)))
    return max(cands, default=(-1,""))[1]


def _extract_section(tokens: list[dict]) -> str:
    line=[t for t in tokens if 14 <= t["cy"] < 28 and t["cx"] < 250]
    line.sort(key=lambda t:t["left"])
    text=" ".join(t["text"] for t in line)
    text=_after_label(text,["अनुभाग संख्या और नाम","अनुभाग","Section"])
    return re.sub(r"\s+", " ", text).strip()[:180]


def _page_list_type(tokens: list[dict]) -> str:
    line=" ".join(t["text"] for t in sorted(tokens,key=lambda x:(x["top"],x["left"])) if t["cy"]<30)
    if "परिवर्धन" in line or "addition" in line.lower(): return "Addition"
    return "Original"


def _target_epic_ocr(page: fitz.Page, rect: fitz.Rect) -> tuple[str,float]:
    clip=fitz.Rect(rect.x0+70, rect.y0+1, rect.x1-1, rect.y0+18)
    pix=page.get_pixmap(matrix=fitz.Matrix(4,4), clip=clip, alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes("png")))
    config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"
    raw=pytesseract.image_to_string(img,lang="eng",config=config).strip()
    return _normalize_epic(raw), 45.0 if raw else 0.0


def _fallback_card_text(page: fitz.Page, rect: fitz.Rect) -> str:
    pix=page.get_pixmap(matrix=fitz.Matrix(4,4),clip=rect,alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img,lang="hin",config="--psm 6")


def _parse_fallback_text(text: str) -> dict:
    text=text or ""
    def one(pattern):
        m=re.search(pattern,text,re.I|re.M); return _clean_person(m.group(1)) if m else ""
    name=one(r"(?:^|\n)\s*नाम\s*[:：-]?\s*([^\n]{2,120})")
    rel_type=""; relative=""
    for patt,typ in [
        (r"पिता\s*का\s*नाम\s*[:：-]?\s*([^\n]{2,120})","Father"),
        (r"पति\s*का\s*नाम\s*[:：-]?\s*([^\n]{2,120})","Husband"),
        (r"माता\s*का\s*नाम\s*[:：-]?\s*([^\n]{2,120})","Mother")]:
        m=re.search(patt,text)
        if m: rel_type=typ; relative=_clean_person(m.group(1)); break
    gender="Female" if ("महिला" in text or "हिला" in text) else "Male" if ("पुरुष" in text or "रुष" in text) else "Other" if "अन्य" in text else ""
    age=0
    m=re.search(r"आयु\s*[:：-]?\s*(\d{1,3})",text)
    if m and 18<=int(m.group(1))<=120: age=int(m.group(1))
    return {"name":name,"relationType":rel_type,"relativeName":relative,"gender":gender,"age":age}




def _target_gender_ocr(page: fitz.Page, rect: fitz.Rect) -> str:
    clip=fitz.Rect(rect.x0+2,rect.y0+42,rect.x0+92,rect.y0+63)
    pix=page.get_pixmap(matrix=fitz.Matrix(4,4),clip=clip,alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes("png")))
    raw=pytesseract.image_to_string(img,lang="hin",config="--psm 7")
    compact=re.sub(r"\s+","",raw)
    if "महिला" in compact or "हिला" in compact: return "Female"
    if "पुरुष" in compact or "रुष" in compact: return "Male"
    if "अन्य" in compact: return "Other"
    return ""

def _card_diagonal_stamp(card_gray: np.ndarray) -> bool:
    # DELETED stamps are large ~33 degree diagonal strokes. Ordinary voter text
    # does not create multiple long parallel strokes at this angle.
    edges=cv2.Canny(card_gray,50,150)
    lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=24,minLineLength=40,maxLineGap=7)
    hits=[]
    if lines is None: return False
    h,w=card_gray.shape
    for x1,y1,x2,y2 in lines[:,0]:
        dx=x2-x1; dy=y2-y1
        angle=abs(math.degrees(math.atan2(dy,dx)))
        length=math.hypot(dx,dy)
        if 25<=angle<=42 and length>=48 and (x1+x2)/2 < 0.76*w:
            hits.append(length)
    return len(hits)>=2 and max(hits,default=0)>=70


def _deletion_code(page: fitz.Page, rect: fitz.Rect) -> str:
    clip=fitz.Rect(rect.x0+1,rect.y0+1,rect.x0+35,rect.y0+16)
    pix=page.get_pixmap(matrix=fitz.Matrix(6,6),clip=clip,alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes("png")))
    raw=pytesseract.image_to_string(img,lang="eng",config="--psm 6 -c tessedit_char_whitelist=ESRMQ0123456789").upper()
    for code in "ESRMQ":
        if code in raw: return code
    return ""

DELETION_REASON={"E":"Deceased / मृतक","S":"Transferred / स्थानांतरित","R":"Duplicate / पुनरावृत्ति","M":"Missing / लापता","Q":"Ineligible / अयोग्य"}


def _parse_roll_card(page: fitz.Page, rect: fitz.Rect, hin_tokens: list[dict], eng_tokens: list[dict], defaults: dict, card_gray: np.ndarray) -> dict | None:
    h=_tokens_in_rect(hin_tokens,rect); e=_tokens_in_rect(eng_tokens,rect)
    # Rebase token positions to this card.
    for q in (h,e):
        for t in q:
            t["rx"]=t["cx"]-rect.x0; t["ry"]=t["cy"]-rect.y0

    # Serial number in top-left box.
    serial=""; serial_conf=0.0
    for t in e:
        if t["ry"]<16 and t["rx"]<78:
            m=re.fullmatch(r"\D*(\d{1,4})\D*",t["text"])
            if m and t["conf"]>=serial_conf:
                serial=m.group(1); serial_conf=t["conf"]

    # EPIC in the top-right band.
    epic=""; epic_conf=0.0
    for source in (e,h):
        for t in sorted(source,key=lambda z:z["left"]):
            if t["ry"]<17 and t["rx"]>78:
                candidate=_normalize_epic(t["text"])
                if candidate and (not epic or t["conf"]>epic_conf):
                    epic=candidate; epic_conf=t["conf"]
    if not epic or epic_conf<35:
        target_epic,target_conf=_target_epic_ocr(page,rect)
        if target_epic:
            epic=target_epic; epic_conf=max(target_conf,55.0)

    nline=_line_text(h,15,27)
    rline=_line_text(h,25,37)
    hline=_line_text(h,34,46)
    aline=_line_text(h,43,59)
    name=_clean_person(_after_label(nline,["नाम","Name"]))

    rel_type=""; relative=""
    if "पिता" in rline: rel_type="Father"
    elif "पति" in rline: rel_type="Husband"
    elif "माता" in rline: rel_type="Mother"
    elif "अन्य" in rline: rel_type="Other"
    if rel_type:
        relative=_clean_person(_after_label(rline,["पिता का नाम","पति का नाम","माता का नाम","नाम"]))

    # English OCR is substantially more reliable for Arabic digits in these scans.
    house=""; house_conf=0.0
    for t in sorted(e,key=lambda z:z["left"]):
        if 34<=t["ry"]<45 and 28<t["rx"]<126:
            cand=re.sub(r"[^A-Z0-9/\-]","",t["text"].upper())
            if cand and re.search(r"\d",cand) and len(cand)<=18:
                house=cand; house_conf=t["conf"]
    if not house:
        house=re.sub(r"\s.*$","",_after_label(hline,["मकान संख्या","मकान नंबर","House No"])).strip()

    age=0; age_conf=0.0
    for t in sorted(e,key=lambda z:z["left"]):
        if 43<=t["ry"]<59 and 8<t["rx"]<72:
            digits=re.sub(r"\D","",t["text"])
            if digits and 18<=int(digits)<=120:
                age=int(digits); age_conf=t["conf"]; break
    if not age:
        m=re.search(r"(?:आयु|Age)\s*[:：-]?\s*(\d{1,3})",aline)
        if m and 18<=int(m.group(1))<=120: age=int(m.group(1))

    gender="Female" if "महिला" in aline else "Male" if "पुरुष" in aline else "Other" if "अन्य" in aline else ""

    # If the page-level OCR missed a core Hindi field, retry only this card.
    if not name or not gender or (not relative and rel_type):
        fb=_parse_fallback_text(_fallback_card_text(page,rect))
        name=name or fb["name"]
        gender=gender or fb["gender"]
        age=age or fb["age"]
        if not relative:
            rel_type=rel_type or fb["relationType"]; relative=fb["relativeName"]
    if not gender:
        gender=_target_gender_ocr(page,rect)

    # Ignore empty grid slots on partially-filled supplementary pages.
    if not epic and not name and not age: return None

    deleted=_card_diagonal_stamp(card_gray)
    dcode=_deletion_code(page,rect) if deleted else ""
    status="Deleted" if deleted else "Active"

    reasons=[]
    if not epic: reasons.append("EPIC missing/invalid")
    if epic and epic_conf<35: reasons.append("EPIC low OCR confidence")
    if not name: reasons.append("name missing")
    if not serial: reasons.append("serial OCR missing; grid sequence will be used")
    if not age: reasons.append("age missing/invalid")
    if not gender: reasons.append("gender missing")
    if house and house_conf and house_conf<35: reasons.append("house number low OCR confidence")

    score=25+(25 if epic else 0)+(20 if name else 0)+(10 if age else 0)+(10 if gender else 0)+(5 if relative else 0)+(5 if house else 0)
    return {
        "serialNo":serial,"epicId":epic,"name":name,"localName":name,
        "relationType":rel_type,"relativeName":relative,"houseNo":house,
        "age":age,"gender":gender or "Other","acNo":defaults.get("acNo",""),
        "partNo":defaults.get("partNo",""),"boothNo":"","boothSerialNo":"",
        "ward":defaults.get("section",""),"sectionAddress":defaults.get("section",""),
        "boothAddress":"","phone":"","familyKey":"","assignedTo":"",
        "recordStatus":status,"dataQuality":"Review","notes":"","sourcePage":defaults.get("pageNo",0),
        "confidence":min(100,score),"reviewReason":", ".join(reasons),
        "listType":defaults.get("listType","Original"),"deletionCode":dcode,
        "deletionReason":DELETION_REASON.get(dcode, "Deleted stamp detected" if deleted else ""),
        "_serialConf":serial_conf,
    }


def _infer_serial_base(rows_by_slot: list[dict|None]) -> int | None:
    offsets=[]
    for idx,row in enumerate(rows_by_slot):
        if row and str(row.get("serialNo","")).isdigit():
            offsets.append(int(row["serialNo"])-idx)
    if not offsets: return None
    counts={v:offsets.count(v) for v in set(offsets)}
    return max(counts,key=lambda v:(counts[v],-abs(v-int(np.median(offsets)))))


def _extract_summary_expectations(doc: fitz.Document) -> dict:
    # Net-elector totals appear in the final summary row in this roll family.
    try:
        page=doc[-1]; sx=page.rect.width/ROLL_BASE_W; sy=page.rect.height/ROLL_BASE_H
        clip=fitz.Rect(390*sx,285*sy,590*sx,306*sy)
        pix=page.get_pixmap(matrix=fitz.Matrix(6,6),clip=clip,alpha=False)
        img=Image.open(io.BytesIO(pix.tobytes("png")))
        raw=pytesseract.image_to_string(img,lang="eng",config="--psm 6 -c tessedit_char_whitelist=0123456789 ")
        nums=[int(x) for x in re.findall(r"\d+",raw)]
        if len(nums)>=4:
            male,female,other,total=nums[-4:]
            return {"male":male,"female":female,"other":other,"total":total}
    except Exception:
        log.exception("Could not read roll summary totals")
    return {}


def _extract_roll_page(page: fitz.Page, page_no: int) -> tuple[list[dict],dict]:
    pix=page.get_pixmap(matrix=fitz.Matrix(OCR_SCALE,OCR_SCALE),alpha=False)
    img=Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    # Two passes are intentional: Hindi gives cleaner names/relations, while
    # English gives much better EPIC IDs, serials, house numbers and ages.
    # Run them sequentially; parallel Tesseract processes can thrash memory on
    # ordinary laptops and actually become much slower.
    hin_data=pytesseract.image_to_data(img,lang="hin",config="--psm 11",output_type=pytesseract.Output.DICT)
    eng_data=pytesseract.image_to_data(img,lang="eng",config="--psm 11",output_type=pytesseract.Output.DICT)
    hin_tokens=_ocr_tokens(hin_data,OCR_SCALE)
    eng_tokens=_ocr_tokens(eng_data,OCR_SCALE)
    defaults={
        "partNo":_extract_part_no(hin_tokens) or _extract_part_no(eng_tokens),
        "acNo":_extract_ac_no(eng_tokens),
        "section":_extract_section(hin_tokens),
        "listType":_page_list_type(hin_tokens),
        "pageNo":page_no,
    }
    gray=np.array(img.convert("L"))
    sx=OCR_SCALE
    slots=[]
    for row in range(10):
        for col in range(3):
            rect=_card_rect(page,row,col)
            x0=max(0,int(rect.x0*sx)); y0=max(0,int(rect.y0*sx)); x1=min(gray.shape[1],int(rect.x1*sx)); y1=min(gray.shape[0],int(rect.y1*sx))
            crop=gray[y0:y1,x0:x1]
            slots.append(_parse_roll_card(page,rect,hin_tokens,eng_tokens,defaults,crop))
    base=_infer_serial_base(slots)
    rows=[]
    for idx,row in enumerate(slots):
        if not row: continue
        if base is not None:
            expected=base+idx
            if row.get("serialNo") != str(expected):
                row["serialNo"]=str(expected)
                # Sequence inference is highly reliable in this fixed 3-column roll.
                rr=[x for x in row.get("reviewReason","").split(", ") if x and not x.startswith("serial OCR missing")]
                row["reviewReason"]=" ,".join(rr).replace(" ,",", ")
        core_ok=bool(row["epicId"] and row["name"] and row["serialNo"] and row["age"] and row["gender"] in {"Male","Female","Other"})
        # Deleted records are valid extracted records, but are never auto-committed as active.
        serious=[r for r in row.get("reviewReason","").split(", ") if r and not r.startswith("house number")]
        row["dataQuality"]="Verified" if core_ok and not serious else "Review"
        row.pop("_serialConf",None)
        rows.append(row)
    return rows,defaults


def extract_pdf_job(job_id: str, pdf_path: Path):
    try:
        with JOBS_LOCK: JOBS[job_id].update(status="processing",progress=1,message="Opening electoral roll")
        doc=fitz.open(pdf_path)
        expected=_extract_summary_expectations(doc)
        all_rows=[]; warnings=[]; pages_processed=0
        # This roll family uses page 1 metadata, page 2 polling-station imagery,
        # voter cards from page 3 through the penultimate page, and a final summary.
        page_indexes=range(2,max(2,len(doc)-1)) if len(doc)>=4 else range(len(doc))
        for n,idx in enumerate(page_indexes,1):
            page=doc[idx]
            rows,defaults=_extract_roll_page(page,idx+1)
            if rows:
                all_rows.extend(rows); pages_processed+=1
            pct=4+int(n/max(1,len(range(2,max(2,len(doc)-1))))*86)
            with JOBS_LOCK: JOBS[job_id].update(progress=pct,message=f"Reading voter cards — page {idx+1}/{len(doc)}")

        # Dedupe by EPIC only when a valid EPIC is present; never collapse rows just because OCR missed an EPIC.
        best={}; no_epic=[]
        for r in all_rows:
            if not r.get("epicId"): no_epic.append(r); continue
            key=r["epicId"]
            if key not in best or r.get("confidence",0)>best[key].get("confidence",0): best[key]=r
        rows=list(best.values())+no_epic
        rows.sort(key=lambda r:(int(r["serialNo"]) if str(r.get("serialNo","")).isdigit() else 10**9,r.get("sourcePage",0)))

        # Sequence diagnostics reveal dropped cards immediately.
        serials=sorted(int(r["serialNo"]) for r in rows if str(r.get("serialNo","")).isdigit())
        gaps=[]
        if serials:
            present=set(serials)
            gaps=[x for x in range(serials[0],serials[-1]+1) if x not in present]
            if gaps: warnings.append(f"Serial gaps: {', '.join(map(str,gaps[:40]))}{'…' if len(gaps)>40 else ''}")

        active=[r for r in rows if r.get("recordStatus")!="Deleted"]
        deleted=[r for r in rows if r.get("recordStatus")=="Deleted"]
        original=[r for r in rows if r.get("listType")=="Original"]
        additions=[r for r in rows if r.get("listType")=="Addition"]
        actual={
            "total":len(active),
            "male":sum(1 for r in active if r.get("gender")=="Male"),
            "female":sum(1 for r in active if r.get("gender")=="Female"),
            "other":sum(1 for r in active if r.get("gender")=="Other"),
        }
        summary_match=True
        if expected:
            for key in ("total","male","female","other"):
                if actual.get(key)!=expected.get(key):
                    summary_match=False
                    warnings.append(f"Summary mismatch for {key}: extracted {actual.get(key)}, PDF summary {expected.get(key)}")

        meta={
            "pages":len(doc),"voterPages":pages_processed,"ocrPages":pages_processed,
            "warnings":warnings,"expected":expected,"actual":actual,"summaryMatch":summary_match,
            "originalRows":len(original),"additionRows":len(additions),"deletedRows":len(deleted),
            "serialGaps":gaps,
        }
        xlsx=JOBS_DIR/f"{job_id}.xlsx"; make_review_xlsx(rows,meta,xlsx)
        (JOBS_DIR/f"{job_id}.json").write_text(json.dumps(rows,ensure_ascii=False),encoding="utf-8")
        clean_rows=sum(1 for r in rows if r["dataQuality"]=="Verified" and r["recordStatus"]!="Deleted")
        review_rows=sum(1 for r in rows if r["dataQuality"]!="Verified")
        with JOBS_LOCK:
            JOBS[job_id].update(
                status="done",progress=100,message="Ready — electoral roll extracted",rows=rows,
                pages=len(doc),ocrPages=pages_processed,extractedRows=len(rows),cleanRows=clean_rows,reviewRows=review_rows,
                activeRows=len(active),deletedRows=len(deleted),originalRows=len(original),additionRows=len(additions),
                summaryMatch=summary_match,expectedSummary=expected,actualSummary=actual,warnings=warnings,xlsx=str(xlsx),
                template="UP Hindi 3-column electoral roll",
            )
    except Exception as e:
        log.exception("PDF job failed")
        with JOBS_LOCK: JOBS[job_id].update(status="error",message=f"Conversion failed: {e}",progress=100)


def _xlsx_safe(value):
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    # Remove XML-invalid control characters while preserving Hindi/Unicode text.
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", str(value))

def make_review_xlsx(rows: list[dict], meta: dict, path: Path):
    # XlsxWriter is deliberately used here instead of openpyxl tables.
    # It produces a conservative OOXML workbook that opens in Excel without a repair prompt.
    wb = xlsxwriter.Workbook(path, {"strings_to_urls": False, "strings_to_formulas": False})
    wb.set_properties({"title": "Electoral Roll Extraction", "subject": "Voter data extracted from electoral roll PDF", "company": "Constituency Manager"})

    purple = "#6D28D9"
    header_fmt = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": purple, "align": "center", "valign": "vcenter", "border": 1})
    cell_fmt = wb.add_format({"valign": "top", "text_wrap": True, "border": 0})
    integer_fmt = wb.add_format({"valign": "top", "num_format": "0"})
    verified_fmt = wb.add_format({"bg_color": "#DCFCE7", "font_color": "#166534"})
    review_fmt = wb.add_format({"bg_color": "#FEF3C7", "font_color": "#92400E"})
    deleted_fmt = wb.add_format({"bg_color": "#FEE2E2", "font_color": "#991B1B"})
    summary_key_fmt = wb.add_format({"bold": True, "bg_color": "#F3E8FF", "border": 1})
    summary_val_fmt = wb.add_format({"text_wrap": True, "border": 1, "valign": "top"})

    active_rows = [r for r in rows if r.get("recordStatus") != "Deleted"]
    verified_rows = [r for r in active_rows if r.get("dataQuality") == "Verified"]
    review_rows = [r for r in rows if r.get("dataQuality") != "Verified"]
    deleted_rows = [r for r in rows if r.get("recordStatus") == "Deleted"]

    # Clean client-facing sheet: voter data only. Quality-control metadata is kept out of this sheet.
    client_headers = ["Serial No","EPIC ID","Name","Relation","Relative Name","House No","Age","Gender","AC No","Part No","Section","List Type","Record Status"]
    client_keys = ["serialNo","epicId","name","relationType","relativeName","houseNo","age","gender","acNo","partNo","sectionAddress","listType","recordStatus"]
    client_widths = [11,20,24,12,24,12,8,10,9,9,24,12,13]

    # Audit sheets keep traceability and review diagnostics.
    audit_headers = client_headers + ["Deletion Code","Deletion Reason","Source Page","Confidence","Data Quality","Review Reason"]
    audit_keys = client_keys + ["deletionCode","deletionReason","sourcePage","confidence","dataQuality","reviewReason"]
    audit_widths = client_widths + [13,28,11,11,14,42]

    def write_sheet(name, data_rows, headers, keys, widths, tab_color, audit=False):
        ws = wb.add_worksheet(name)
        ws.set_tab_color(tab_color)
        ws.freeze_panes(1, 0)
        ws.set_row(0, 24)
        for col, h in enumerate(headers):
            ws.write(0, col, h, header_fmt)
            ws.set_column(col, col, widths[col])
        for ridx, row in enumerate(data_rows, 1):
            for col, key in enumerate(keys):
                value = _xlsx_safe(row.get(key, ""))
                fmt = integer_fmt if key in {"age", "sourcePage", "confidence"} and isinstance(value, (int, float)) else cell_fmt
                ws.write(ridx, col, value, fmt)
            # Visual row cue only; no formulas or tables that can trigger Excel repair.
            if audit:
                q = row.get("dataQuality", "")
                status = row.get("recordStatus", "")
                if status == "Deleted":
                    ws.set_row(ridx, None, deleted_fmt)
                elif q == "Verified":
                    pass
        if data_rows:
            ws.autofilter(0, 0, len(data_rows), len(headers)-1)
        return ws

    voters_ws = write_sheet("Voters", rows, client_headers, client_keys, client_widths, "#6D28D9")
    write_sheet("Verified Active", verified_rows, client_headers, client_keys, client_widths, "#22C55E")
    write_sheet("Review Queue", review_rows, audit_headers, audit_keys, audit_widths, "#F59E0B", audit=True)
    write_sheet("Deleted", deleted_rows, audit_headers, audit_keys, audit_widths, "#EF4444", audit=True)
    write_sheet("Audit Review", rows, audit_headers, audit_keys, audit_widths, "#64748B", audit=True)

    summary = wb.add_worksheet("Summary")
    summary.set_tab_color("#3B82F6")
    summary.freeze_panes(1, 0)
    summary.write_row(0, 0, ["Metric", "Value"], header_fmt)
    summary.set_column(0, 0, 34)
    summary.set_column(1, 1, 95)
    summary_data = [
        ("Template", "UP Hindi 3-column electoral roll"),
        ("PDF pages", meta.get("pages", 0)), ("Voter pages processed", meta.get("voterPages", 0)),
        ("Cards extracted", len(rows)), ("Active voters extracted", len(active_rows)), ("Deleted cards", meta.get("deletedRows", 0)),
        ("Original-roll cards", meta.get("originalRows", 0)), ("Addition-list cards", meta.get("additionRows", 0)),
        ("Verified active rows", len(verified_rows)), ("Review rows", len(review_rows)),
        ("Extracted male", sum(1 for r in active_rows if r.get("gender") == "Male")),
        ("Extracted female", sum(1 for r in active_rows if r.get("gender") == "Female")),
        ("Extracted other", sum(1 for r in active_rows if r.get("gender") == "Other")),
        ("PDF summary total", meta.get("expected", {}).get("total", "Not read")),
        ("PDF summary male", meta.get("expected", {}).get("male", "Not read")),
        ("PDF summary female", meta.get("expected", {}).get("female", "Not read")),
        ("Summary cross-check", "MATCH" if meta.get("summaryMatch") else "CHECK REQUIRED"),
        ("Serial gaps", ", ".join(map(str, meta.get("serialGaps", []))) or "None"),
        ("Warnings", " | ".join(meta.get("warnings", [])) or "None"),
    ]
    for ridx, (key, value) in enumerate(summary_data, 1):
        summary.write(ridx, 0, _xlsx_safe(key), summary_key_fmt)
        summary.write(ridx, 1, _xlsx_safe(value), summary_val_fmt)

    # Open on the clean client-facing voter sheet.
    voters_ws.activate()
    voters_ws.select()
    wb.close()

@app.post("/pdf/jobs", dependencies=[Depends(require_api_key)])
async def create_pdf_job(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"): raise HTTPException(400,"Please upload a PDF")
    data=await file.read(MAX_PDF_BYTES+1)
    if len(data)>MAX_PDF_BYTES: raise HTTPException(413,"PDF is too large")
    job_id=uuid.uuid4().hex[:16]; pdf_path=JOBS_DIR/f"{job_id}.pdf"; pdf_path.write_bytes(data)
    with JOBS_LOCK: JOBS[job_id]={"id":job_id,"status":"queued","progress":0,"message":"Queued","filename":file.filename,"rows":[]}
    threading.Thread(target=extract_pdf_job,args=(job_id,pdf_path),daemon=True).start()
    return {"jobId":job_id,"status":"queued"}

@app.get("/pdf/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def pdf_job(job_id: str):
    with JOBS_LOCK: job=JOBS.get(job_id)
    if not job: raise HTTPException(404,"PDF job not found or server restarted")
    return {k:v for k,v in job.items() if k!="xlsx"}

@app.get("/pdf/jobs/{job_id}/xlsx", dependencies=[Depends(require_api_key)])
def pdf_job_xlsx(job_id: str):
    with JOBS_LOCK: job=JOBS.get(job_id)
    if not job or job.get("status")!="done": raise HTTPException(404,"Excel file is not ready")
    return FileResponse(job["xlsx"],filename=f"voter-extraction-{job_id}.xlsx",media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.post("/pdf/jobs/{job_id}/commit", dependencies=[Depends(require_api_key)])
def commit_pdf_job(job_id: str, include_review: bool=False, include_deleted: bool=False, db: Session = Depends(db_session)):
    with JOBS_LOCK: job=JOBS.get(job_id)
    if not job or job.get("status")!="done": raise HTTPException(404,"PDF job not ready")
    inserted=updated=skipped=0
    for r in job.get("rows",[]):
        if r.get("recordStatus")=="Deleted" and not include_deleted:
            skipped+=1; continue
        if r.get("dataQuality")!="Verified" and not include_review:
            skipped+=1; continue
        try:
            payload=VoterIn(**{k:r.get(k) for k in VoterIn.model_fields.keys()})
            existing=db.scalar(select(Voter).where(Voter.epic_id==payload.epicId))
            if existing: apply_voter(existing,payload); updated+=1
            else: v=Voter(); apply_voter(v,payload); db.add(v); inserted+=1
        except Exception: skipped+=1
    db.commit(); return {"ok":True,"inserted":inserted,"updated":updated,"skipped":skipped}

@app.get("/voters/{voter_id}/slip", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def voter_slip(voter_id:int, db:Session=Depends(db_session)):
    v=db.get(Voter,voter_id)
    if not v: raise HTTPException(404,"Voter not found")
    # Neutral voter-information slip: no candidate/party content.
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Voter Information Slip</title><style>body{{font-family:Arial,sans-serif;padding:32px}}.slip{{max-width:620px;border:2px solid #6d28d9;border-radius:16px;padding:24px}}h2{{color:#6d28d9}}table{{width:100%;border-collapse:collapse}}td{{padding:8px;border-bottom:1px solid #eee}}.small{{color:#666;font-size:12px}}</style></head><body><div class="slip"><h2>Voter Information Slip</h2><table><tr><td>Name</td><td>{v.name}</td></tr><tr><td>EPIC ID</td><td>{v.epic_id}</td></tr><tr><td>Part / Booth</td><td>{v.part_no} / {v.booth_no}</td></tr><tr><td>Serial No.</td><td>{v.serial_no}</td></tr><tr><td>Address</td><td>{v.section_address or v.booth_address}</td></tr></table><p class="small">Administrative voter information only. Verify details against the official electoral roll.</p></div><script>window.print()</script></body></html>'''

