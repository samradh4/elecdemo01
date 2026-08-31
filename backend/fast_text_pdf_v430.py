from __future__ import annotations

"""Fast path for electoral-roll PDFs that already contain a usable text layer.

Selectable-text PDFs should not be rasterized and OCR'd page-by-page. This
module first tries PyMuPDF word extraction, reuses the existing fixed-card
geometry/parser, and falls back to the adaptive OCR parser when the text layer
is absent or incomplete. Scanned PDFs therefore keep the existing OCR safety
net, while digital PDFs can complete dramatically faster.
"""

import os
import re

import main as legacy

TEXT_RASTER_SCALE = max(0.8, min(1.5, float(os.environ.get("ROLL_TEXT_RASTER_SCALE", "1.0"))))
TEXT_MIN_WORDS = max(40, min(500, int(os.environ.get("ROLL_TEXT_MIN_WORDS", "90"))))
TEXT_MIN_EPICS = max(3, min(30, int(os.environ.get("ROLL_TEXT_MIN_EPICS", "8"))))
TEXT_MIN_ROWS = max(5, min(30, int(os.environ.get("ROLL_TEXT_MIN_ROWS", "15"))))
TEXT_MIN_CORE_RATIO = max(0.5, min(1.0, float(os.environ.get("ROLL_TEXT_MIN_CORE_RATIO", "0.78"))))

_OCR_EXTRACT_ROLL_PAGE = legacy._extract_roll_page
_ORIGINAL_SUMMARY = legacy._extract_summary_expectations


def _word_tokens(page):
    words = page.get_text("words", sort=True) or []
    tokens = []
    for word in words:
        if len(word) < 5:
            continue
        x0, y0, x1, y1, text = word[:5]
        text = str(text or "").strip()
        if not text:
            continue
        width = max(0.1, float(x1) - float(x0))
        height = max(0.1, float(y1) - float(y0))
        tokens.append({
            "text": text,
            "conf": 99.0,
            "left": float(x0),
            "top": float(y0),
            "width": width,
            "height": height,
            "cx": (float(x0) + float(x1)) / 2,
            "cy": (float(y0) + float(y1)) / 2,
            "x": float(x0),
            "y": float(y0),
        })
    return tokens


def _usable_text_layer(tokens) -> bool:
    if len(tokens) < TEXT_MIN_WORDS:
        return False
    epics = 0
    for t in tokens:
        if legacy._normalize_epic(t.get("text", "")):
            epics += 1
    return epics >= TEXT_MIN_EPICS


def _text_layer_roll_page(page, page_no: int):
    tokens = _word_tokens(page)
    if not _usable_text_layer(tokens):
        return None

    defaults = {
        "partNo": legacy._extract_part_no(tokens),
        "acNo": legacy._extract_ac_no(tokens),
        "section": legacy._extract_section(tokens),
        "listType": legacy._page_list_type(tokens),
        "pageNo": page_no,
    }

    # A low-resolution raster is retained only for deletion-stamp detection.
    # Field text itself comes from the PDF text layer, not Tesseract.
    pix = page.get_pixmap(matrix=legacy.fitz.Matrix(TEXT_RASTER_SCALE, TEXT_RASTER_SCALE), alpha=False)
    img = legacy.Image.open(legacy.io.BytesIO(pix.tobytes("png"))).convert("L")
    gray = legacy.np.array(img)

    slots = []
    for row in range(10):
        for col in range(3):
            rect = legacy._card_rect(page, row, col)
            x0 = max(0, int(rect.x0 * TEXT_RASTER_SCALE))
            y0 = max(0, int(rect.y0 * TEXT_RASTER_SCALE))
            x1 = min(gray.shape[1], int(rect.x1 * TEXT_RASTER_SCALE))
            y1 = min(gray.shape[0], int(rect.y1 * TEXT_RASTER_SCALE))
            crop = gray[y0:y1, x0:x1]
            slots.append(legacy._parse_roll_card(page, rect, tokens, tokens, defaults, crop))

    base = legacy._infer_serial_base(slots)
    rows = []
    for idx, row in enumerate(slots):
        if not row:
            continue
        if base is not None:
            expected = base + idx
            if row.get("serialNo") != str(expected):
                row["serialNo"] = str(expected)
                rr = [
                    x for x in row.get("reviewReason", "").split(", ")
                    if x and not x.startswith("serial OCR missing")
                ]
                row["reviewReason"] = ", ".join(rr)

        core_ok = bool(
            row.get("epicId")
            and row.get("name")
            and row.get("serialNo")
            and row.get("age")
            and row.get("gender") in {"Male", "Female", "Other"}
        )
        serious = [
            r for r in row.get("reviewReason", "").split(", ")
            if r and not r.startswith("house number")
        ]
        row["dataQuality"] = "Verified" if core_ok and not serious else "Review"
        row["extractMethod"] = "text-layer"
        row.pop("_serialConf", None)
        rows.append(row)

    core_count = sum(
        1 for r in rows
        if r.get("epicId")
        and r.get("name")
        and r.get("serialNo")
        and r.get("age")
        and r.get("gender") in {"Male", "Female", "Other"}
    )
    core_ratio = core_count / max(1, len(rows))
    if len(rows) < TEXT_MIN_ROWS or core_ratio < TEXT_MIN_CORE_RATIO:
        return None
    return rows, defaults


def _auto_extract_roll_page(page, page_no: int):
    try:
        fast = _text_layer_roll_page(page, page_no)
        if fast is not None:
            return fast
    except Exception:
        # Any unexpected text-layer issue must preserve the reliable OCR path.
        pass
    return _OCR_EXTRACT_ROLL_PAGE(page, page_no)


def _fast_summary(doc):
    try:
        page = doc[-1]
        sx = page.rect.width / legacy.ROLL_BASE_W
        sy = page.rect.height / legacy.ROLL_BASE_H
        clip = legacy.fitz.Rect(390 * sx, 285 * sy, 590 * sx, 306 * sy)
        raw = page.get_text("text", clip=clip) or ""
        nums = [int(x) for x in re.findall(r"\d+", raw)]
        if len(nums) >= 4:
            male, female, other, total = nums[-4:]
            return {"male": male, "female": female, "other": other, "total": total}
    except Exception:
        pass
    return _ORIGINAL_SUMMARY(doc)


legacy._extract_roll_page = _auto_extract_roll_page
legacy._extract_summary_expectations = _fast_summary
legacy.TEXT_LAYER_FASTPATH_ENABLED = True
legacy.TEXT_LAYER_FASTPATH_MIN_EPICS = TEXT_MIN_EPICS
legacy.TEXT_LAYER_FASTPATH_MIN_WORDS = TEXT_MIN_WORDS
