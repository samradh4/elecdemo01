from __future__ import annotations

"""Fast OCR patch for the fixed-layout Hindi electoral-roll converter.

The legacy extractor does two full-page Tesseract passes (Hindi and English) on
 every voter page. For bulk processing we first run one combined hin+eng pass
 at a smaller render scale, then keep the existing targeted field fallbacks.
 Suspicious pages automatically fall back to the reliable two-pass parser.

V4.4 also renders directly to grayscale PyMuPDF pixels instead of creating a
PNG and decoding it again. That removes avoidable compression/decompression
work and cuts the page image memory footprint substantially during large batches.
"""

import os

import main as legacy

FAST_SCALE = max(1.4, min(2.2, float(os.environ.get("ROLL_OCR_FAST_SCALE", "1.65"))))
FAST_LANG = os.environ.get("ROLL_OCR_FAST_LANG", "hin+eng").strip() or "hin+eng"
MIN_PAGE_ROWS = max(1, min(30, int(os.environ.get("ROLL_OCR_FAST_MIN_ROWS", "15"))))
MIN_CORE_RATIO = max(0.50, min(1.0, float(os.environ.get("ROLL_OCR_FAST_MIN_CORE_RATIO", "0.80"))))

# Capture the reliable implementation before replacing it.
_ORIGINAL_EXTRACT_ROLL_PAGE = legacy._extract_roll_page


def _gray_page_image(page, scale: float):
    pix = page.get_pixmap(
        matrix=legacy.fitz.Matrix(scale, scale),
        colorspace=legacy.fitz.csGRAY,
        alpha=False,
    )
    return legacy.Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _fast_extract_roll_page(page, page_no: int):
    scale = FAST_SCALE
    img = _gray_page_image(page, scale)

    # One mixed-language page pass instead of two complete page passes.
    mixed_data = legacy.pytesseract.image_to_data(
        img,
        lang=FAST_LANG,
        config="--psm 11",
        output_type=legacy.pytesseract.Output.DICT,
    )
    mixed_tokens = legacy._ocr_tokens(mixed_data, scale)

    # The existing parser expects Hindi and English token collections. A mixed
    # pass contains both scripts plus Arabic digits, so the same token geometry
    # can feed both paths. Field-specific fallback OCR remains unchanged.
    hin_tokens = mixed_tokens
    eng_tokens = mixed_tokens
    defaults = {
        "partNo": legacy._extract_part_no(hin_tokens) or legacy._extract_part_no(eng_tokens),
        "acNo": legacy._extract_ac_no(eng_tokens),
        "section": legacy._extract_section(hin_tokens),
        "listType": legacy._page_list_type(hin_tokens),
        "pageNo": page_no,
    }

    gray = legacy.np.asarray(img)
    slots = []
    for row in range(10):
        for col in range(3):
            rect = legacy._card_rect(page, row, col)
            x0 = max(0, int(rect.x0 * scale))
            y0 = max(0, int(rect.y0 * scale))
            x1 = min(gray.shape[1], int(rect.x1 * scale))
            y1 = min(gray.shape[0], int(rect.y1 * scale))
            crop = gray[y0:y1, x0:x1]
            slots.append(
                legacy._parse_roll_card(
                    page, rect, hin_tokens, eng_tokens, defaults, crop
                )
            )

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
                    x
                    for x in row.get("reviewReason", "").split(", ")
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
            r
            for r in row.get("reviewReason", "").split(", ")
            if r and not r.startswith("house number")
        ]
        row["dataQuality"] = "Verified" if core_ok and not serious else "Review"
        row.pop("_serialConf", None)
        rows.append(row)

    # Adaptive safety net: a normal full voter page should yield many cards and
    # most should have core fields. Weak pages automatically use the original
    # two-pass parser rather than silently accepting a faster but poorer result.
    core_count = sum(
        1
        for r in rows
        if r.get("epicId")
        and r.get("name")
        and r.get("serialNo")
        and r.get("age")
        and r.get("gender") in {"Male", "Female", "Other"}
    )
    core_ratio = core_count / max(1, len(rows))
    if len(rows) < MIN_PAGE_ROWS or core_ratio < MIN_CORE_RATIO:
        return _ORIGINAL_EXTRACT_ROLL_PAGE(page, page_no)

    return rows, defaults


legacy._extract_roll_page = _fast_extract_roll_page
# The legacy fallback reads OCR_SCALE dynamically, so the configured fast scale
# remains the single source of truth for the adaptive path as well.
legacy.OCR_SCALE = FAST_SCALE
legacy.FAST_OCR_ENABLED = True
legacy.FAST_OCR_LANG = FAST_LANG
legacy.FAST_OCR_DIRECT_GRAY = True
