import os
from pathlib import Path
from datetime import datetime, timezone

Path("data").mkdir(exist_ok=True)
Path("data/test_v41.db").unlink(missing_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///./data/test_v41.db"
os.environ["V4_AUTH_SECRET"] = "v41-test-secret"

from fastapi.testclient import TestClient
from main_v4_entry import app

c = TestClient(app)

def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text)
    return r.json() if "json" in r.headers.get("content-type", "") else r

admin = ok(c.post("/v4/auth/register", json={"username":"admin41","fullName":"Admin 4.1","phone":"9999999999","password":"Admin@123"}))
assert admin["user"]["role"] == "admin"
ah = {"Authorization": "Bearer " + admin["token"]}
const = ok(c.post("/v4/admin/constituencies", headers=ah, json={"code":"AC-1","name":"Demo Assembly"}))
booth = ok(c.post("/v4/admin/booths", headers=ah, json={"constituencyId":const["id"],"boothNo":"101","name":"Demo Booth","address":"Demo School"}))
vol = ok(c.post("/v4/admin/users", headers=ah, json={"username":"vol41","fullName":"Volunteer 41","phone":"8888888888","password":"Welcome@123"}))
ok(c.post("/v4/admin/assignments/direct", headers=ah, json={"userId":vol["id"],"constituencyId":const["id"],"boothId":booth["id"]}))

csv_text = "constituencyCode,constituencyName,boothNo,boothName,serialNo,epicId,nameEnglish,nameHindi,relativeName,houseNo,age,gender,section\nAC-1,Demo Assembly,101,Demo Booth,1,ABC0000001,Aarav Sharma,आरव शर्मा,Ramesh Sharma,H-1,31,Male,Demo Section\n"
r = c.post("/v4/admin/import/voters-v41.csv", headers=ah, files={"file": ("voters.csv", csv_text.encode("utf-8"), "text/csv")})
imp = ok(r)
assert imp["inserted"] == 1 and imp["rejected"] == 0

login = ok(c.post("/v4/auth/login", json={"username":"vol41","password":"Welcome@123"}))
vh = {"Authorization": "Bearer " + login["token"]}
my = ok(c.get("/v4/my/voters", headers=vh))
assert len(my["items"]) == 1
voter = my["items"][0]
assert voter["name"] == "Aarav Sharma"
assert voter["localName"] == "आरव शर्मा"

mutation = {"mutationId":"mut-v41-0001","voterId":voter["id"],"status":"Verified","notes":"Demo verified","updatedAt":datetime.now(timezone.utc).isoformat()}
ok(c.post("/v4/sync", headers=vh, json={"mutations":[mutation]}))

now = datetime.now(timezone.utc).isoformat()
points = {"points":[
    {"pointId":"gps-v41-0001","boothId":booth["id"],"shiftId":"shift-demo","latitude":28.6500,"longitude":77.2300,"accuracy":12,"capturedAt":now},
    {"pointId":"gps-v41-0002","boothId":booth["id"],"shiftId":"shift-demo","latitude":28.6505,"longitude":77.2305,"accuracy":10,"capturedAt":now}
]}
track = ok(c.post("/v4/tracking/points", headers=vh, json=points))
assert len(track["accepted"]) == 2

ref = ok(c.post("/v4/referrals", headers=vh, json={"fullName":"Demo Referral","phone":"7777777777","note":"Interested in field work"}))
assert ref["status"] == "pending"
perf = ok(c.get("/v4/my/performance", headers=vh))
assert perf["housesVisited"] == 1
assert perf["recordsUpdated"] == 1
assert perf["volunteersReferred"] == 1
assert perf["gpsPoints90d"] == 2

refs = ok(c.get("/v4/admin/referrals", headers=ah))
assert len(refs) == 1
ok(c.post(f"/v4/admin/referrals/{refs[0]['id']}/review", headers=ah, json={"status":"approved"}))
ok(c.post(f"/v4/admin/designation/{vol['id']}", headers=ah, json={"designation":"Senior Volunteer"}))
admin_perf = ok(c.get("/v4/admin/performance", headers=ah))
row = next(x for x in admin_perf if x["user"]["id"] == vol["id"])
assert row["designation"] == "Senior Volunteer"
assert row["referralsApproved"] == 1

hist = ok(c.get(f"/v4/admin/tracking/history?userId={vol['id']}", headers=ah))
assert len(hist["points"]) == 2
latest = ok(c.get("/v4/admin/tracking/latest", headers=ah))
assert any(x["user"]["id"] == vol["id"] for x in latest)
assert c.get("/v4/admin/export/field-ops.csv", headers=ah).status_code == 200
assert c.get("/v4/admin/export/tracking.csv", headers=ah).status_code == 200
print("v4.1 smoke test passed")
