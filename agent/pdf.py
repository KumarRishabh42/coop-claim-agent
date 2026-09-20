"""PDF text/image extraction for uploaded files. See SPEC.md 8.3."""
from __future__ import annotations

import pymupdf


def pdf_to_text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def pdf_first_page_to_png(pdf_bytes: bytes, dpi: int = 200) -> bytes:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def ensure_image_bytes(filename: str, content: bytes) -> bytes:
    """Returns PNG/JPEG bytes suitable for a vision call. Renders page 1 if
    given a PDF; passes image bytes through unchanged otherwise."""
    if filename.lower().endswith(".pdf"):
        return pdf_first_page_to_png(content)
    return content


def ensure_text(filename: str, content: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        return pdf_to_text(content)
    return content.decode("utf-8", errors="replace")
