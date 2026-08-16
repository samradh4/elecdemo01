# Constituency Manager 3.3 — Validation & Error Handling

Fixes the `[object Object]` save error.

- API validation errors are converted into readable field-by-field messages.
- Manual voter entry validates Name, EPIC ID, Serial No., Age and long constituency fields before sending to the backend.
- Age values like `39-` are rejected in the form instead of becoming `null`.
- Serial No. accepts digits only.
- EPIC ID is normalized and validated as a 10-character alphanumeric ID.
- Gender inputs such as `male`, `M`, `female`, and `F` are normalized.
- Backend repeats the same validation so invalid records cannot bypass the UI.

The screenshot that showed `[object Object],[object Object]` was the browser stringifying FastAPI/Pydantic validation objects. This version formats them properly.
