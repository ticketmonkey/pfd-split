# Prompt 02 — Emission + CLI

Run after stage 01 is complete and its tests pass.

---

Read `/home/whatnotbrewer/pdf-split/prompts/00-SPEC.md` in full before writing any code, then
read the existing `pdfsplit/engine.py`, `pdfsplit/model.py` and `pdfsplit/sidecar.py` from
stage 01. The spec is authoritative — do not redesign anything in it.

Build **stage 2 of 4: PDF emission and the command-line interface.** Deliverables:

```
pdfsplit/render.py    # §7.1 generated header page
pdfsplit/write.py     # §7 chunk emission, --also-text, 00-index.md
pdfsplit/cli.py       # §9 click CLI
tests/test_write.py
```

`render.py` also gains the thumbnail function in stage 03; in this stage it only needs the
header page. Put the header page here rather than in `write.py` so stage 03 can reuse the
module cleanly.

## Emission (`write.py`)

Follow §7 exactly. The public surface:

```python
write.write_book(plan, out_root, *, prefix_book=False, also_text=False,
                 header_page=True) -> WrittenResult
```

Per chunk: `insert_pdf` the page range → prepend the header page → set metadata → write the
re-based sub-outline → save with `garbage=4, deflate=True`.

Traps in this stage:

- **The source document is opened read-only and nothing is ever written to its directory**
  (§3). All output, including the sidecar, goes under `<out>/<book_slug>/`.
- Sub-outline pages must be offset by `+1` for the prepended header page and re-based to
  1-based for `set_toc()` (§7 step 4). An off-by-one here silently produces bookmarks that
  land one page early in every chunk.
- Sub-outline levels are re-based relative to the chunk's own level and floored at 1;
  `set_toc()` rejects a list whose first entry is not level 1.
- Skip `set_toc()` entirely when the chunk contains no sub-entries rather than passing `[]`.
- Filenames use `chunk.seq`, never chapter numbers, zero-padded to the width of the total
  count (§6). The human label lives in `chunk.label`, `/Title`, the header page and the index.
- Never invent an edition or publisher string (§5.2). If `doc.metadata` lacks it, omit the line.

The header page uses PyMuPDF built-in Helvetica (`helv` / `hebo`) only — **no external font
files**, since the tool must work offline with no assets to ship.

## CLI (`cli.py`)

Implement `inspect`, `split` and a `serve` stub exactly as specified in §9, including the full
option table, the preview table format, and the exit codes (`0` ok, `1` error, `2` refused for
unresolved flags).

`serve` in this stage is a stub that prints "not yet implemented" and exits 1 — stage 03 fills
it in. Wire the `pdfsplit = pdfsplit.cli:main` entry point now.

Behaviour that matters:

- `inspect` writes nothing at all. It prints the outline tree per level with entry counts, page
  ranges and word counts, plus whether a text layer was found — enough to choose `--level`
  without guessing.
- `split` prints the preview and asks for confirmation by default.
- `--yes` enables batch use, but **refuses any book with `snap_proposed` or `unverified`
  boundaries** and exits 2 unless `--force` is also given. This is deliberate: unattended runs
  must not quietly emit chunks with boundaries nobody looked at.
- In a multi-book run (`pdfsplit split *.pdf --yes`), a book that raises prints its error,
  does **not** abort the remaining books, and makes the final exit code non-zero.
- On a successful split, write the sidecar (§8).
- Every skip, merge, subdivision and proposed correction is visible in the preview (§3.4).
  Use `rich` for the table; status must be readable without relying on colour alone.

## Tests

`tests/test_write.py`, using the stage-01 synthetic fixtures:

- Emitted chunk page count equals `end - start + 1`, plus one when a header page is prepended.
- With `--no-header-page` the count is exact and page 0 is the real chapter opener.
- Reopening an emitted chunk shows `/Title` equal to `"<book> — <label>"`.
- The chunk's sub-outline entries land on the correct pages **after** the header offset —
  assert an actual destination page, not just that a TOC exists.
- Filenames are zero-padded, sorted correctly, and unique; a duplicate chapter title yields
  `-2`.
- `--prefix-book` produces `<book_slug>__NN-slug.pdf`.
- `--also-text` writes one `.md` per chunk with the label as its H1.
- `00-index.md` lists every emitted chunk and has a Skipped section naming each skipped title
  with its reason.
- **The source file's mtime and size are unchanged after a split, and no new file appears in
  the source directory.** This guards invariant §3.1.
- A book with unresolved flags plus `--yes` and no `--force` exits 2 and writes nothing.

Run the full suite (stage 01 tests included) and paste the actual output. Do not report the
stage complete with failing or skipped tests.

## Done when

`pytest` passes, and this works end to end on a synthetic fixture:

```
pdfsplit inspect <fixture>.pdf
pdfsplit split <fixture>.pdf --notebooklm --out /tmp/pdfsplit-check --yes
```

producing `/tmp/pdfsplit-check/<book-slug>/` with numbered PDFs, `00-index.md` and
`.pdfsplit.json`. Stop there — stage 03 is a separate prompt.
