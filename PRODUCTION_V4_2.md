# Constituency Manager 4.2 production notes

## Bulk PDF OCR

The v4.2 admin portal accepts multiple electoral-roll PDFs in one upload and places them into a bounded server queue. This deliberately avoids the old behavior where every upload created an unlimited OCR thread.

Recommended Render environment variables for a production instance:

```text
PDF_WORKERS=2
PDF_MAX_BATCH=50
ROLL_OCR_SCALE=2.0
PDF_STORAGE_DIR=/var/data/pdf-v42
MAX_PDF_MB=80
V41_GPS_RETENTION_DAYS=90
```

Use `PDF_WORKERS=1` on a very small/512 MB service. Use 2 workers on a server with at least 2 vCPU and enough memory. Increase to 3–4 only after load testing representative voter-roll PDFs. More workers are not automatically faster because every worker launches CPU/memory-heavy Tesseract OCR.

`ROLL_OCR_SCALE=2.0` is the balanced-fast default. The parser still uses high-resolution targeted OCR for weak EPIC/gender/deletion fields. Before changing below 2.0, compare extraction totals, gender totals, EPIC quality and review-queue size against representative rolls.

## Persistent files

PDF job metadata is persisted in PostgreSQL. The uploaded PDFs and generated Excel/JSON result files are stored under `PDF_STORAGE_DIR`. For production, mount persistent storage at that path. Without persistent storage, a platform restart can remove source/result files even though the PostgreSQL job record remains.

## Bulk workflow

1. Admin opens `/v4/admin-ops` and signs in.
2. Open **PDF Converter**.
3. Select multiple PDFs and choose **Upload & queue PDFs**.
4. The portal shows queued/processing/done/error progress and refreshes automatically.
5. Download each Excel result when ready.
6. The API also supports batch ZIP download and direct import of completed extracted rows into the v4 constituency/booth database using the electoral-roll Part No as booth number.

## Capacity

The code can queue up to 50 PDFs per request by default and hundreds across repeated batches, but actual conversion throughput is determined by server CPU, RAM, PDF page count and scan quality. For sustained high-volume OCR, use an always-on paid instance with persistent storage; a free/sleeping service is appropriate only for demos and light testing.
