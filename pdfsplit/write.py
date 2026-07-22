"""Chunk emission (§7), ``--also-text`` (§7.2) and ``00-index.md`` (§7.3).

Everything here writes **only** under ``<out>/<book_slug>/``. The source document is
opened read-only and nothing is ever written to its directory (invariant §3.1). Like
``engine.py`` this module does no terminal I/O — it returns a :class:`WrittenResult`
the CLI and web skins report on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import fitz

from . import render
from .engine import read_outline
from .model import BookPlan, Chunk


@dataclass
class WrittenResult:
    out_dir: str
    written: list[str] = field(default_factory=list)   # emitted chunk PDFs
    texts: list[str] = field(default_factory=list)      # per-chunk .md (--also-text)
    index: str | None = None                            # 00-index.md path


def _emitted(plan: BookPlan) -> list[Chunk]:
    """Chunks that actually become files: kept, user-included, document order."""
    return [c for c in plan.chunks if c.skip_reason is None and c.include]


def _pad_width(n: int) -> int:
    """Zero-pad width for sequence numbers (§6): 2 digits normally, 3 over 99."""
    return max(2, len(str(n)))


def _filename(chunk: Chunk, plan: BookPlan, width: int, prefix_book: bool) -> str:
    stem = f"{chunk.seq:0{width}d}-{chunk.slug}"
    if prefix_book:
        stem = f"{plan.book_slug}__{stem}"
    return stem


def _sub_outline(chunk: Chunk, entries, header_offset: int) -> list[list]:
    """Flat outline entries whose page falls inside the chunk, re-based for
    ``set_toc`` (§7 step 4): pages offset for the prepended header and made 1-based,
    levels re-based relative to the shallowest in-range entry and normalized so the
    hierarchy never jumps by more than one (``set_toc`` rejects such a list)."""
    in_range = [e for e in entries if chunk.start <= e.page <= chunk.end]
    if not in_range:
        return []
    base = min(e.level for e in in_range)

    toc: list[list] = []
    prev = 0
    for e in in_range:
        level = max(1, e.level - base + 1)
        if level > prev + 1:               # no jumps, and the first row lands on 1
            level = prev + 1
        prev = level
        page1 = e.page - chunk.start + 1 + header_offset
        toc.append([level, e.title, page1])
    return toc


def write_book(plan: BookPlan, out_root: str, *, prefix_book: bool = False,
               also_text: bool = False, header_page: bool = True) -> WrittenResult:
    """Emit every included chunk of ``plan`` under ``<out_root>/<book_slug>/`` (§7)."""
    out_dir = os.path.join(out_root, plan.book_slug)
    os.makedirs(out_dir, exist_ok=True)

    src = fitz.open(plan.source)          # read-only; never saved, never mutated
    try:
        entries = read_outline(src, [])
        source_filename = os.path.basename(plan.source)
        chunks = _emitted(plan)
        width = _pad_width(len(chunks))
        header_offset = 1 if header_page else 0

        result = WrittenResult(out_dir=out_dir)
        for chunk in chunks:
            stem = _filename(chunk, plan, width, prefix_book)
            pdf_path = os.path.join(out_dir, f"{stem}.pdf")

            new = fitz.open()
            try:
                new.insert_pdf(src, from_page=chunk.start, to_page=chunk.end)
                if header_page:
                    render.prepend_header_page(
                        new, chunk, plan, source_filename, len(chunks)
                    )
                new.set_metadata({
                    "title": f"{plan.book_title} — {chunk.label}",
                    "author": plan.author or "",
                    "subject": f"pages {chunk.start + 1}–{chunk.end + 1} "
                               f"of {source_filename}",
                    "producer": "pdfsplit",
                })
                toc = _sub_outline(chunk, entries, header_offset)
                if toc:
                    new.set_toc(toc)
                new.save(pdf_path, garbage=4, deflate=True)
            finally:
                new.close()
            result.written.append(pdf_path)

            if also_text:
                md_path = os.path.join(out_dir, f"{stem}.md")
                _write_chunk_text(md_path, chunk, plan, src, source_filename)
                result.texts.append(md_path)

        result.index = _write_index(out_dir, plan, chunks, width, prefix_book)
        return result
    finally:
        src.close()


def _write_chunk_text(md_path: str, chunk: Chunk, plan: BookPlan, src,
                      source_filename: str) -> None:
    """§7.2 — one ``.md`` per chunk, label as its H1, page text joined with blanks."""
    pages = [src[p].get_text("text") for p in range(chunk.start, chunk.end + 1)]
    body = "\n\n".join(pages)
    header = (
        f"# {chunk.label}\n\n"
        f"> {plan.book_title} — source pages {chunk.start + 1}–{chunk.end + 1}\n\n"
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(header + body + "\n")


def _pages(chunk: Chunk) -> str:
    return f"{chunk.start + 1}–{chunk.end + 1}"


def _contents_label(chunk: Chunk) -> str:
    label = chunk.label
    if chunk.merged_from:
        return f"{label} *(merged)*"
    if chunk.subdivided_from:
        return f"{label} *(subdivided)*"
    return label


def _write_index(out_dir: str, plan: BookPlan, chunks: list[Chunk], width: int,
                 prefix_book: bool) -> str:
    """§7.3 — ``00-index.md``: the emitted-chunk table, a Skipped section naming
    every dropped title with its reason, and the plan's warnings."""
    band = plan.band
    if plan.notebooklm:
        mode = f"notebooklm mode ({band.floor}/{band.target}/{band.ceiling})"
    else:
        mode = "plain mode"

    counts = {"ok": 0, "snap_proposed": 0, "unverified": 0, "not_applicable": 0}
    for c in chunks:
        counts[c.verify.status] = counts.get(c.verify.status, 0) + 1
    summary_bits = []
    if counts["ok"]:
        summary_bits.append(f"{counts['ok']} verified")
    if counts["snap_proposed"]:
        summary_bits.append(f"{counts['snap_proposed']} proposed")
    if counts["unverified"]:
        summary_bits.append(f"{counts['unverified']} unverified")
    if counts["not_applicable"]:
        summary_bits.append(f"{counts['not_applicable']} without text layer")

    lines: list[str] = []
    lines.append(f"# {plan.book_title}")
    lines.append("")
    src_line = (f"Source: `{plan.source}` · {plan.total_pages} pages · "
                f"split at level {plan.level} · {mode}")
    lines.append(src_line)
    generated = f"Generated {len(chunks)} chunks"
    if summary_bits:
        generated += " · " + ", ".join(summary_bits)
    lines.append(generated)
    lines.append("")

    lines.append("| # | File | Contents | Source pages | Words |")
    lines.append("|---|------|----------|--------------|-------|")
    for c in chunks:
        stem = _filename(c, plan, width, prefix_book)
        lines.append(
            f"| {c.seq} | `{stem}.pdf` | {_contents_label(c)} "
            f"| {_pages(c)} | {c.words:,} |"
        )

    skipped = [c for c in plan.chunks if c.skip_reason is not None]
    excluded = [c for c in plan.chunks
                if c.skip_reason is None and not c.include]
    if skipped or excluded:
        lines.append("")
        lines.append("## Skipped")
        for c in skipped:
            lines.append(f"- **{c.label}** — pages {_pages(c)} — {c.skip_reason}")
        for c in excluded:
            lines.append(f"- **{c.label}** — pages {_pages(c)} — excluded by user")

    proposals = [c for c in chunks if c.verify.status == "snap_proposed"]
    if plan.warnings or proposals:
        lines.append("")
        lines.append("## Warnings")
        for c in proposals:
            lines.append(
                f"- Bookmark for \"{c.label}\" starts at p.{c.start + 1}; "
                f"boundary check proposes p.{c.verify.proposed_start + 1} "
                f"(emitted unchanged)."
            )
        for w in plan.warnings:
            lines.append(f"- {w}")

    lines.append("")
    index_path = os.path.join(out_dir, "00-index.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return index_path
