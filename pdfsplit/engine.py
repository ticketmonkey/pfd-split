"""Planning pipeline (§5).

``engine.py`` contains **no HTTP and no terminal I/O** — no ``print``, no ``rich``,
no ``click``. It takes a path plus options and returns a :class:`BookPlan`. The CLI
and web skins (stages 02–03) are thin wrappers over these functions, and the tests
drive the individual passes directly, so everything here stays pure and importable.

Everything uses PyMuPDF (``import fitz``); no other document library is imported.
"""

from __future__ import annotations

import os
import re
import unicodedata

import fitz

from .errors import LevelNotFoundError, NoOutlineError
from .model import Band, BookPlan, Chunk, OutlineEntry, VerifyResult
from .verify import verify_chunks

# --------------------------------------------------------------------------- #
# Naming (§6). slugify / override_key live here because §5.11 needs them and
# sidecar.py (§8) re-imports them — engine never imports sidecar, so there is
# no cycle.
# --------------------------------------------------------------------------- #

def slugify(s: str, maxlen: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^\s*(chapter|ch\.?|part|appendix|lesson|module)\s*[0-9ivxlcdm]*\s*[:.\-–—]?\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:maxlen].rstrip("-")
    return s or "untitled"


def override_key(entry: OutlineEntry) -> str:
    """Sidecar override key (§8), stable across ``--level`` changes and start
    corrections because it uses the entry's *original* 0-based outline page."""
    return f"{entry.level}:{entry.page}:{slugify(entry.title)}"


# --------------------------------------------------------------------------- #
# Skiplist (§5.5)
# --------------------------------------------------------------------------- #

KEEP_PATTERNS = [r"^glossary\b"]

SKIP_PATTERNS = [
    r"^(table of )?contents$", r"^toc$",
    r"^cover$", r"^title page$", r"^half[- ]title$",
    r"^copyright", r"^colophon$", r"^dedication$",
    r"^front\s*matter$", r"^back\s*matter$",
    r"^foreword$", r"^preface$", r"^introduction to the (second|third|\w+) edition$",
    r"^acknowledge?ments?$",
    r"^about the author", r"^about the technical", r"^about this book$",
    r"^appendix\b", r"^appendices$",
    r"^index$", r"^subject index$", r"^author index$",
    r"^bibliography$", r"^references$", r"^further reading$",
    r"^errata$", r"^notes$",
    r"^other books", r"^also (by|from)\b", r"^advertisement", r"^praise for",
    r"^list of (figures|tables)$",
]

_CHAPTER_RE = re.compile(r"^\s*(chapter|ch\.?|lesson|module)\s*\d+", re.I)
_NUMBERED_RE = re.compile(r"^\s*\d+\s*[.:\-–—]\s")


def _is_chapter(title_lc: str) -> bool:
    return bool(_CHAPTER_RE.match(title_lc) or _NUMBERED_RE.match(title_lc))


def classify_title(raw_title: str, extra_keep=(), extra_drop=()) -> tuple[str | None, bool]:
    """Return ``(skip_reason, keeplisted)`` for a title (§5.5), first match wins.

    ``skip_reason`` is ``None`` when the section is kept. ``keeplisted`` is True only
    when a *keep rule* (``--keep`` or ``KEEP_PATTERNS``) matched — not the chapter
    test — because the merge pass must never fold a keeplisted section (e.g. the
    Glossary) into a chapter.
    """
    t = raw_title.lower().strip()
    for pat in extra_keep:
        if re.search(pat, t):
            return None, True
    if _is_chapter(t):
        return None, False
    for pat in KEEP_PATTERNS:
        if re.search(pat, t):
            return None, True
    for pat in extra_drop:
        if re.search(pat, t):
            return f"user --drop: {pat}", False
    for pat in SKIP_PATTERNS:
        if re.search(pat, t):
            return f"skiplist: {pat}", False
    return None, False


# --------------------------------------------------------------------------- #
# §5.1 outline, §5.2 title, §5.4 end pages
# --------------------------------------------------------------------------- #

def read_outline(doc, warnings: list[str]) -> list[OutlineEntry]:
    """Flat, 0-based outline (§5.1). Raises :class:`NoOutlineError` on an empty
    outline. Unresolvable entries (raw ``page <= 0``) are dropped with a warning."""
    toc = doc.get_toc(simple=True)
    if not toc:
        raise NoOutlineError(f"{doc.name!r} has no bookmark outline")

    resolved: list[tuple[int, str, int]] = []
    for level, title, page1 in toc:
        if page1 <= 0:
            warnings.append(f"Dropped outline entry {title!r}: unresolvable destination")
            continue
        resolved.append((level, title, page1 - 1))

    if not resolved:
        raise NoOutlineError(f"{doc.name!r} outline has no resolvable entries")

    entries: list[OutlineEntry] = []
    last_at_level: dict[int, int] = {}
    for i, (level, title, page0) in enumerate(resolved):
        parent = last_at_level.get(level - 1)
        entries.append(OutlineEntry(idx=i, level=level, title=title, page=page0, parent=parent))
        last_at_level[level] = i
    return entries


def derive_title(doc, path: str) -> tuple[str, str | None]:
    """Book title and author (§5.2). Never invents edition/publisher."""
    meta = doc.metadata or {}
    raw = (meta.get("title") or "").strip()
    stem = os.path.splitext(os.path.basename(path))[0]
    filename_title = re.sub(r"[_\-]+", " ", stem).strip()

    if raw and len(raw) > 2 and raw.lower() not in (stem.lower(), filename_title.lower()):
        title = raw
    else:
        title = filename_title or stem or "Untitled"

    author = (meta.get("author") or "").strip() or None
    return title, author


def end_page(entry: OutlineEntry, entries: list[OutlineEntry], total_pages: int) -> int:
    """§5.4: end = (start of the next entry in the *full flat list* whose level
    <= this entry's level) − 1, else the last page. Using the full list is what
    drops skipped sections' pages and stops a chapter swallowing the next Part."""
    for nxt in entries[entry.idx + 1:]:
        if nxt.level <= entry.level:
            return nxt.page - 1
    return total_pages - 1


# --------------------------------------------------------------------------- #
# §5.8 oversize, §5.9 merge — each callable on a chunk list
# --------------------------------------------------------------------------- #

def _label_number_prefix(title: str) -> str:
    """"Chapter 7. Foo" -> "Chapter 7"; falls back to the whole title."""
    m = re.match(r"^\s*(chapter|ch\.?|part|lesson|module)\s*(\d+)", title.strip(), re.I)
    if m:
        word = m.group(1).rstrip(".").lower()
        word = "Chapter" if word in ("ch", "chapter") else word.capitalize()
        return f"{word} {m.group(2)}"
    return title.strip()


def _lead_number(title: str) -> str | None:
    m = re.search(r"\d+", title)
    return m.group(0) if m else None


def oversize_pass(chunks, entries, word_counts, total_pages, ceiling, size_of, warnings):
    """§5.8. Subdivide kept chunks over ``ceiling`` into their outline children,
    recursively; leave (and warn) if a chunk has no deeper outline. Applies in both
    modes. Skipped chunks pass through untouched."""
    out: list[Chunk] = []
    for chunk in chunks:
        if chunk.skip_reason is not None:
            out.append(chunk)
        else:
            out.extend(_subdivide(chunk, entries, word_counts, total_pages, ceiling, size_of, warnings))
    return out


def _subdivide(chunk, entries, word_counts, total_pages, ceiling, size_of, warnings):
    if size_of(chunk) <= ceiling:
        return [chunk]

    parent_idx = chunk.entries[0]
    parent = entries[parent_idx]
    children = [e for e in entries if e.parent == parent_idx]
    if not children:
        warnings.append(
            f"{parent.title!r} is {chunk.words} words with no deeper outline to split on"
        )
        return [chunk]

    total = len(children)
    prefix = _label_number_prefix(parent.title)
    pieces: list[Chunk] = []
    for i, child in enumerate(children):
        # first child chunk starts at the PARENT's start so no pages are lost
        # between the chapter opener and its first subsection.
        start = chunk.start if i == 0 else child.page
        end = min(end_page(child, entries, total_pages), chunk.end)
        if end < start:
            end = start
        pieces.append(Chunk(
            seq=0,
            label=f"{prefix} (part {i + 1} of {total}) — {child.title}",
            slug="",
            start=start,
            end=end,
            words=sum(word_counts[start:end + 1]),
            entries=[child.idx],
            parent=child.parent,
            verify=VerifyResult("unverified"),
            subdivided_from=parent.title,
            part_of=(i + 1, total),
        ))

    out: list[Chunk] = []
    for piece in pieces:
        out.extend(_subdivide(piece, entries, word_counts, total_pages, ceiling, size_of, warnings))
    return out


def merge_pass(chunks, entries, floor, ceiling, size_of, is_keeplisted):
    """§5.9, ``--notebooklm`` only. Greedy, minimal, document order. Merge **only to
    escape the floor**, never toward the target. A skipped chunk breaks a run so a
    merge can never absorb dropped pages (invariant 3); the ``parent`` equality test
    stops a merge crossing a Part boundary and handles flat books (``None == None``)."""
    out: list[Chunk] = []
    i, n = 0, len(chunks)
    while i < n:
        c = chunks[i]
        if c.skip_reason is not None:
            out.append(c)
            i += 1
            continue

        group = [c]
        total = size_of(c)
        j = i + 1
        while (total < floor and j < n
               and chunks[j].skip_reason is None
               and chunks[j].parent == c.parent
               and not is_keeplisted(chunks[j])
               and not is_keeplisted(c)
               and chunks[j].subdivided_from is None
               and c.subdivided_from is None
               and total + size_of(chunks[j]) <= ceiling):
            group.append(chunks[j])
            total += size_of(chunks[j])
            j += 1

        out.append(_emit_group(group, entries))
        i = j
    return out


def _emit_group(group, entries):
    if len(group) == 1:
        return group[0]

    first, last = group[0], group[-1]
    merged = Chunk(
        seq=0,
        label="",
        slug="",
        start=first.start,
        end=last.end,
        words=sum(g.words for g in group),
        entries=[e for g in group for e in g.entries],
        parent=first.parent,
        verify=VerifyResult("unverified"),
        merged_from=[g.label for g in group],
    )

    if first.parent is not None:
        parent_title = entries[first.parent].title
        a, b = _lead_number(first.label), _lead_number(last.label)
        if a and b:
            merged.label = f"Chapters {a}–{b} — {parent_title}"
        else:
            merged.label = parent_title
        merged.slug = slugify(parent_title)
    else:
        merged.label = f"{first.label} / {last.label}"
        merged.slug = slugify(first.label)[:28] + "-and-" + slugify(last.label)[:28]

    return merged


# --------------------------------------------------------------------------- #
# Entry point (§5.1–§5.11)
# --------------------------------------------------------------------------- #

def plan_book(path, *, level=1, notebooklm=False, band=Band(),
              extra_keep=(), extra_drop=(), overrides=None) -> BookPlan:
    path = os.path.abspath(path)
    doc = fitz.open(path)  # opened read-only; nothing is ever written to the source
    try:
        warnings: list[str] = []
        total_pages = doc.page_count

        # §5.1 outline
        entries = read_outline(doc, warnings)

        # §5.2 title
        book_title, author = derive_title(doc, path)

        # §5.6 word counts (extract text once; reused by verify)
        page_texts = [doc[i].get_text("text") for i in range(total_pages)]
        word_counts = [len(t.split()) for t in page_texts]
        total_words = sum(word_counts)
        words_per_page = (total_words / total_pages) if total_pages else 0.0

        # §5.7 no-text-layer fallback
        has_text_layer = words_per_page >= 20
        if has_text_layer:
            eff_band = band

            def size_of(c: Chunk) -> int:
                return c.words
        else:
            warnings.append(
                f"No usable text layer ({total_words} words over {total_pages} pages). "
                f"Banding by page count; boundary verification unavailable."
            )
            eff_band = Band(
                floor=round(band.floor / 400),
                target=round(band.target / 400),
                ceiling=round(band.ceiling / 400),
            )

            def size_of(c: Chunk) -> int:
                return c.end - c.start + 1

        # §5.3 select level
        level_counts: dict[int, int] = {}
        for e in entries:
            level_counts[e.level] = level_counts.get(e.level, 0) + 1
        selected = [e for e in entries if e.level == level]
        if not selected:
            raise LevelNotFoundError(level, level_counts)

        # §5.4 end pages + build chunks, §5.5 skiplist
        keeplisted_idx: set[int] = set()
        chunks: list[Chunk] = []
        for e in selected:
            start = e.page
            end = end_page(e, entries, total_pages)
            if end < start:
                warnings.append(f"{e.title!r} has end < start; clamped to a single page")
                end = start
            reason, keeplisted = classify_title(e.title, extra_keep, extra_drop)
            if keeplisted:
                keeplisted_idx.add(e.idx)
            chunks.append(Chunk(
                seq=0,
                label=e.title,
                slug="",
                start=start,
                end=end,
                words=sum(word_counts[start:end + 1]),
                entries=[e.idx],
                parent=e.parent,
                verify=VerifyResult("not_applicable" if not has_text_layer else "unverified"),
                skip_reason=reason,
            ))

        def is_keeplisted(c: Chunk) -> bool:
            return bool(c.entries) and c.entries[0] in keeplisted_idx

        # §5.8 oversize (both modes)
        chunks = oversize_pass(
            chunks, entries, word_counts, total_pages, eff_band.ceiling, size_of, warnings
        )

        # §5.9 merge (notebooklm only)
        if notebooklm:
            chunks = merge_pass(
                chunks, entries, eff_band.floor, eff_band.ceiling, size_of, is_keeplisted
            )

        # §5.10 verify (skipped when no text layer)
        if has_text_layer:
            verify_chunks(chunks, page_texts, entries, total_pages)

        # §5.11 overrides, then sequence + slugs
        _apply_overrides(chunks, entries, overrides, total_pages, word_counts)
        _assign_sequence(chunks)
        _assign_slugs(chunks, entries)

        return BookPlan(
            source=path,
            book_title=book_title,
            book_slug=slugify(book_title),
            author=author,
            total_pages=total_pages,
            level=level,
            notebooklm=notebooklm,
            band=band,
            has_text_layer=has_text_layer,
            words_per_page=words_per_page,
            chunks=chunks,
            warnings=warnings,
        )
    finally:
        doc.close()


def _apply_overrides(chunks, entries, overrides, total_pages, word_counts):
    """§5.11. ``starts`` replaces a chunk start (and re-derives the previous chunk's
    end, marking the corrected chunk ``ok``); ``include`` toggles emission."""
    if not overrides:
        return
    starts = overrides.get("starts", {}) or {}
    include = overrides.get("include", {}) or {}

    key_to_pos = {}
    for pos, c in enumerate(chunks):
        if c.entries:
            key_to_pos[override_key(entries[c.entries[0]])] = pos

    for key, new_start in starts.items():
        pos = key_to_pos.get(key)
        if pos is None:
            continue
        c = chunks[pos]
        c.start = new_start
        c.words = sum(word_counts[c.start:c.end + 1])
        c.verify.status = "ok"
        c.verify.proposed_start = None
        if pos > 0:
            prev = chunks[pos - 1]
            prev.end = max(prev.start, new_start - 1)
            prev.words = sum(word_counts[prev.start:prev.end + 1])

    for key, inc in include.items():
        pos = key_to_pos.get(key)
        if pos is not None:
            chunks[pos].include = bool(inc)


def _assign_sequence(chunks):
    """§5.11: ``seq`` 1..N over emitted chunks (kept, included, not skipped)."""
    seq = 0
    for c in chunks:
        if c.skip_reason is None and c.include:
            seq += 1
            c.seq = seq
        else:
            c.seq = 0


def _assign_slugs(chunks, entries):
    """§6: unique filename slugs within the book; collisions get -2, -3, …."""
    seen: dict[str, int] = {}
    for c in chunks:
        base = c.slug if c.merged_from else slugify(entries[c.entries[0]].title)
        n = seen.get(base, 0) + 1
        seen[base] = n
        c.slug = base if n == 1 else f"{base}-{n}"


# --------------------------------------------------------------------------- #
# inspect data (§9) — returns data, prints nothing
# --------------------------------------------------------------------------- #

def outline_summary(path, max_level=3):
    """Structured outline data for the ``inspect`` command (§9). Prints nothing."""
    path = os.path.abspath(path)
    doc = fitz.open(path)
    try:
        warnings: list[str] = []
        entries = read_outline(doc, warnings)
        total_pages = doc.page_count
        word_counts = [len(doc[i].get_text("text").split()) for i in range(total_pages)]
        total_words = sum(word_counts)
        words_per_page = (total_words / total_pages) if total_pages else 0.0
        book_title, author = derive_title(doc, path)

        levels: dict[int, dict] = {}
        for e in entries:
            if e.level > max_level:
                continue
            end = max(end_page(e, entries, total_pages), e.page)
            info = levels.setdefault(e.level, {"count": 0, "entries": []})
            info["count"] += 1
            info["entries"].append({
                "title": e.title,
                "start": e.page,
                "end": end,
                "words": sum(word_counts[e.page:end + 1]),
            })

        return {
            "source": path,
            "book_title": book_title,
            "author": author,
            "total_pages": total_pages,
            "has_text_layer": words_per_page >= 20,
            "words_per_page": words_per_page,
            "levels": levels,
            "warnings": warnings,
        }
    finally:
        doc.close()
