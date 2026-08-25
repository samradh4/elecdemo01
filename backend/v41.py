from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from v4 import (
    Base, engine, db_session, current_user, admin_user,
    User, Constituency, Booth, Voter, Assignment, SurveyLog,
    active_assignment, user_json, constituency_json, booth_json, assignment_json,
    SURVEY_STATUSES,
)

# Neutral field-work statuses only. These colors/statuses are intentionally not
# candidate-support or voting-intention labels.
SURVEY_STATUSES.update({
    "Verified", "Needs Follow-up", "Needs Admin Review",
    "Deceased/Missing", "Not Living Here",
})

GPS_RETENTION_DAYS = int(__import__("os").environ.get("V41_GPS_RETENTION_DAYS", "90"))
GPS_MAX_BATCH = 2000


class LocationPoint(Base):
    __tablename__ = "v41_location_points"
    __table_args__ = (UniqueConstraint("user_id", "point_id", name="uq_v41_location_user_point"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    point_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("v4_users.id", ondelete="CASCADE"), index=True)
    booth_id: Mapped[int] = mapped_column(ForeignKey("v4_booths.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[str] = mapped_column(String(100), default="", index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    accuracy: Mapped[float] = mapped_column(Float, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class VolunteerReferral(Base):
    __tablename__ = "v41_volunteer_referrals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("v4_users.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(140))
    phone: Mapped[str] = mapped_column(String(30), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("v4_users.id"), nullable=True)


class VolunteerProfile(Base):
    __tablename__ = "v41_volunteer_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("v4_users.id", ondelete="CASCADE"), primary_key=True)
    designation: Mapped[str] = mapped_column(String(80), default="Volunteer")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("v4_users.id"), nullable=True)


Base.metadata.create_all(engine)
router = APIRouter(prefix="/v4", tags=["v4.1-field-ops"])


class LocationPointIn(BaseModel):
    pointId: str = Field(min_length=6, max_length=100)
    boothId: int
    shiftId: str = Field(default="", max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float = Field(default=0, ge=0, le=50000)
    capturedAt: str


class LocationBatchIn(BaseModel):
    points: list[LocationPointIn] = []


class ReferralIn(BaseModel):
    fullName: str = Field(min_length=2, max_length=140)
    phone: str = Field(min_length=5, max_length=30)
    note: str = Field(default="", max_length=1000)


class ReferralReviewIn(BaseModel):
    status: str


class DesignationIn(BaseModel):
    designation: str = Field(min_length=2, max_length=80)


def _parse_dt(value: str) -> datetime:
    try:
        text = (value or "").strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(text)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _cleanup_gps(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=GPS_RETENTION_DAYS)
    db.execute(delete(LocationPoint).where(LocationPoint.received_at < cutoff))


def _profile(db: Session, user_id: int) -> VolunteerProfile:
    p = db.get(VolunteerProfile, user_id)
    if not p:
        p = VolunteerProfile(user_id=user_id, designation="Volunteer")
        db.add(p)
        db.flush()
    return p


def _distance_km(points: list[LocationPoint]) -> float:
    def hav(a: LocationPoint, b: LocationPoint) -> float:
        r = 6371.0
        p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
        dp = math.radians(b.latitude - a.latitude)
        dl = math.radians(b.longitude - a.longitude)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(min(1.0, math.sqrt(h)))
    total = 0.0
    for i in range(1, len(points)):
        # Ignore obviously impossible jumps so GPS noise does not inflate performance.
        step = hav(points[i - 1], points[i])
        if step <= 5:
            total += step
    return round(total, 2)


def performance_json(db: Session, u: User) -> dict:
    logs = db.scalars(select(SurveyLog).where(SurveyLog.user_id == u.id).order_by(SurveyLog.server_received_at)).all()
    voter_ids = {x.voter_id for x in logs}
    visited_logs = [x for x in logs if x.status != "Pending"]
    visited_voter_ids = {x.voter_id for x in visited_logs}
    houses = set()
    if visited_voter_ids:
        for v in db.scalars(select(Voter).where(Voter.id.in_(visited_voter_ids))).all():
            if (v.house_no or "").strip():
                houses.add(v.house_no.strip())
    refs = db.scalars(select(VolunteerReferral).where(VolunteerReferral.referrer_user_id == u.id)).all()
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=GPS_RETENTION_DAYS)
    gps = db.scalars(select(LocationPoint).where(LocationPoint.user_id == u.id, LocationPoint.received_at >= recent_cutoff).order_by(LocationPoint.captured_at)).all()
    activity_days = set()
    for x in logs:
        if x.server_received_at:
            activity_days.add(x.server_received_at.date())
    for x in gps:
        if x.captured_at:
            activity_days.add(x.captured_at.date())
    last_log = logs[-1].server_received_at if logs else None
    last_gps = gps[-1].captured_at if gps else None
    last = max([x for x in (last_log, last_gps) if x], default=None)
    a = active_assignment(db, u.id)
    p = _profile(db, u.id)
    return {
        "user": user_json(u),
        "designation": p.designation,
        "assignment": assignment_json(db, a) if a else None,
        "recordsUpdated": len(voter_ids),
        "totalUpdates": len(logs),
        "housesVisited": len(houses),
        "activeDays": len(activity_days),
        "volunteersReferred": len(refs),
        "referralsApproved": sum(1 for r in refs if r.status == "approved"),
        "gpsPoints90d": len(gps),
        "trackedDistanceKm90d": _distance_km(gps),
        "lastActivityAt": _iso(last),
    }


@router.get("/ops/health")
def ops_health(db: Session = Depends(db_session)):
    return {
        "ok": True,
        "version": "4.1.0",
        "gpsRetentionDays": GPS_RETENTION_DAYS,
        "locationPoints": db.scalar(select(func.count()).select_from(LocationPoint)) or 0,
    }


@router.post("/tracking/points")
def upload_tracking(payload: LocationBatchIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    if user.role != "volunteer":
        raise HTTPException(403, "Volunteer account required")
    a = active_assignment(db, user.id)
    if not a:
        raise HTTPException(403, "No active booth assignment")
    accepted, rejected = [], []
    _cleanup_gps(db)
    for p in payload.points[:GPS_MAX_BATCH]:
        if p.boothId != a.booth_id:
            rejected.append({"pointId": p.pointId, "error": "Point belongs to an old/revoked booth"})
            continue
        exists = db.scalar(select(LocationPoint).where(LocationPoint.user_id == user.id, LocationPoint.point_id == p.pointId))
        if exists:
            accepted.append(p.pointId)
            continue
        db.add(LocationPoint(
            point_id=p.pointId, user_id=user.id, booth_id=a.booth_id, shift_id=p.shiftId.strip(),
            latitude=p.latitude, longitude=p.longitude, accuracy=p.accuracy,
            captured_at=_parse_dt(p.capturedAt), received_at=datetime.now(timezone.utc),
        ))
        accepted.append(p.pointId)
    db.commit()
    return {"ok": True, "accepted": accepted, "rejected": rejected, "retentionDays": GPS_RETENTION_DAYS}


@router.get("/my/performance")
def my_performance(user: User = Depends(current_user), db: Session = Depends(db_session)):
    if user.role != "volunteer":
        raise HTTPException(403, "Volunteer account required")
    return performance_json(db, user)


@router.post("/referrals")
def add_referral(payload: ReferralIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    if user.role != "volunteer":
        raise HTTPException(403, "Volunteer account required")
    r = VolunteerReferral(referrer_user_id=user.id, full_name=payload.fullName.strip(), phone=payload.phone.strip(), note=payload.note.strip())
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "fullName": r.full_name, "phone": r.phone, "status": r.status, "createdAt": _iso(r.created_at)}


@router.get("/my/referrals")
def my_referrals(user: User = Depends(current_user), db: Session = Depends(db_session)):
    rows = db.scalars(select(VolunteerReferral).where(VolunteerReferral.referrer_user_id == user.id).order_by(VolunteerReferral.created_at.desc())).all()
    return [{"id": r.id, "fullName": r.full_name, "phone": r.phone, "note": r.note, "status": r.status, "createdAt": _iso(r.created_at)} for r in rows]


@router.get("/admin/performance")
def admin_performance(admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    users = db.scalars(select(User).where(User.role == "volunteer").order_by(User.full_name)).all()
    return [performance_json(db, u) for u in users]


@router.get("/admin/referrals")
def admin_referrals(status: str = Query(default=""), admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    stmt = select(VolunteerReferral)
    if status:
        stmt = stmt.where(VolunteerReferral.status == status)
    rows = db.scalars(stmt.order_by(VolunteerReferral.created_at.desc()).limit(1000)).all()
    out = []
    for r in rows:
        u = db.get(User, r.referrer_user_id)
        out.append({"id": r.id, "referrer": user_json(u) if u else None, "fullName": r.full_name, "phone": r.phone, "note": r.note, "status": r.status, "createdAt": _iso(r.created_at), "reviewedAt": _iso(r.reviewed_at)})
    return out


@router.post("/admin/referrals/{referral_id}/review")
def review_referral(referral_id: int, payload: ReferralReviewIn, admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    status = payload.status.strip().lower()
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(400, "Status must be pending, approved or rejected")
    r = db.get(VolunteerReferral, referral_id)
    if not r:
        raise HTTPException(404, "Referral not found")
    r.status = status; r.reviewed_at = datetime.now(timezone.utc); r.reviewed_by = admin.id
    db.commit()
    return {"ok": True, "status": r.status}


@router.post("/admin/designation/{user_id}")
def set_designation(user_id: int, payload: DesignationIn, admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    u = db.get(User, user_id)
    if not u or u.role != "volunteer":
        raise HTTPException(404, "Volunteer not found")
    p = _profile(db, user_id)
    p.designation = payload.designation.strip(); p.updated_at = datetime.now(timezone.utc); p.updated_by = admin.id
    db.commit()
    return {"ok": True, "designation": p.designation}


@router.get("/admin/tracking/latest")
def tracking_latest(admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    _cleanup_gps(db); db.commit()
    users = db.scalars(select(User).where(User.role == "volunteer", User.active == True).order_by(User.full_name)).all()
    now = datetime.now(timezone.utc)
    out = []
    for u in users:
        p = db.scalar(select(LocationPoint).where(LocationPoint.user_id == u.id).order_by(LocationPoint.captured_at.desc()).limit(1))
        if not p:
            continue
        age_min = max(0, int((now - (p.captured_at if p.captured_at.tzinfo else p.captured_at.replace(tzinfo=timezone.utc))).total_seconds() // 60))
        b = db.get(Booth, p.booth_id)
        out.append({"user": user_json(u), "booth": booth_json(b) if b else None, "latitude": p.latitude, "longitude": p.longitude, "accuracy": p.accuracy, "capturedAt": _iso(p.captured_at), "ageMinutes": age_min, "shiftId": p.shift_id})
    return out


@router.get("/admin/tracking/history")
def tracking_history(userId: int, day: str = Query(default=""), tzOffsetMinutes: int = Query(default=330, ge=-720, le=840), admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    u = db.get(User, userId)
    if not u or u.role != "volunteer":
        raise HTTPException(404, "Volunteer not found")
    now = datetime.now(timezone.utc)
    if day:
        try:
            local_midnight = datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(minutes=tzOffsetMinutes)))
            start = local_midnight.astimezone(timezone.utc)
            end = start + timedelta(days=1)
        except Exception:
            raise HTTPException(400, "day must be YYYY-MM-DD")
    else:
        start = now - timedelta(hours=24); end = now + timedelta(minutes=1)
    retention_start = now - timedelta(days=GPS_RETENTION_DAYS)
    if start < retention_start:
        start = retention_start
    rows = db.scalars(select(LocationPoint).where(LocationPoint.user_id == userId, LocationPoint.captured_at >= start, LocationPoint.captured_at < end).order_by(LocationPoint.captured_at).limit(10000)).all()
    return {"user": user_json(u), "retentionDays": GPS_RETENTION_DAYS, "distanceKm": _distance_km(rows), "points": [{"latitude": p.latitude, "longitude": p.longitude, "accuracy": p.accuracy, "capturedAt": _iso(p.captured_at), "shiftId": p.shift_id, "boothId": p.booth_id} for p in rows]}


@router.get("/admin/export/tracking.csv")
def export_tracking(userId: int = 0, admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=GPS_RETENTION_DAYS)
    stmt = select(LocationPoint).where(LocationPoint.received_at >= cutoff)
    if userId:
        stmt = stmt.where(LocationPoint.user_id == userId)
    rows = db.scalars(stmt.order_by(LocationPoint.user_id, LocationPoint.captured_at)).all()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Volunteer", "Username", "Booth", "Latitude", "Longitude", "Accuracy", "Captured At", "Shift ID"])
    for p in rows:
        u = db.get(User, p.user_id); b = db.get(Booth, p.booth_id)
        w.writerow([u.full_name if u else "", u.username if u else "", b.booth_no if b else "", p.latitude, p.longitude, p.accuracy, _iso(p.captured_at), p.shift_id])
    data = "\ufeff" + out.getvalue()
    return StreamingResponse(iter([data.encode("utf-8")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=gps-tracking-90-days.csv"})


@router.post("/admin/import/voters-v41.csv")
async def import_voters_v41(file: UploadFile = File(...), admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    raw = await file.read()
    if len(raw) > 100 * 1024 * 1024:
        raise HTTPException(413, "CSV is too large")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    inserted = updated = rejected = 0; errors = []
    for idx, row in enumerate(reader, start=2):
        try:
            ccode = (row.get("constituencyCode") or row.get("AC No") or row.get("acNo") or "").strip()
            cname = (row.get("constituencyName") or row.get("Constituency") or row.get("AC Name") or ccode or "Constituency").strip()
            booth_no = (row.get("boothNo") or row.get("Booth No") or row.get("partNo") or row.get("Part No") or "").strip()
            if not ccode or not booth_no:
                raise ValueError("constituencyCode and boothNo are required")
            c = db.scalar(select(Constituency).where(Constituency.code == ccode))
            if not c:
                c = Constituency(code=ccode, name=cname); db.add(c); db.flush()
            b = db.scalar(select(Booth).where(Booth.constituency_id == c.id, Booth.booth_no == booth_no))
            if not b:
                b = Booth(constituency_id=c.id, booth_no=booth_no, name=(row.get("boothName") or row.get("Booth Name") or "").strip(), address=(row.get("boothAddress") or row.get("Booth Address") or "").strip())
                db.add(b); db.flush()
            epic = (row.get("epicId") or row.get("EPIC ID") or row.get("EPIC") or "").strip().upper()
            name_en = (row.get("nameEnglish") or row.get("Name (English)") or row.get("English Name") or row.get("name") or row.get("Name") or "").strip()
            name_hi = (row.get("nameHindi") or row.get("Name (Hindi)") or row.get("Hindi Name") or row.get("localName") or row.get("Local/Hindi Name") or row.get("नाम") or "").strip()
            if not epic or not (name_en or name_hi):
                raise ValueError("EPIC ID and at least one voter name are required")
            v = db.scalar(select(Voter).where(Voter.booth_id == b.id, Voter.epic_id == epic))
            if not v:
                v = Voter(constituency_id=c.id, booth_id=b.id, epic_id=epic, name=name_en or "", local_name=name_hi or "")
                db.add(v); inserted += 1
            else:
                updated += 1
            v.constituency_id = c.id; v.booth_id = b.id; v.epic_id = epic
            v.serial_no = (row.get("serialNo") or row.get("Serial No") or row.get("क्रमांक") or "").strip()
            v.name = name_en or ""           # English column stays English-only.
            v.local_name = name_hi or ""     # Hindi column stays Hindi-only; no English fallback.
            v.relation_type = (row.get("relationType") or row.get("Relation") or "").strip()
            v.relative_name = (row.get("relativeName") or row.get("Relative Name") or "").strip()
            v.house_no = (row.get("houseNo") or row.get("House No") or "").strip()
            try:
                v.age = int((row.get("age") or row.get("Age") or "0").strip() or 0)
            except Exception:
                v.age = 0
            v.gender = (row.get("gender") or row.get("Gender") or "Other").strip() or "Other"
            v.section = (row.get("section") or row.get("Section") or row.get("sectionAddress") or "").strip()
            if (inserted + updated) % 500 == 0:
                db.commit()
        except Exception as e:
            rejected += 1
            if len(errors) < 40:
                errors.append({"row": idx, "error": str(e)})
    db.commit()
    return {"ok": True, "inserted": inserted, "updated": updated, "rejected": rejected, "errors": errors, "nameColumns": {"english": "nameEnglish", "hindi": "nameHindi"}}


@router.get("/admin/export/field-ops.csv")
def export_field_ops(admin: User = Depends(admin_user), db: Session = Depends(db_session)):
    rows = db.scalars(select(Voter).order_by(Voter.constituency_id, Voter.booth_id, Voter.serial_no, Voter.id)).all()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Constituency", "Booth No", "Serial No", "EPIC ID", "Name (English)", "Name (Hindi)", "Relative Name", "House No", "Age", "Gender", "Field Status", "Notes", "Updated At", "Updated By"])
    for v in rows:
        c = db.get(Constituency, v.constituency_id); b = db.get(Booth, v.booth_id); u = db.get(User, v.survey_updated_by) if v.survey_updated_by else None
        w.writerow([c.name if c else "", b.booth_no if b else "", v.serial_no, v.epic_id, v.name, v.local_name, v.relative_name, v.house_no, v.age, v.gender, v.survey_status, v.survey_notes, _iso(v.survey_updated_at), u.full_name if u else ""])
    data = "\ufeff" + out.getvalue()
    return StreamingResponse(iter([data.encode("utf-8")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=field-operations-export.csv"})


@router.get("/admin-ops", response_class=HTMLResponse, include_in_schema=False)
def admin_ops_portal():
    path = Path(__file__).with_name("admin_v41.html")
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "<h1>Admin field operations portal file missing</h1>"
