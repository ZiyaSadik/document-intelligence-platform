"""Text extraction and page rasterisation.

Two paths, chosen per page:

* **Native text layer** - pdfplumber returns real characters, so the text goes
  to the model as text. Cheaper, exact, no OCR error.
* **Rasterisation** - the page yields little or no text, so it is rendered to
  an image and read by the vision model.

The second path is not a rare fallback for this corpus. Many published
financial statements are exported with their text painted as vector outlines:
no font resource, no embedded image, just a content stream of path fills. Both
pdfplumber and pypdf return zero characters for those pages even though they
look perfectly legible. Rasterising is the only way to read them, which is why
pypdfium2 - a self-contained wheel with no system dependencies, so it installs
identically on a laptop and in a slim container - is a hard requirement here
rather than a convenience.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.core.config import settings
from app.core.exceptions import OcrError
from app.core.logging import get_logger
from app.utils.files import IMAGE_MIME_TYPES, PDF_MIME

logger = get_logger(__name__)

JPEG_MEDIA_TYPE = "image/jpeg"


@dataclass(slots=True)
class PageContent:
    """One page, resolved to either text or an image."""

    page_number: int
    text: str | None = None
    image_b64: str | None = None
    image_media_type: str = JPEG_MEDIA_TYPE
    used_ocr: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.text) or bool(self.image_b64)


@dataclass(slots=True)
class DocumentContent:
    pages: list[PageContent] = field(default_factory=list)

    @property
    def ocr_used(self) -> bool:
        return any(page.used_ocr for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def extraction_method(self) -> str:
        if not self.pages:
            return "none"
        if all(page.used_ocr for page in self.pages):
            return "vision_llm"
        if any(page.used_ocr for page in self.pages):
            return "hybrid_text_and_vision"
        return "native_text_layer"


class OcrService:
    """Turns raw upload bytes into per-page model input."""

    def __init__(
        self,
        *,
        text_layer_min_chars: int | None = None,
        raster_dpi: int | None = None,
        max_edge_px: int | None = None,
        jpeg_quality: int | None = None,
    ) -> None:
        self._min_chars = (
            text_layer_min_chars
            if text_layer_min_chars is not None
            else settings.text_layer_min_chars
        )
        self._dpi = raster_dpi or settings.raster_dpi
        self._max_edge = max_edge_px or settings.max_image_edge_px
        self._quality = jpeg_quality or settings.jpeg_quality

    def extract(
        self, payload: bytes, *, mime_type: str, filename: str
    ) -> DocumentContent:
        if mime_type == PDF_MIME:
            content = self._from_pdf(payload, filename=filename)
        elif mime_type in IMAGE_MIME_TYPES:
            content = self._from_image(payload, filename=filename)
        else:  # pragma: no cover - guarded by validation
            raise OcrError(log_detail=f"unsupported mime type {mime_type}")

        if not any(page.has_content for page in content.pages):
            raise OcrError(
                "No readable content could be obtained from the document.",
                context={"file_name": filename},
            )

        logger.info(
            "content prepared for extraction",
            extra={
                "file_name": filename,
                "pages": content.page_count,
                "method": content.extraction_method,
                "ocr_used": content.ocr_used,
            },
        )
        return content

    # -- PDF ---------------------------------------------------------------
    def _from_pdf(self, payload: bytes, *, filename: str) -> DocumentContent:
        text_by_page = self._pdf_text_layer(payload, filename=filename)

        pages: list[PageContent] = []
        pages_to_raster: list[int] = []
        for index, text in enumerate(text_by_page):
            if text and len(text.strip()) >= self._min_chars:
                pages.append(
                    PageContent(page_number=index + 1, text=text, used_ocr=False)
                )
            else:
                pages.append(PageContent(page_number=index + 1, used_ocr=True))
                pages_to_raster.append(index)

        if pages_to_raster:
            logger.info(
                "rasterising pages with no usable text layer",
                extra={
                    "file_name": filename,
                    "pages": [i + 1 for i in pages_to_raster],
                    "dpi": self._dpi,
                },
            )
            rendered = self._rasterise(payload, pages_to_raster, filename=filename)
            for index, image_b64 in rendered.items():
                pages[index].image_b64 = image_b64

        return DocumentContent(pages=pages)

    def _pdf_text_layer(self, payload: bytes, *, filename: str) -> list[str | None]:
        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                return [page.extract_text() for page in pdf.pages]
        except Exception as exc:
            # A text-layer read failure is recoverable: rasterisation still
            # works, so log it and fall through with no text.
            logger.warning(
                "text layer unreadable; falling back to rasterisation",
                extra={"file_name": filename, "error": f"{type(exc).__name__}: {exc}"},
            )
            try:
                pdf = pdfium.PdfDocument(io.BytesIO(payload))
                try:
                    return [None] * len(pdf)
                finally:
                    pdf.close()
            except Exception as inner:  # pragma: no cover - validated earlier
                raise OcrError(
                    context={"file_name": filename},
                    log_detail=f"{type(inner).__name__}: {inner}",
                ) from inner

    def _rasterise(
        self, payload: bytes, page_indices: list[int], *, filename: str
    ) -> dict[int, str]:
        scale = self._dpi / 72.0
        rendered: dict[int, str] = {}
        try:
            pdf = pdfium.PdfDocument(io.BytesIO(payload))
        except Exception as exc:  # pragma: no cover - validated earlier
            raise OcrError(
                context={"file_name": filename},
                log_detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        try:
            for index in page_indices:
                page = pdf[index]
                try:
                    image = page.render(scale=scale).to_pil()
                finally:
                    page.close()
                rendered[index] = self._encode_jpeg(image)
        except Exception as exc:
            raise OcrError(
                "The document pages could not be rendered for reading.",
                context={"file_name": filename},
                log_detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        finally:
            pdf.close()
        return rendered

    # -- Images ------------------------------------------------------------
    def _from_image(self, payload: bytes, *, filename: str) -> DocumentContent:
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                # Phone photos carry an EXIF orientation flag; without applying
                # it a portrait receipt arrives sideways and reads far worse.
                image = ImageOps.exif_transpose(opened)
                encoded = self._encode_jpeg(image)
        except Exception as exc:
            raise OcrError(
                "The image could not be prepared for reading.",
                context={"file_name": filename},
                log_detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        return DocumentContent(
            pages=[PageContent(page_number=1, image_b64=encoded, used_ocr=True)]
        )

    # -- Shared ------------------------------------------------------------
    def _encode_jpeg(self, image: Image.Image) -> str:
        """Downscale, flatten to RGB and base64-encode as JPEG.

        The cap keeps request size sane: the sample corpus includes a 4.4 MB
        phone photo whose full resolution buys no extra legibility.
        """
        if image.mode in ("RGBA", "LA", "P"):
            converted = image.convert("RGBA")
            background = Image.new("RGB", converted.size, (255, 255, 255))
            background.paste(converted, mask=converted.split()[-1])
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

        longest = max(image.size)
        if longest > self._max_edge:
            ratio = self._max_edge / longest
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=self._quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")
