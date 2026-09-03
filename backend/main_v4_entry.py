from main import app
from v4 import router as v4_router
from v430_features import router as v430_features_router
from v423_import_diagnostics import router as v423_import_diagnostics_router
from v421_demo_fix import router as v421_demo_fix_router
from v41 import router as v41_router
from pdf_bulk_v42 import router as pdf_v42_router
from pdf_batch_v440 import router as pdf_batch_v440_router
# Patch the fixed-roll parser after the bulk module initializes legacy settings.
import fast_ocr_v422  # noqa: F401
# Digital/selectable-text PDFs bypass full-page OCR when their text layer is reliable.
import fast_text_pdf_v430  # noqa: F401
from pdf_import_v42 import router as pdf_import_v42_router

app.include_router(v4_router)
# v4.3 is registered first so its admin portal/access queue wins the duplicate portal route.
app.include_router(v430_features_router)
# v4.2.3 keeps PDF import diagnostics and quality warnings.
app.include_router(v423_import_diagnostics_router)
app.include_router(v421_demo_fix_router)
app.include_router(v41_router)
app.include_router(pdf_v42_router)
app.include_router(pdf_batch_v440_router)
app.include_router(pdf_import_v42_router)
