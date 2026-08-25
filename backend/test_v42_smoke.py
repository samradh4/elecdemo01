import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="cm42-test-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "test.db")
os.environ["DATA_DIR"] = str(root / "data")
os.environ["PDF_STORAGE_DIR"] = str(root / "pdf")
os.environ["PDF_WORKERS"] = "2"
os.environ["ROLL_OCR_SCALE"] = "2.0"
os.environ["ROLL_OCR_FAST_SCALE"] = "1.8"
os.environ["V4_AUTH_SECRET"] = "cm42-test-secret"

from fastapi.testclient import TestClient
from main_v4_entry import app
import main
import pdf_bulk_v42

# Queue intake is tested without running Tesseract. OCR itself is exercised in
# production; this smoke test verifies the bounded queue and fast-parser wiring.
pdf_bulk_v42._submit = lambda job_id, source_path: None

c = TestClient(app)
admin = c.post("/v4/auth/register", json={
    "username": "admin42", "fullName": "Admin 42", "phone": "9999999999", "password": "Admin@123"
})
assert admin.status_code == 200, admin.text
j = admin.json()
assert j["user"]["role"] == "admin"
h = {"Authorization": "Bearer " + j["token"]}

cfg = c.get("/v4/admin/pdf/config", headers=h)
assert cfg.status_code == 200, cfg.text
cfgj = cfg.json()
assert cfgj["workers"] == 2
assert cfgj["ocrScale"] == 1.8
assert getattr(main, "FAST_OCR_ENABLED", False) is True

files = [
    ("files", ("booth-101.pdf", b"%PDF-1.4\n% demo\n", "application/pdf")),
    ("files", ("booth-102.pdf", b"%PDF-1.4\n% demo\n", "application/pdf")),
]
r = c.post("/v4/admin/pdf/bulk", headers=h, files=files)
assert r.status_code == 200, r.text
out = r.json()
assert len(out["jobs"]) == 2
assert all(x["status"] == "queued" for x in out["jobs"])

jobs = c.get("/v4/admin/pdf/jobs", headers=h)
assert jobs.status_code == 200, jobs.text
assert len(jobs.json()) == 2

portal = c.get("/v4/admin-ops")
assert portal.status_code == 200
html = portal.text
assert "function doLogin()" in html
assert "login.style.display" not in html
assert "multiple" in html and "/admin/pdf/bulk" in html

print("v4.2 bulk queue/admin portal smoke test passed")
