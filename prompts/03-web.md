# Prompt 03 — Local web UI

Run after stages 01 and 02 are complete and their tests pass.

---

Read `/home/whatnotbrewer/pdf-split/prompts/00-SPEC.md` in full before writing any code —
§10 is the section that governs this stage — then read the existing `pdfsplit/engine.py`,
`pdfsplit/write.py`, `pdfsplit/render.py` and `pdfsplit/sidecar.py`. The spec is
authoritative; do not redesign anything in it.

Build **stage 3 of 4: the localhost boundary-review UI.** Deliverables:

```
pdfsplit/render.py     # add the thumbnail function
pdfsplit/web.py        # §10.1 FastAPI app
pdfsplit/static/index.html
pdfsplit/static/app.js
pdfsplit/static/style.css
pdfsplit/cli.py        # replace the `serve` stub with the real thing
tests/test_web.py
```

## Why this UI exists

Everything upstream rests on trusting bookmark destinations, and a text preview cannot show
whether a boundary is actually right — you would find out weeks later via a bad podcast. A
browser can render the page.

But the UI only pays for itself if it does **not** ask you to check everything: 30 books ×
~15 chapters is ~450 boundaries, and past book three you start reflexively clicking "fine",
which is trusting bookmarks blindly with extra steps. So the engine's verify pass does the
first pass, and **only flagged rows expand**. A 14-chapter book is two thumbnails to check.
Preserve that property in every design decision here.

## Backend (`web.py`)

Implement the API in §10.1 exactly — routes, query parameters, request and response shapes.

Hard requirements:

- **Bind `127.0.0.1` only. Never `0.0.0.0`.** These are copyrighted books; nothing leaves the
  machine and nothing is uploaded. PDFs are read server-side from a local library directory.
- Book IDs are `sha1(abspath).hexdigest()[:12]`, resolved through a dict built by the library
  scan. **Never accept a filesystem path in a URL** — that is the path-traversal guard. Reject
  unknown IDs with 404.
- Thumbnails render server-side with `page.get_pixmap(matrix=fitz.Matrix(z, z))`, returned as
  PNG, behind an in-memory LRU of ~200 keyed by `(id, n, width)`. This is deliberate: it means
  no JavaScript PDF library exists in this project at all.
- `POST /api/books/{id}/overrides` writes the sidecar (§8) immediately, so a correction
  survives closing the tab.
- Keep open `fitz.Document` handles in a small cache rather than reopening per thumbnail
  request; close them on shutdown.
- All planning goes through `engine.plan_book` — `web.py` must contain **no** planning logic
  of its own.

`cli.py serve` starts uvicorn on the given port and opens a browser unless `--no-browser`.

## Frontend

Hand-written HTML + CSS + vanilla JS. **No npm, no bundler, no framework, no CDN, no web
fonts, no remote images.** Node exists on this machine — do not use it. The server must work
fully offline.

Layout per §10.2: left rail (library list, outline-less books greyed and unselectable),
toolbar (level selector populated from the plan's available levels, NotebookLM toggle,
floor/target/ceiling inputs, output dir, prefix-book / also-text / header-page checkboxes,
Split button), and the chunk list.

Row behaviour, which is the core of the stage:

- `ok` → collapsed single line: status dot, seq, label, page range, word count, include
  checkbox. Render the word count against the band so under-floor / in-band / over-ceiling are
  visually distinct at a glance.
- `snap_proposed` or `unverified` → **auto-expanded**, with a horizontal thumbnail strip of
  pages `start-2 … start+2`, the current start highlighted, any proposed page marked, `◀ ▶`
  nudge buttons, and for `snap_proposed` an **Accept p.N** button.
- skipped → dimmed, showing the skip reason, with a checkbox to force-include.

Header line reads `12 of 14 auto-verified · 2 need review`.

The Split button is disabled while flags are unresolved, with an explicit "split anyway"
checkbox to override — mirroring the CLI's `--yes` / `--force` relationship. Accepting a
correction or toggling include POSTs to `/overrides` immediately.

Style per §10.3: utilitarian and dense, a technical tool rather than a landing page. System
font stack, compact type scale, one accent colour, tight rows with whitespace between groups.
Status must be communicated by shape or icon **as well as** colour, never colour alone.
Respect `prefers-color-scheme` for light and dark.

## Tests

`tests/test_web.py` with `fastapi.testclient.TestClient` over a temp library directory holding
the stage-01 synthetic fixtures:

- `/api/library` lists the fixtures; `no_outline_book` appears with `has_outline: false`.
- `/api/books/{id}/plan` returns the same chunk boundaries as calling `engine.plan_book`
  directly with the same options — the API must not drift from the engine.
- Changing `level` and `notebooklm` in the query changes the plan accordingly.
- `/page/{n}.png` returns `image/png` with a valid PNG signature; an out-of-range page is 404.
- An unknown or forged book ID is 404 — include a case with a path-like ID such as
  `..%2F..%2Fetc%2Fpasswd` and assert it does not resolve.
- POSTing overrides writes the sidecar, and a subsequent plan request reflects them.
- POSTing split writes files into the requested output dir and returns their paths.
- **The source PDFs are unchanged (size and mtime) after a full round trip**, guarding §3.1.

Run the full suite (stages 01–03) and paste the actual output. Do not report the stage
complete with failing or skipped tests.

## Done when

`pytest` passes and `pdfsplit serve --library <dir with fixtures>` opens a working page where
you can select a book, see collapsed verified rows and an auto-expanded flagged row with real
thumbnails, accept a correction, and split. Then verify by hand that reopening the same book
shows the correction already applied from the sidecar.

Stop there — stage 04 is a separate prompt.
