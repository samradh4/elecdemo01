import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="cm43-test-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "test.db")
os.environ["DATA_DIR"] = str(root / "data")
os.environ["PDF_STORAGE_DIR"] = str(root / "pdf")
os.environ["PDF_WORKERS"] = "2"
os.environ["ROLL_OCR_SCALE"] = "2.0"
os.environ["ROLL_OCR_FAST_SCALE"] = "1.8"
os.environ["V4_AUTH_SECRET"] = "cm43-test-secret"

from fastapi.testclient import TestClient
from main_v4_entry import app
import main

c = TestClient(app)

admin = c.post("/v4/auth/register", json={
    "username": "admin43", "fullName": "Admin 43", "phone": "9999999999", "password": "Admin@123"
})
assert admin.status_code == 200, admin.text
aj = admin.json()
assert aj["user"]["role"] == "admin"
h = {"Authorization": "Bearer " + aj["token"]}

vol = c.post("/v4/auth/register", json={
    "username": "vol43", "fullName": "New Volunteer", "phone": "9000000043", "password": "Volunteer@123"
})
assert vol.status_code == 200, vol.text
assert vol.json()["user"]["role"] == "volunteer"

queue = c.get("/v4/admin/access-queue", headers=h)
assert queue.status_code == 200, queue.text
qj = queue.json()
assert len(qj) == 1
assert qj[0]["user"]["username"] == "vol43"
assert qj[0]["needsAccess"] is True
assert qj[0]["pendingAssignment"] is None

portal = c.get("/v4/admin-ops")
assert portal.status_code == 200
html = portal.text
assert "Constituency Manager 4.3" in html
assert "New registrations awaiting access" in html
assert "/admin/access-queue" in html
assert "Approve access / assign booth" in html
assert "accessToast" in html

assert getattr(main, "FAST_OCR_ENABLED", False) is True
assert getattr(main, "TEXT_LAYER_FASTPATH_ENABLED", False) is True

print("v4.3 access queue, portal popup and PDF fast-path smoke test passed")
