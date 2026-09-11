"""Render docs/architecture.png.

Kept as a script rather than a hand-drawn image so the diagram can be
regenerated when the pipeline changes, and so the layout lives in version
control alongside the code it describes.

    python scripts/generate_architecture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "architecture.png"

W, H = 1500, 1180
SCALE = 2  # supersample, then downsample, for readable text without a font hint

INK = (27, 32, 41)
MUTED = (98, 107, 122)
LINE = (186, 194, 206)
ACCENT = (31, 79, 216)
ACCENT_SOFT = (232, 238, 252)
GREEN = (23, 105, 58)
GREEN_SOFT = (231, 246, 236)
AMBER = (138, 90, 0)
AMBER_SOFT = (255, 246, 226)
SURFACE = (255, 255, 255)
CANVAS = (245, 246, 248)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
MONO_CANDIDATES = [
    "C:/Windows/Fonts/consola.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def load(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size * SCALE)
            except OSError:
                continue
    return ImageFont.load_default(size * SCALE)


def main() -> int:
    image = Image.new("RGB", (W * SCALE, H * SCALE), CANVAS)
    d = ImageDraw.Draw(image)

    f_title = load(BOLD_CANDIDATES, 30)
    f_sub = load(FONT_CANDIDATES, 15)
    f_head = load(BOLD_CANDIDATES, 17)
    f_body = load(FONT_CANDIDATES, 13)
    f_small = load(FONT_CANDIDATES, 12)
    f_mono = load(MONO_CANDIDATES, 12)
    f_step = load(BOLD_CANDIDATES, 13)

    def s(v: float) -> int:
        return int(v * SCALE)

    def box(x, y, w, h, *, fill=SURFACE, outline=LINE, width=1.5, radius=10):
        d.rounded_rectangle(
            [s(x), s(y), s(x + w), s(y + h)],
            radius=s(radius), fill=fill, outline=outline, width=s(width),
        )

    def text(x, y, value, font=f_body, fill=INK, anchor="la"):
        d.text((s(x), s(y)), value, font=font, fill=fill, anchor=anchor)

    def arrow(x, y1, y2, *, label=None, colour=LINE):
        d.line([(s(x), s(y1)), (s(x), s(y2 - 9))], fill=colour, width=s(2))
        d.polygon(
            [(s(x - 6), s(y2 - 10)), (s(x + 6), s(y2 - 10)), (s(x), s(y2))],
            fill=colour,
        )
        if label:
            text(x + 14, (y1 + y2) / 2 - 8, label, f_small, MUTED)

    # -- Title ------------------------------------------------------------
    text(50, 40, "Document Intelligence Platform", f_title)
    text(
        50, 82,
        "Extraction, validation and API service for invoices and financial "
        "statements",
        f_sub, MUTED,
    )
    d.line([(s(50), s(112)), (s(W - 50), s(112))], fill=LINE, width=s(1.5))

    # -- Clients ----------------------------------------------------------
    box(50, 140, 1400, 92, fill=SURFACE)
    text(70, 156, "CLIENTS", f_small, MUTED)
    box(70, 176, 430, 40, fill=ACCENT_SOFT, outline=ACCENT, width=1)
    text(90, 188, "Dashboard  ·  Jinja2 + hand-written CSS + vanilla JS", f_body, ACCENT)
    box(520, 176, 430, 40, fill=ACCENT_SOFT, outline=ACCENT, width=1)
    text(540, 188, "Swagger UI  ·  /docs   ·   ReDoc  ·  /redoc", f_body, ACCENT)
    box(970, 176, 460, 40, fill=ACCENT_SOFT, outline=ACCENT, width=1)
    text(990, 188, "Any REST client  ·  curl, Postman", f_body, ACCENT)

    arrow(750, 232, 268, label="multipart/form-data  ·  file + document_type")

    # -- API layer --------------------------------------------------------
    box(50, 268, 1400, 128)
    text(70, 284, "FASTAPI APPLICATION", f_small, MUTED)
    endpoints = [
        ("POST", "/api/v1/documents/process", "upload and process"),
        ("GET", "/api/v1/documents/{name}", "latest result by name"),
        ("GET", "/api/v1/documents", "dashboard list"),
        ("GET", "/api/v1/health", "health probe"),
    ]
    for index, (verb, path, note) in enumerate(endpoints):
        y = 308 + index * 21
        text(70, y, verb, f_step, ACCENT)
        text(122, y, path, f_mono)
        text(430, y, note, f_small, MUTED)

    box(700, 300, 730, 84, fill=(250, 251, 252))
    text(720, 312, "Cross-cutting middleware", f_step, INK)
    for index, item in enumerate(
        [
            "x-request-id correlation on every log record",
            "single JSON error envelope: {\"error\": {code, message, request_id}}",
            "no stack trace, provider message or secret ever reaches the client",
        ]
    ):
        text(720, 334 + index * 17, "\u2022  " + item, f_small, MUTED)

    arrow(750, 396, 432)

    # -- Orchestrator -----------------------------------------------------
    box(50, 432, 1400, 46, fill=(250, 251, 252))
    text(70, 447, "DocumentService", f_head)
    text(
        250, 450,
        "orchestrates the pipeline; owns ordering and failure handling, nothing else",
        f_small, MUTED,
    )

    arrow(750, 478, 516)

    # -- Pipeline ---------------------------------------------------------
    stages = [
        (
            "1  VALIDATE",
            "document_validation_service",
            [
                "type from magic bytes,",
                "not the extension",
                "page count \u2264 3, size, integrity",
                "typed error \u2192 400 / 413",
            ],
            None,
        ),
        (
            "2  READ",
            "ocr_service",
            [
                "native PDF text layer when",
                "the page has one",
                "otherwise rasterise at 200 DPI",
                "(pypdfium2, no system deps)",
            ],
            None,
        ),
        (
            "3  EXTRACT",
            "extraction_service",
            [
                "Claude vision, one streamed",
                "call for all pages",
                "structured output \u2192 Pydantic",
                "figures returned as printed",
            ],
            ACCENT,
        ),
        (
            "4  RECONCILE",
            "financial_validation_service",
            [
                "pure Python, no model",
                "per document type, per period",
                "missing operand \u2192",
                "NOT_APPLICABLE",
            ],
            GREEN,
        ),
        (
            "5  SCORE",
            "confidence_service",
            [
                "deterministic, explainable",
                "evidence corroboration +",
                "source path + check outcome",
                "never model self-reported",
            ],
            AMBER,
        ),
    ]

    x0, gap, bw, bh = 50, 20, 268, 168
    for index, (step, module, lines, tint) in enumerate(stages):
        x = x0 + index * (bw + gap)
        fill = {
            ACCENT: ACCENT_SOFT, GREEN: GREEN_SOFT, AMBER: AMBER_SOFT
        }.get(tint, SURFACE)
        box(x, 516, bw, bh, fill=fill, outline=tint or LINE, width=1.5)
        text(x + 18, 534, step, f_step, tint or ACCENT)
        text(x + 18, 556, module, f_mono, MUTED)
        for line_index, line in enumerate(lines):
            text(x + 18, 586 + line_index * 19, line, f_small, INK)
        if index < len(stages) - 1:
            cx = x + bw + gap / 2
            d.line(
                [(s(cx - 7), s(600)), (s(cx + 5), s(600))], fill=LINE, width=s(2)
            )
            d.polygon(
                [(s(cx + 4), s(594)), (s(cx + 4), s(606)), (s(cx + 12), s(600))],
                fill=LINE,
            )

    # -- External model call ---------------------------------------------
    d.line([(s(736), s(684)), (s(736), s(726))], fill=ACCENT, width=s(2))
    d.polygon(
        [(s(730), s(725)), (s(742), s(725)), (s(736), s(734))], fill=ACCENT
    )
    box(556, 734, 360, 52, fill=ACCENT_SOFT, outline=ACCENT, width=1.5)
    text(576, 746, "Anthropic Messages API", f_step, ACCENT)
    text(576, 766, "the only outbound network call in the pipeline", f_small, MUTED)

    # -- Persistence ------------------------------------------------------
    arrow(750, 786, 826)
    box(50, 826, 1400, 108)
    text(70, 842, "PERSISTENCE", f_small, MUTED)
    box(70, 858, 400, 54, fill=(250, 251, 252))
    text(90, 870, "DocumentRepository", f_step)
    text(90, 890, "all SQLAlchemy lives here", f_small, MUTED)
    d.line([(s(490), s(885)), (s(552), s(885))], fill=LINE, width=s(2))
    d.polygon(
        [(s(551), s(879)), (s(551), s(891)), (s(560), s(885))], fill=LINE
    )
    box(570, 858, 380, 54, fill=(250, 251, 252))
    text(590, 870, "SQLite (local)   ·   PostgreSQL (deployed)", f_body)
    text(590, 890, "one ORM layer; DATABASE_URL selects the backend", f_small, MUTED)
    box(980, 858, 450, 54, fill=AMBER_SOFT, outline=(226, 200, 150), width=1)
    text(1000, 870, "Free-tier disks are ephemeral, so a deployed", f_small, AMBER)
    text(1000, 890, "SQLite file would empty itself on redeploy.", f_small, AMBER)

    # -- Response ---------------------------------------------------------
    arrow(750, 934, 962)
    box(50, 962, 1400, 150, fill=SURFACE)
    text(70, 978, "RESPONSE  ·  stored verbatim and replayed by GET", f_small, MUTED)
    payload = [
        "document_name, document_type, processing_status  \u2014  PASS / FAILED",
        "file_validation  \u2014  file_type, is_supported, is_readable, page_count, status",
        "extracted_data   \u2014  every field with value, as-printed form, page number, "
        "evidence quote; line items; per-period key figures",
        "validation       \u2014  per check: formula, operands, calculated, reported, "
        "variance, PASS / FAIL / NOT_APPLICABLE",
        "processing_metadata  \u2014  ocr_used, extraction method, model, pages, "
        "processed_at, processing_time_ms",
        "overall_confidence  \u2014  optional, computed deterministically after extraction",
    ]
    for index, line in enumerate(payload):
        text(70, 1002 + index * 18, line, f_small, INK)

    image = image.resize((W, H), Image.LANCZOS)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, "PNG", optimize=True)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
