"""File-type sniffing and filename hygiene.

The declared ``Content-Type`` and the filename extension are both attacker- (or
just browser-) controlled, so the real type is determined from the leading
bytes of the payload.
"""

from __future__ import annotations

import os
import re
import unicodedata

PDF_MIME = "application/pdf"
JPEG_MIME = "image/jpeg"
PNG_MIME = "image/png"

SUPPORTED_MIME_TYPES = frozenset({PDF_MIME, JPEG_MIME, PNG_MIME})
IMAGE_MIME_TYPES = frozenset({JPEG_MIME, PNG_MIME})

_EXTENSION_BY_MIME = {PDF_MIME: ".pdf", JPEG_MIME: ".jpg", PNG_MIME: ".png"}

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\- ()&]+")


def sniff_mime_type(payload: bytes) -> str | None:
    """Identify a supported type from magic bytes, else ``None``.

    Only the three supported formats are recognised; anything else -- including
    a ``.pdf``-named text file -- returns ``None`` and is rejected upstream.
    """
    if len(payload) < 4:
        return None
    if payload[:5] == b"%PDF-" or payload[:4] == b"%PDF":
        return PDF_MIME
    if payload[:3] == b"\xff\xd8\xff":
        return JPEG_MIME
    if payload[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG_MIME
    return None


def extension_for(mime_type: str) -> str:
    return _EXTENSION_BY_MIME.get(mime_type, "")


def sanitise_filename(filename: str | None, *, fallback: str = "document") -> str:
    """Reduce an uploaded filename to a safe, storable basename.

    Strips any directory component (defeating ``../`` traversal), normalises
    Unicode, removes control and shell-significant characters and caps length.
    """
    if not filename:
        return fallback

    # Handle both POSIX and Windows separators regardless of host OS.
    name = filename.replace("\\", "/").split("/")[-1]
    name = os.path.basename(name).strip()
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")

    if not name:
        return fallback
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:190] + dot + ext) if dot else name[:200]
    return name


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
