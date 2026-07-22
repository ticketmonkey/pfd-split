# Prompt 01 — Core engine + tests

Paste this into a fresh session started in `/home/whatnotbrewer/pdf-split`.

---

Read `/home/whatnotbrewer/pdf-split/prompts/00-SPEC.md` in full before writing any code. It is
the authoritative specification for this project and it settles every design question — do not
redesign anything in it, and do not add scope beyond it.

Build **stage 1 of 4: the core engine and its tests.** No CLI, no web server, no user-facing
output in this stage. Deliverables:

```
pdfsplit/__init__.py
pdfsplit/errors.py      # NoOutlineError, LevelNotFoundError
pdfsplit/model.py       # §4 dataclasses + JSON round-trip
pdfsplit/engine.py      # §5 planning pipeline
pdfsplit/verify.py      # §5.10 boundary verification
pdfsplit/sidecar.py     # §8 load/save
tests/conftest.py       # §12 synthetic fixtures
tests/test_engine.py
tests/test_verify.py
tests/test_sidecar.py
pyproject.toml          # §11
```

## Requirements specific to this stage

**`engine.py` must contain no HTTP and no terminal I/O — no `print`, no `rich`, no `click`.**
It takes a path plus options and returns a `BookPlan`. Both later skins are thin wrappers over
it. This module is what the tests exercise, so keep it pure and importable.

The public surface is:

```python
engine.plan_book(path, *, level=1, notebooklm=False, band=Band(),
                 extra_keep=(), extra_drop=(), overrides=None) -> BookPlan
engine.outline_summary(path, max_level=3) -> ...   # data for `inspect`; returns data, prints nothing
```

Use PyMuPDF (`import fitz`) for everything. Add no dependencies. Do not import `pypdf` even
though it is installed.

Implement the pipeline in the order given in §5.1–§5.11. The passes must be separable
functions so tests can drive them individually — in particular the oversize pass (§5.8), the
merge pass (§5.9) and the verify pass (§5.10) should each be callable on a chunk list.

Watch these specific traps:

- `get_toc()` returns **1-based** pages; the model is **0-based** throughout (§4). Convert on
  read, convert back only for display and `set_toc()`.
- End pages come from the next entry **at level ≤ the current entry's level in the full flat
  list**, not the next *selected* entry (§5.4). Getting this wrong makes chapters swallow the
  following Part and makes skipped sections leak their pages into neighbours.
- The chapter test in §5.5 runs **before** the skiplist, so "Chapter 9. Notes" is not dropped
  by the `^notes$` pattern.
- The merge pass compares `parent` identity, and `None == None` is what makes flat books work
  without a special case (§5.9). Merge only to escape the floor — never to fill toward target.
- Entries with an unresolvable destination (`page <= 0`) are dropped with a warning, not
  treated as page 0.

## Tests

Build the §12 fixtures with PyMuPDF at test time. Commit no binary fixtures. Every generated
page must carry its section title as real extractable text, or the verify tests have nothing
to match against.

Cover at minimum:

- `simple_book` at level 1 emits Ch1–Ch3; Cover, Contents, Index and "Appendix A: Tables" are
  all marked skipped with the matching reason; the glossary-style keep rule is exercised by
  adding a Glossary entry and asserting it survives.
- Chunk boundaries are exact and contiguous over kept ranges, and **the pages of skipped
  sections appear in no chunk**.
- The last chunk ends at `total_pages - 1`.
- `part_book` at level 2 with `notebooklm=True`: Part I's three short chapters merge into one
  chunk labelled after the Part; Part II's chapter stays separate. **Assert explicitly that no
  chunk spans the Part boundary.**
- `part_book` at level 1 produces two big Part-sized chunks — the documented reason `--level`
  exists.
- `deep_book`: the oversize chapter is subdivided into its level-2 children, each piece carries
  `subdivided_from` and `part_of`, and no pages are lost between the chapter opener and the
  first subsection.
- An oversize chapter with no children is left intact and produces a warning.
- `offset_book`: Ch2 gets `status == "snap_proposed"` with `proposed_start` equal to the page
  that actually holds the chapter opener. Assert the plan does **not** silently move the start.
- A proposal never crosses the previous or next chunk's start.
- `scanned_book`: `has_text_layer is False`, a warning is present, banding falls back to pages,
  every chunk is `not_applicable`, and — the regression that matters — **the whole book does
  not collapse into a single chunk**.
- `no_outline_book` raises `NoOutlineError`; requesting a level that does not exist raises
  `LevelNotFoundError` naming the available levels.
- Slug collisions between two identically-titled chapters resolve to `-2`.
- Sidecar round-trips; override keys stay stable across a `--level` change; a sidecar whose
  recorded `source_size`/`source_mtime` no longer match causes overrides to be **ignored with a
  warning** while level/mode/band are still restored.

Run the suite and paste the actual output. If anything fails, fix it — do not report a stage
as complete with failing or skipped tests.

## Done when

`pytest` passes, `python -c "from pdfsplit import engine"` works, and `pip install -e .`
resolves without downloading anything (every dependency is already installed at user level).
Stop there and summarise — stages 02–04 are separate prompts.
