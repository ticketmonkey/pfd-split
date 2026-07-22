# Build prompts for pdfsplit

Five files. `00-SPEC.md` is the authoritative specification; `01`–`04` are staged build
prompts that each reference it.

## How to use these

Start a fresh session in `/home/whatnotbrewer/pdf-split` and paste the contents of one prompt
file. Each stage is self-contained given the spec — a new session needs nothing from the
conversation these were written in.

| Stage | Prompt | Builds | Depends on |
|---|---|---|---|
| 1 | `01-engine.md` | Planning engine, verification, sidecar, tests | — |
| 2 | `02-cli.md` | PDF emission, header pages, `00-index.md`, CLI | stage 1 |
| 3 | `03-web.md` | Localhost FastAPI review UI, thumbnails | stages 1–2 |
| 4 | `04-validate.md` | Real-book survey, end-to-end check, band tuning, README | stages 1–3 |

Do not skip stage 4. It is the only stage that tests whether the premise is true rather than
whether the code runs.

## Running them

The simplest path is one session per stage:

```
cd /home/whatnotbrewer/pdf-split
claude
> [paste 01-engine.md]
```

Stages 2 and 3 can share a session with the stage before if context allows, but each prompt
re-reads the spec and the existing modules, so a fresh session per stage is safe and cheap.

Stage 4 wants a real ebook library and, for Part C, manual work in NotebookLM.

## If a stage goes wrong

`00-SPEC.md` wins over anything a build prompt or a model says. If the code and the spec
disagree, the code is wrong. If you decide the *spec* is wrong, edit `00-SPEC.md` first and
then rerun the affected stage — otherwise the next stage will rebuild against the old
assumption.

## Ground rules baked into every prompt

- No new dependencies. PyMuPDF, FastAPI, uvicorn, click and rich are already installed at user
  level. `pypdf` is installed but deliberately unused.
- No npm, no bundler, no frontend framework, no CDN, no web fonts. Node 18 exists; it is not
  used.
- Source PDFs are opened read-only. Nothing is ever written to the source directory.
- The web server binds `127.0.0.1` only.
- Nothing is skipped, merged, subdivided or boundary-corrected silently.
- No NotebookLM upload automation — there is no public API, and upload stays manual.
