from main import app
from v4 import router as v4_router
from v423_import_diagnostics import router as v423_import_diagnostics_router
from v421_demo_fix import router as v421_demo_fix_router
from v41 import router as v41_router
from pdf_bulk_v42 import router as pdf_v42_router
# Patch the legacy roll page parser after pdf_bulk_v42 initializes it, before any jobs run.
import fast_ocr_v422  # noqa: F401
from pdf_import_v42 import router as pdf_import_v42_router

app.include_router(v4_router)
# v4.2.3 overrides the same admin portal/import route to add quality warnings and rejection diagnostics.
app.include_router(v423_import_diagnostics_router)
# Must be registered before v41 because both expose /v4/admin-ops.
app.include_router(v421_demo_fix_router)
app.include_router(v41_router)
app.include_router(pdf_v42_router)
app.include_router(pdf_import_v42_router)
