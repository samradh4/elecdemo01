# Electoral-roll converter 3.5

This build adds a template-specific high-accuracy converter for the Uttar Pradesh Hindi EROLLGEN/SIR voter-roll format with three voter-card columns per page.

## Why this is different

The previous converter OCR'd too much page text together. In this roll family every voter lives inside a fixed card. Version 3.5 OCRs Hindi and English separately, assigns words to a card by coordinates, and only then builds a row.

## Fields extracted

- Serial No.
- EPIC ID (standard and legacy slash format)
- Hindi voter name
- Father/Husband/Mother relation
- Relative name
- House No.
- Age
- Gender
- AC No.
- Part No.
- Section name
- Original-roll vs Addition-list status
- Active vs Deleted status
- Deletion code/reason when visible
- PDF source page
- Confidence / review reason

## Safety and validation

- Serial sequence is reconstructed from the card grid when the tiny serial box OCR is weak.
- Duplicate EPIC IDs are detected.
- Deleted-stamp cards are kept in Excel but are not auto-imported as active voters.
- The final PDF summary is read and cross-checked against extracted active totals and gender totals.
- Excel always opens on the full Voters sheet, so a strict review threshold cannot make the workbook look empty.

## Test against the supplied 32-page sample

The converter was exercised page-by-page against the supplied sample roll and produced:

- 839 voter cards extracted
- Serial sequence 1–839 with no gaps
- 776 original-roll cards
- 63 addition-list cards
- 4 deleted cards detected
- 835 active voters
- 465 male active voters
- 370 female active voters
- 0 other active voters
- Final active totals match the summary printed in the PDF
- 0 missing EPIC IDs after fallback OCR
- 0 missing voter names

The workbook contains `Voters`, `Verified Active`, `Review Queue`, `Deleted`, and `Summary` sheets.
