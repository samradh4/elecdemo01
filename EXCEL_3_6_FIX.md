# Excel 3.6 Fix

- Replaced openpyxl table-based PDF export with XlsxWriter.
- Removed Excel table XML from PDF exports to eliminate repair prompts.
- `Voters` is now a clean client-facing sheet without OCR audit columns.
- Added `Audit Review` for Source Page, Confidence, Data Quality and Review Reason.
- Kept `Verified Active`, `Review Queue`, `Deleted` and `Summary`.
- Added XML-invalid control-character cleaning while preserving Hindi text.
- Workbook opens on `Voters`.
