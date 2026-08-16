# Constituency Manager 3.6

Cross-platform voter-list and constituency administration software built with Expo React Native + FastAPI.

## Runs on
- Android / iPhone through Expo Go or an EAS build
- macOS through the web build in Chrome/Safari
- Windows through the web build in Chrome/Edge
- Production backend on Docker/Render/VPS

## Included workflows
- Purple mobile dashboard similar to modern election-management apps
- Voter counts by gender, booth/part counts, database-quality dashboard
- Fast voter search + advanced search by name, relative, AC, part, serial, EPIC and mobile
- Voter detail screen with EPIC, house, age, gender, part, booth, section/booth address
- Family/household grouping using Family Key
- Team/field-staff management and assignment field
- Neutral data-quality status colors (`Verified` / `Review`)
- Booth map action using saved booth address
- Neutral printable Voter Information Slip (no party/candidate content)
- CSV import with validation and EPIC upsert
- CSV/XLSX database export
- PDF -> Excel extraction job with progress
- Coordinate-based voter-card extraction for text PDFs
- Hindi/English Tesseract OCR fallback for scanned PDFs
- Client-facing `Voters` sheet plus `Verified Active`, `Review Queue`, `Deleted`, `Audit Review`, and `Summary` sheets
- Clean-only PDF import by default so uncertain OCR rows do not silently enter the database
- SQLite by default; PostgreSQL supported with `DATABASE_URL`
- Optional backend API key using `APP_API_KEY`
- Health endpoint and structured backend errors

## Mac setup
```bash
cd Constituency-Manager-3.6
./scripts/setup-mac.sh
./scripts/run-mac.sh
```
The app opens in the browser. Backend is `http://127.0.0.1:8000`.

## Windows setup
1. Install Node.js LTS, Python 3, VS Code and Tesseract OCR.
2. Run:
```bat
scripts\setup-windows.bat
scripts\run-windows.bat
```
3. Allow Python/Node through Windows Firewall on Private networks when prompted.

## Android / iPhone testing
Run the backend on your Mac/Windows computer and keep phone + computer on the same Wi-Fi.

```bash
npx expo start -c
```
Scan the QR using Expo Go. In Settings enter the computer LAN address, e.g. `http://192.168.1.5:8000`.

## Android APK
```bash
npm install -g eas-cli
eas login
eas build -p android --profile preview
```

## Backend self-test
```bash
cd backend
python -m test_backend
```
or
```bash
python test_backend.py
```

## Production database
Set:
```text
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DATABASE
APP_API_KEY=a-long-random-secret
```
For a real multi-user deployment, use PostgreSQL and HTTPS rather than a local SQLite file.

## PDF extraction accuracy
No OCR engine can guarantee perfect extraction from every electoral-roll scan. This app is designed to fail safely: uncertain rows are marked `Review`, kept outside the clean sheet, and are not imported by the clean-import button. Before commercial deployment, test the backend against the exact electoral-roll formats the customer will use.

## Political-data boundary
The app is for neutral electoral-roll administration. It does not include voter political-preference/sentiment scoring, persuasion targeting, candidate preference labels, or party-specific voter profiling. Status colors are only for record quality/workflow.

## v3.1 startup reliability
Use the provided launcher instead of starting Expo alone. It starts the API, waits until `/health` is ready, then opens the web app. The frontend also retries during startup, so it does not show a premature `Failed to fetch` popup while the backend is still booting.

Mac:
```bash
./scripts/run-mac.sh
```

Windows:
```bat
scripts\run-windows.bat
```

## Version 3.5 electoral-roll parser

For the supplied Uttar Pradesh Hindi EROLLGEN/SIR 3-column roll format, see `ROLL_CONVERTER_3_5.md`. The converter now uses coordinate-based card OCR and summary cross-checking instead of whole-page text parsing.


## Excel 3.6 changes
- PDF exports use XlsxWriter with conservative OOXML and no Excel table objects, preventing the previous `[Repaired]` warning.
- `Voters` contains clean voter columns only.
- OCR traceability fields (`Source Page`, `Confidence`, `Data Quality`, `Review Reason`) live in `Audit Review` / review support sheets.
- Hindi text is preserved and invalid XML control characters are removed before writing.
