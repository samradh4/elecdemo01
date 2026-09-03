import io
import os
import tempfile
import zipfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="cm44-test-"))
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "test.db")
os.environ["DATA_DIR"] = str(root / "data")
os.environ["PDF_STORAGE_DIR"] = str(root / "pdf")
os.environ["PDF_WORKERS"] = "2"
os.environ["ROLL_OCR_FAST_SCALE"] = "1.65"
os.environ["V4_AUTH_SECRET"] = "cm44-test-secret"

from fastapi.testclient import TestClient
from sqlalchemy import select

from main_v4_entry import app
import fast_ocr_v422
import main
import pdf_bulk_v42

# Queue behavior is tested without invoking Tesseract.
pdf_bulk_v42._submit = lambda job_id, source_path: None

c = TestClient(app)
admin = c.post("/v4/auth/register", json={
    "username": "admin44", "fullName": "Admin 44", "phone": "9999999999", "password": "Admin@123"
})
assert admin.status_code == 200, admin.text
j = admin.json()
h = {"Authorization": "Bearer " + j["token"]}

batch = "mobile-test-batch"
for name in ("constituency-101.pdf", "constituency-102.pdf"):
    r = c.post(
        "/v4/admin/pdf/bulk/single",
        headers=h,
        data={"batchId": batch},
        files={"file": (name, b"%PDF-1.4\n% test\n", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["batchId"] == batch

status = c.get(f"/v4/admin/pdf/batches/{batch}", headers=h)
assert status.status_code == 200, status.text
sj = status.json()
assert sj["counts"]["total"] == 2
assert sj["counts"]["queued"] == 2
assert len(sj["jobs"]) == 2

# Pretend conversion finished so the ZIP export path can be verified.
with pdf_bulk_v42.SessionLocal() as db:
    rows = db.scalars(select(pdf_bulk_v42.PdfJobRecord).where(pdf_bulk_v42.PdfJobRecord.batch_id == batch)).all()
    for idx, row in enumerate(rows, start=1):
        result = root / f"result-{idx}.xlsx"
        result.write_bytes(b"xlsx-demo")
        row.status = "done"
        row.progress = 100
        row.message = "Done"
        row.result_path = str(result)
    db.commit()

z = c.get(f"/v4/admin/pdf/batches/{batch}/xlsx.zip", headers=h)
assert z.status_code == 200, z.text
with zipfile.ZipFile(io.BytesIO(z.content)) as archive:
    names = archive.namelist()
    assert len(names) == 2
    assert all(name.endswith("-converted.xlsx") for name in names)

assert getattr(main, "FAST_OCR_ENABLED", False) is True
assert getattr(main, "FAST_OCR_DIRECT_GRAY", False) is True
assert fast_ocr_v422.FAST_SCALE == 1.65

print("v4.4 fast multi-PDF batch regression test passed")
