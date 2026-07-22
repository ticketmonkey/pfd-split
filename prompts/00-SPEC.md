# pdfsplit — Authoritative Specification

This is the single source of truth. Build prompts `01`–`04` reference it. If a build prompt
and this spec disagree, this spec wins. Do not redesign anything here — the decisions below
were settled deliberately. If something is genuinely underspecified, pick the simplest option
and note it in the README rather than inventing scope.

---

## 1. Purpose

Split DRM-free technical ebook PDFs into chapter-sized PDFs for import into NotebookLM.

A whole book fed to NotebookLM produces one ~20-minute Audio Overview that skims everything.
One chapter per source gives each chapter its own 20 minutes and scopes flashcards to a topic.

**The tool's job ends at a folder of PDFs on disk.** NotebookLM has no public API for adding
sources; upload is manual drag-and-drop. Do not build, attempt, or suggest upload automation.

Books in scope: publisher PDFs (O'Reilly, Manning, Packt, Cisco Press, No Starch) that ship a
real bookmark outline. Outline-less and scanned books are explicitly out of scope for
detection, but must fail loudly rather than guess.

---

## 2. Environment

Already installed at user level (`~/.local/lib/python3.11/site-packages`), no venv, Python 3.11:

- PyMuPDF 1.25.1 (`import fitz`)
- FastAPI 0.115.6, uvicorn 0.34.0, starlette 0.41.3, pydantic 2.10.4, jinja2 3.1.6
- click, rich
- pypdf 6.13.2 — **installed but do not use it**

**Add no new dependencies.** Use PyMuPDF for everything: outline reading, text extraction,
page subsetting, metadata, sub-TOC writing, thumbnail rendering, header-page generation.

Node 18 and npm 9 exist. **Do not use them.** No build step, no bundler, no framework.
The web frontend is hand-written HTML + CSS + vanilla JS served as static files.

---

## 3. Non-negotiable invariants

1. **Source PDFs are opened read-only and nothing is ever written to the source directory.**
   All output — chunks, index, sidecar, thumbnail cache — goes under the output directory.
2. A page belongs to the chapter that *starts* on it. Chunk spans `[start, end]` inclusive,
   0-based page indices.
3. Pages belonging to skipped sections are dropped, never absorbed into a neighbouring chunk.
4. Nothing is skipped, merged, subdivided, or boundary-corrected **silently**. Every such
   action is visible in the preview, the UI, and `00-index.md`.
5. Boundary corrections are *proposed*, never auto-applied without a human accept.
6. `engine.py` contains no HTTP and no terminal I/O. It takes a path plus options and returns
   a plan object. CLI and web are thin skins over the same functions.

---

## 4. Data model

All page indices in the data model are **0-based**. PyMuPDF's `get_toc()` returns 1-based
pages — normalize on read. Only convert back to 1-based for display and for `set_toc()`.

```python
@dataclass(frozen=True)
class OutlineEntry:
    idx: int                  # index into the flat entry list
    level: int                # 1-based, as returned by get_toc()
    title: str                # raw title from the outline
    page: int                 # 0-based; -1 if the destination is unresolvable
    parent: int | None        # idx of nearest preceding entry at level-1, else None

@dataclass
class VerifyResult:
    status: str               # "ok" | "snap_proposed" | "unverified" | "not_applicable"
    score: float              # 0.0–1.0 token-overlap score at the current start page
    proposed_start: int | None
    proposed_score: float | None
    checked_pages: list[int]

@dataclass
class Chunk:
    seq: int                  # 1-based emission order; assigned last, after all passes
    label: str                # human label, e.g. "Chapter 3 — Path Selection"
                              #                   "Chapters 5–7 — BGP Policy"
                              #                   "Chapter 7 (part 2 of 3) — Troubleshooting"
    slug: str                 # filename slug, unique within the book
    start: int                # 0-based inclusive
    end: int                  # 0-based inclusive
    words: int
    entries: list[int]        # OutlineEntry.idx values this chunk covers
    parent: int | None        # shared outline parent, used by the merge pass
    merged_from: list[str] | None   # original titles, when this is a merge product
    subdivided_from: str | None     # original chapter title, when an oversize split product
    part_of: tuple[int, int] | None # (2, 3) meaning "part 2 of 3"
    skip_reason: str | None   # non-None => not emitted, but kept in the plan for display
    include: bool             # user override; False => not emitted
    verify: VerifyResult

@dataclass
class Band:
    floor: int = 6000
    target: int = 12000
    ceiling: int = 20000

@dataclass
class BookPlan:
    source: str               # absolute path
    book_title: str
    book_slug: str
    author: str | None
    total_pages: int
    level: int
    notebooklm: bool
    band: Band
    has_text_layer: bool
    words_per_page: float
    chunks: list[Chunk]       # includes skipped chunks, in document order
    warnings: list[str]
```

---

## 5. Planning pipeline

Entry point: `engine.plan_book(path, *, level=1, notebooklm=False, band=Band(),
extra_keep=(), extra_drop=(), overrides=None) -> BookPlan`

### 5.1 Read outline

`doc.get_toc(simple=True)` → `[[level, title, page1based], ...]`.

- Empty or missing outline → raise `NoOutlineError`. **Do not fall back to heuristics.**
- An entry with `page <= 0` (unresolvable destination) → drop it and append a warning.
- Derive `parent` with a stack: for entry at level L, parent is the most recent preceding
  entry at level L-1. Entries at level 1 have `parent = None`.

### 5.2 Book title

`doc.metadata.get("title")` if non-empty and not obviously junk (not the filename, length > 2),
else the source filename with the extension stripped and separators normalized to spaces.
`author` from `doc.metadata.get("author")` or None. **Never invent an edition or publisher** —
if the metadata does not contain it, omit that line everywhere.

### 5.3 Select level

Entries where `level == requested_level`. If none exist, raise `LevelNotFoundError` listing
each available level with its entry count. Preserve document order.

### 5.4 Derive end pages

For each selected entry, `end` = (start page of the next entry **in the full flat list** whose
`level <= this entry's level`) − 1. If there is no such entry, `end = total_pages - 1`.

Using the full flat list — not just selected entries — is what makes skipped sections drop
their pages correctly and stops a chapter from swallowing the following Part.

If `end < start` after derivation, clamp `end = start` and add a warning.

### 5.5 Skiplist

Match against the **raw lowercased, stripped** title, in this order — first match wins:

1. `--keep` user regexes → keep.
2. Chapter test: `^\s*(chapter|ch\.?|lesson|module)\s*\d+` or `^\s*\d+\s*[.:\-–—]\s` → keep.
   This runs before the skiplist so a chapter titled "Chapter 9. Notes" is never dropped.
3. `KEEP_PATTERNS` → keep.
4. `--drop` user regexes → skip, reason `"user --drop: <pattern>"`.
5. `SKIP_PATTERNS` → skip, reason `"skiplist: <pattern>"`.
6. Otherwise keep.

```python
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
```

Appendices are dropped by design. Glossary is kept by design — it is good flashcard material.

Skipped chunks stay in `BookPlan.chunks` with `skip_reason` set so the preview can show them.
They are excluded from the oversize, merge, and verify passes.

### 5.6 Word counts

`len(doc[i].get_text("text").split())`, cached in a per-page list computed once.
Chunk words = sum over `[start, end]`.

### 5.7 No-text-layer fallback

`words_per_page = total_words / total_pages`. If `words_per_page < 20`:

- `has_text_layer = False`
- Warning: `"No usable text layer (N words over M pages). Banding by page count; boundary
  verification unavailable."`
- Band converts to pages at **400 words/page** → floor 15, target 30, ceiling 50 pages.
  All banding logic uses page counts instead of word counts.
- Every chunk gets `verify.status = "not_applicable"`.

This exists because word-count banding on a scan would merge the entire book into one chunk —
the exact opposite of the tool's purpose.

### 5.8 Oversize pass

Applies in **both** modes (a 40k-word chapter is bad regardless of `--notebooklm`), using
`band.ceiling`.

For each kept chunk over the ceiling:

- Find its children: outline entries whose `parent == chunk's single entry idx`.
- No children → leave it, add warning `"'<title>' is N words with no deeper outline to split on"`.
- Otherwise replace it with chunks derived from the children (end pages recomputed by §5.4,
  clamped to the parent's range; the first child chunk starts at the **parent's** start so no
  pages are lost between the chapter opener and its first subsection).
- Recurse until every piece is ≤ ceiling or has no children.
- Each piece gets `subdivided_from = <parent title>` and `part_of = (i, total)`.
- Label: `"Chapter 7 (part 2 of 3) — <child title>"`.

### 5.9 Merge pass — `--notebooklm` only

Greedy, minimal, document order over kept chunks only:

```
i = 0
while i < len(chunks):
    group = [chunks[i]]
    total = chunks[i].words
    j = i + 1
    while (total < band.floor
           and j < len(chunks)
           and chunks[j].parent == chunks[i].parent     # both None on a flat book = OK
           and not is_keeplisted(chunks[j])             # never merge glossary into a chapter
           and not is_keeplisted(chunks[i])
           and chunks[j].subdivided_from is None        # never re-merge oversize split products
           and chunks[i].subdivided_from is None
           and total + chunks[j].words <= band.ceiling):
        group.append(chunks[j]); total += chunks[j].words; j += 1
    emit_group(group)
    i = j
```

Merge **only to escape the floor**, never to fill toward the target. The `parent` equality test
is what stops a merge crossing a Part boundary; it also handles flat books uniformly, since
`None == None`.

Group naming:

- All members share a non-`None` parent → `label = "Chapters 5–7 — <parent title>"`,
  `slug = slugify(parent title)`.
- Otherwise → `label = "<first title> / <last title>"`,
  `slug = slugify(first)[:28] + "-and-" + slugify(last)[:28]`.

`merged_from` = the original member titles. A single-member group is not a merge — leave
`merged_from = None` and use the chunk's own title.

`band.target` is advisory only: it appears in the preview and `00-index.md` to show how far
each chunk is from ideal. No algorithm branches on it.

### 5.10 Verify pass

Skipped when `has_text_layer` is False.

```python
STOPWORDS = {"the","a","an","and","or","of","to","in","for","with","on","at","by",
             "chapter","part","section","appendix","introduction"}

def tokens(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) >= 3 and t not in STOPWORDS}
```

- `title_tokens = tokens(chunk's first entry title)`. If empty → `status = "unverified"`, done.
- `score(page) = len(title_tokens & tokens(page_text)) / len(title_tokens)`
- `score(start) >= 0.7` → `status = "ok"`.
- Otherwise score pages `start-3 .. start+3` (excluding `start`, clamped to the document, and
  clamped so a proposal never crosses the previous chunk's start or the next chunk's start).
  Best candidate with `score >= 0.7` and `score > score(start)` → `status = "snap_proposed"`,
  `proposed_start` set. No candidate qualifies → `status = "unverified"`.
- Record every page tried in `checked_pages`.

This catches misaligned bookmarks, which is the common failure. It does not catch a bookmark
pointing at the right page for the wrong reason, and it degrades on image-typeset chapter
openers. That is why a failure marks the boundary for review and never guesses.

### 5.11 Apply overrides, assign sequence

Overrides from the sidecar (§8) are applied after verification:
`starts` replaces `chunk.start` (and re-derives the previous chunk's `end`);
`include` sets `chunk.include`. An accepted start override sets `verify.status = "ok"`.

Finally assign `seq` 1..N over emitted chunks in document order (kept, included, not skipped).

---

## 6. Naming

```python
def slugify(s: str, maxlen: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^\s*(chapter|ch\.?|part|appendix|lesson|module)\s*[0-9ivxlcdm]*\s*[:.\-–—]?\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:maxlen].rstrip("-")
    return s or "untitled"
```

Collisions within a book get `-2`, `-3`, … appended.

**Filename: `NN-slug.pdf`** where `NN` is `chunk.seq` zero-padded to the width of the total
count (2 digits normally, 3 if over 99 chunks). Sequence numbers — not chapter numbers — so
files always sort correctly and are always unique. With front matter skipped these usually
coincide anyway. **No letter suffixes, no ranges in filenames.** The human-meaningful label
("Chapters 5–7 — BGP Policy", "Chapter 7 (part 2 of 3)") lives in `chunk.label`, the PDF
`/Title`, the header page, and `00-index.md`.

With `--prefix-book`: `<book_slug>__NN-slug.pdf`. Use this when building a topic notebook fed
from several books, where identical chapter titles would otherwise be indistinguishable.

Output directory: `<out>/<book_slug>/`. `book_slug = slugify(book_title)`.

---

## 7. Emission

Per chunk, in `write.py`:

1. `new = fitz.open()` then `new.insert_pdf(src, from_page=chunk.start, to_page=chunk.end)`.
2. Unless `--no-header-page`, prepend the generated header page (§7.1) at index 0.
3. `new.set_metadata({"title": f"{book_title} — {chunk.label}", "author": author or "",
   "subject": f"pages {chunk.start+1}–{chunk.end+1} of {source_filename}",
   "producer": "pdfsplit"})`
4. Sub-outline: take flat outline entries whose page falls in `[chunk.start, chunk.end]`,
   re-level relative to the chunk's own base level (`entry.level - base_level + 1`, floored at 1),
   offset pages to `entry.page - chunk.start + 1 + header_offset` (1-based for `set_toc`,
   `header_offset` = 1 if a header page was prepended else 0). Call `new.set_toc(toc)`.
   Skip if the resulting list is empty.
5. `new.save(path, garbage=4, deflate=True)`, then `new.close()`.

### 7.1 Header page

New page matching the chunk's first page dimensions, white, built with PyMuPDF built-in
Helvetica (`helv`, `hebo`) — **no external font files**. Vertically positioned in the upper
third, generous margins, left-aligned:

```
<Book Title>                          18pt hebo
<Author>                              10pt helv, grey (0.4), omit if absent
────────────────────────────────      hairline rule
<chunk.label>                         14pt hebo
Chapter 3 of 14                       10pt helv          ← position within the book
Source pages 78–101 · bgp-routing.pdf  9pt helv, grey (0.5)
```

"Chapter 3 of 14" uses `chunk.seq` and the total emitted count. For merged and subdivided
chunks state that explicitly: `"Chapters 5–7 of 14"`, `"Chapter 7, part 2 of 3"`.

This page exists because a chunk PDF is otherwise a context-free slab that opens mid-sentence
with no indication of which book it came from — it tells the model what it is reading and
where the material sits in the book's arc.

### 7.2 `--also-text`

Alongside each chunk, `NN-slug.md`:

```markdown
# <chunk.label>

> <Book Title> — source pages 78–101

<page 1 text>

<page 2 text>
```

Page text via `page.get_text("text")`, pages joined with `\n\n`. Off by default: text loses
every diagram, which carries real meaning in technical books. It exists for books whose code
listings extract badly from PDF.

### 7.3 `00-index.md`

Written once per book into the output directory:

```markdown
# <Book Title>

Source: `/abs/path/book.pdf` · 412 pages · split at level 1 · notebooklm mode (6000/12000/20000)
Generated 14 chunks · 11 verified, 2 corrected, 1 unverified

| # | File | Contents | Source pages | Words |
|---|------|----------|--------------|-------|
| 1 | `01-bgp-fundamentals.pdf` | Chapter 1 — BGP Fundamentals | 45–78 | 12,800 |
| 2 | `02-bgp-policy.pdf` | Chapters 5–7 — BGP Policy *(merged)* | 102–141 | 9,100 |

## Skipped
- **Index** — pages 380–411 — skiplist: `^index$`
- **Appendix A: BGP Attributes** — pages 350–379 — skiplist: `^appendix\b`

## Warnings
- Bookmark for "Route Reflection" pointed at p.102; corrected to p.103.
```

---

## 8. Sidecar persistence

`<out>/<book_slug>/.pdfsplit.json`. Written to the **output** directory, never beside the source.

```json
{
  "version": 1,
  "source": "/abs/path/bgp-routing.pdf",
  "source_size": 41234567,
  "source_mtime": 1690000000.0,
  "level": 1,
  "notebooklm": true,
  "band": {"floor": 6000, "target": 12000, "ceiling": 20000},
  "overrides": {
    "starts":  {"1:101:route-reflection": 102},
    "include": {"1:349:appendix-a-bgp-attributes": true}
  }
}
```

**Override key** = `f"{entry.level}:{entry.page}:{slugify(entry.title)}"` using the entry's
*original* outline page, so keys stay stable when `--level` changes or a start is corrected.

On load: if `source_size` or `source_mtime` differ from the file on disk, **ignore all
overrides** and warn — page numbers from a different revision of the file are dangerous.
Level, mode and band are still restored.

Written automatically whenever the web UI accepts a correction or runs a split, and whenever
the CLI completes a split. Hand-editable, which doubles as the escape hatch for a
pathological book, but you should never need to open it normally.

---

## 9. CLI

```
pdfsplit inspect BOOK.pdf [--max-level 3]
pdfsplit split BOOK.pdf [BOOK2.pdf ...] [options]
pdfsplit serve [--library DIR] [--out DIR] [--port 8000] [--no-browser]
```

`inspect` writes nothing. It prints, via `rich`: book title, page count, whether a text layer
was found, and the outline tree per level with entry counts, page ranges and word counts, so
the `--level` choice is informed.

`split` options:

| Flag | Default | Meaning |
|---|---|---|
| `--level N` | `1` | Outline depth to split on |
| `--notebooklm` / `-n` | off | Enable merge pass |
| `--floor N` | `6000` | Merge below this many words |
| `--target N` | `12000` | Advisory ideal, shown in preview only |
| `--ceiling N` | `20000` | Subdivide above this many words |
| `--out DIR` | `./out` | Output root; book goes in `<out>/<book_slug>/` |
| `--prefix-book` | off | Prefix filenames with the book slug |
| `--also-text` | off | Also emit `.md` per chunk |
| `--no-header-page` | off | Suppress the generated header page |
| `--keep REGEX` | — | Repeatable; force-keep matching titles |
| `--drop REGEX` | — | Repeatable; force-drop matching titles |
| `--dry-run` | off | Print the plan, write nothing |
| `--yes` / `-y` | off | Skip confirmation |
| `--force` | off | With `--yes`, proceed despite unresolved flags |

Default behaviour prints the preview and asks for confirmation. `--yes` enables batch
(`pdfsplit split *.pdf --yes`) but **refuses any book with `snap_proposed` or `unverified`
boundaries** unless `--force` — those books get opened in the web UI individually.

Preview format (`rich` table), one row per chunk including skipped ones:

```
  #  Status  Contents                            Pages      Words
  1  ok      Chapter 1 — BGP Fundamentals        45–78     12,800
  2  SNAP    Chapter 2 — Route Reflection        102–129    8,400   → start p.103
  -  skip    Index                               380–411      800   skiplist: ^index$

  12 of 14 verified · 1 correction proposed · 1 unverified
```

Exit codes: `0` success · `1` error (no outline, level not found, unreadable file) ·
`2` refused because of unresolved boundary flags. In multi-book runs, a failing book prints
its error, does not abort the run, and makes the final exit code non-zero.

---

## 10. Web UI

`pdfsplit serve` binds **127.0.0.1 only** — never `0.0.0.0`. These are copyrighted books you
own; nothing leaves the machine and nothing is uploaded. The browser browses a local library
directory server-side.

### 10.1 API

Book IDs are `sha1(abspath).hexdigest()[:12]`, resolved through a dict built by the library
scan. **Never accept a filesystem path in a URL** — that is the path-traversal guard.

```
GET  /                                    → static index.html
GET  /api/library                         → {"dir": str,
                                             "books": [{"id","filename","title","pages","has_outline"}]}
GET  /api/books/{id}/plan                 → BookPlan as JSON
       ?level=1&notebooklm=true&floor=6000&target=12000&ceiling=20000
GET  /api/books/{id}/page/{n}.png?w=280   → image/png thumbnail of 0-based page n
POST /api/books/{id}/overrides            → body {"starts": {...}, "include": {...}}; saves sidecar
POST /api/books/{id}/split                → body: same options as the plan query plus
                                             {"prefix_book","also_text","header_page"}
                                          → {"out_dir": str, "written": [str], "index": str}
```

Thumbnails: `page.get_pixmap(matrix=fitz.Matrix(z, z))` where `z` is chosen to hit the
requested width; PNG bytes; in-memory LRU cache of ~200 entries keyed by `(id, n, w)`.
Server-side rendering is deliberate — it avoids a JavaScript PDF library entirely.

### 10.2 Page

One page, three regions:

- **Left rail** — library book list from `/api/library`; books without an outline shown
  greyed with a "no outline" tag and not selectable.
- **Toolbar** — level selector (populated from the plan's available levels), NotebookLM
  toggle, floor/target/ceiling numeric inputs, output dir, `--prefix-book` / `--also-text` /
  header-page checkboxes, and the Split button.
- **Chunk list** — one row per chunk in document order.

Row states:

- **Verified (`ok`)** — collapsed single line: status dot, seq, label, page range, word count,
  include checkbox. Word count rendered against the band (under floor / in band / over ceiling
  visually distinct).
- **Flagged (`snap_proposed` or `unverified`)** — **auto-expanded**, showing a horizontal
  thumbnail strip of pages `start-2 … start+2` with the current start highlighted and any
  proposed page marked, plus `◀ ▶` nudge buttons and, for `snap_proposed`, an
  **Accept p.103** button.
- **Skipped** — dimmed line showing the skip reason, with a checkbox to force-include it.

Header line: `12 of 14 auto-verified · 2 need review`. This is the whole point of the UI —
a 14-chapter book is two thumbnails to check, not fourteen. At 30 books × ~15 chapters,
reviewing everything by hand degrades into reflexively clicking "fine", which is the same as
trusting bookmarks blindly.

The Split button is disabled while flags are unresolved, with an explicit "split anyway"
checkbox to override. Accepting a correction or toggling include immediately POSTs to
`/api/books/{id}/overrides`, so reopening the book later shows the work already done.

### 10.3 Frontend style

Utilitarian and dense — a technical tool, not a landing page. System font stack, a compact
type scale, one accent colour, generous use of whitespace *between* groups but tight rows.
Status communicated by both colour and shape/icon, never colour alone. Respect
`prefers-color-scheme`. No external requests of any kind: no CDN, no web fonts, no remote
images — the server must work fully offline.

---

## 11. Project layout

```
pdf-split/
  pdfsplit/
    __init__.py
    errors.py     # NoOutlineError, LevelNotFoundError
    model.py      # dataclasses from §4 + to_dict/from_dict
    engine.py     # §5 planning pipeline
    verify.py     # §5.10
    render.py     # thumbnails + header page (§7.1)
    write.py      # §7 emission, --also-text, 00-index.md
    sidecar.py    # §8
    cli.py        # §9  (click)
    web.py        # §10 (fastapi)
    static/
      index.html
      app.js
      style.css
  tests/
    conftest.py
    test_engine.py
    test_verify.py
    test_write.py
    test_sidecar.py
  pyproject.toml
  README.md
```

`pyproject.toml`: name `pdfsplit`, requires-python `>=3.11`, dependencies
`pymupdf`, `click`, `rich`, `fastapi`, `uvicorn`, entry point
`pdfsplit = pdfsplit.cli:main`. Install with `pip install -e .` — every dependency is already
present, so this should resolve without downloading anything.

---

## 12. Test fixtures

`tests/conftest.py` builds synthetic PDFs with PyMuPDF at test time — **no binary fixtures
committed**. Each generated page carries its section title as real text so verification has
something to match.

| Fixture | Shape |
|---|---|
| `simple_book` | 60 pp, flat level-1 outline: Cover 0, Contents 2, Ch1 5, Ch2 15, Ch3 30, "Appendix A: Tables" 45, Index 52 |
| `part_book` | Parts at level 1, chapters at level 2; Part I has three ~2k-word chapters (exercises merging), Part II has one ~10k chapter (must not merge across the Part boundary) |
| `offset_book` | Like `simple_book` but Ch2's bookmark points at page 14 while the text "Chapter 2" is on page 15 |
| `deep_book` | One chapter far above the ceiling with level-2 subsections (exercises the oversize pass) |
| `scanned_book` | Pages containing only a drawn rectangle, zero extractable text |
| `no_outline_book` | Valid PDF with no bookmarks at all |

---

## 13. Explicitly out of scope

Do not build, and do not suggest building:

- Heuristic or OCR chapter detection for outline-less or scanned PDFs.
- Auto-merging in default mode; title-keyword or LLM-judged relatedness.
- Library management: tracking which books have been split, cross-book state, a central store.
- Any NotebookLM upload automation or browser scripting.
- Deployment, authentication, multi-user support, or binding to anything but `127.0.0.1`.
- Any npm/Node build step or frontend framework.

---

## 14. Known-uncertain

The band defaults (6000 / 12000 / 20000 words) are **an estimate, not measured**. There is no
reliable public data on how NotebookLM's Audio Overview quality scales with source size, and
NotebookLM offers options to request longer overviews that would shift the ceiling. They are
starting values, which is exactly why they are flags and why the preview makes retuning free.

Validate against one real book and one real Audio Overview before splitting a whole library
(see prompt `04-validate.md`).
