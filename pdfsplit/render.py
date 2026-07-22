"""Rendering with PyMuPDF (§7.1 header page).

This module owns everything drawn *onto* a page with PyMuPDF's built-in fonts —
no external font files, so the tool works offline with nothing to ship. Stage 03
adds the page-thumbnail renderer here too; the header page lives here (rather than
in ``write.py``) so both skins can reuse the module cleanly.

The header page exists because a chunk PDF is otherwise a context-free slab that
opens mid-sentence with no indication of which book it came from — it tells the
model what it is reading and where the material sits in the book's arc.
"""

from __future__ import annotations

import fitz

from .engine import _label_number_prefix

# PyMuPDF built-in Helvetica only (§7.1) — no font assets to ship.
_BOLD = "hebo"
_ROMAN = "helv"

_GREY_AUTHOR = (0.4, 0.4, 0.4)
_GREY_SOURCE = (0.5, 0.5, 0.5)
_BLACK = (0.0, 0.0, 0.0)

# PyMuPDF's built-in base-14 fonts draw with Latin-1 encoding, which lacks the
# en/em dash and curly quotes we happily put in labels, metadata and the index.
# Transliterate drawn header text so nothing silently vanishes on the page; the
# PDF metadata and 00-index.md keep the original Unicode.
_TRANSLIT = {
    "–": "-", "—": "-", "−": "-",       # en / em / minus
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ",
}


def _latin1(s: str) -> str:
    for bad, good in _TRANSLIT.items():
        s = s.replace(bad, good)
    return s.encode("latin-1", "ignore").decode("latin-1")


def position_line(chunk, total_emitted: int) -> str:
    """The "where in the book" line under the chunk label (§7.1).

    Normal chunk → ``"Chapter 3 of 14"`` (uses ``chunk.seq`` and the emitted total).
    Merged/subdivided chunks state their shape explicitly, matching the label.
    """
    if chunk.part_of is not None:
        a, b = chunk.part_of
        prefix = _label_number_prefix(chunk.subdivided_from or chunk.label)
        return f"{prefix}, part {a} of {b}"
    if chunk.merged_from is not None and "—" in chunk.label:
        head = chunk.label.split("—")[0].strip()      # "Chapters 5–7"
        if head:
            return f"{head} of {total_emitted}"
    return f"Chapter {chunk.seq} of {total_emitted}"


def _draw_header(page, chunk, plan, source_filename: str, total_emitted: int) -> None:
    rect = page.rect
    left = 72.0                          # 1" left margin
    right = rect.width - 72.0
    y = rect.height * 0.26              # upper third, generous top margin

    page.insert_text((left, y), _latin1(plan.book_title), fontname=_BOLD, fontsize=18)
    y += 24
    if plan.author:
        page.insert_text((left, y), _latin1(plan.author), fontname=_ROMAN,
                         fontsize=10, color=_GREY_AUTHOR)
        y += 18
    else:
        y += 6

    page.draw_line((left, y), (right, y), color=_BLACK, width=0.5)   # hairline rule
    y += 26

    page.insert_text((left, y), _latin1(chunk.label), fontname=_BOLD, fontsize=14)
    y += 22
    page.insert_text((left, y), _latin1(position_line(chunk, total_emitted)),
                     fontname=_ROMAN, fontsize=10)
    y += 16
    src = f"Source pages {chunk.start + 1}-{chunk.end + 1} · {source_filename}"
    page.insert_text((left, y), _latin1(src), fontname=_ROMAN, fontsize=9,
                     color=_GREY_SOURCE)


def prepend_header_page(new_doc, chunk, plan, source_filename: str,
                        total_emitted: int):
    """Insert a generated header page (§7.1) at index 0 of an in-progress chunk
    document, matching the dimensions of what is currently its first page."""
    rect = new_doc[0].rect
    page = new_doc.new_page(pno=0, width=rect.width, height=rect.height)
    _draw_header(page, chunk, plan, source_filename, total_emitted)
    return page
