# Prompt 04 — Validate against real books, then tune

Run after stages 01–03 are complete and their tests pass. This stage writes almost no code —
it exists to check that the thing works on real publisher PDFs and that the premise holds.

---

Read `/home/whatnotbrewer/pdf-split/prompts/00-SPEC.md`, in particular **§14 Known-uncertain**,
then read the code from stages 01–03.

## Why this stage exists

Every design decision assumes two things that have never been measured:

1. That publisher outlines behave the way the spec expects — chapters at a consistent level,
   destinations pointing at real chapter openers, titles that the skiplist patterns actually
   match.
2. That chapter-sized sources produce better NotebookLM Audio Overviews than whole-book ones,
   and that the band defaults (6000 / 12000 / 20000 words) land near the sweet spot.

The second one is a **guess**. There is no reliable public data on how Audio Overview quality
scales with source size, and NotebookLM offers options to request longer overviews that would
shift the ceiling. If the band is wrong it is a flag change; if chapter-sized chunks do not
help at all, that is worth discovering after one book rather than thirty.

**Do not tune the defaults on synthetic fixtures. Only real books count here.**

## Part A — real outline survey

Ask me for the path to my ebook library. Then run `pdfsplit inspect` across every PDF in it
and produce a short written report covering:

- How many books have an outline at all, and which do not.
- For each book, which level holds the chapters — and specifically how many are Shape A
  (chapters at level 1) versus Shape B (Parts at level 1, chapters at level 2) versus flat.
- Word count distribution per chapter across the whole library: median, and how many chapters
  fall below the 6000 floor or above the 20000 ceiling. **This is the evidence for whether the
  band defaults are sane.**
- Every outline title that the skiplist dropped, and every title it kept that looks like front
  or back matter it should have dropped. False positives matter more than false negatives —
  wrongly dropping a real chapter is the worst outcome.
- Any book where `has_text_layer` is False.

Do not change any defaults yet. Report first, and tell me what the numbers actually say —
including if they say the defaults are fine.

## Part B — end-to-end on one book

Pick the most structurally typical book from Part A, then:

1. `pdfsplit split <book> --notebooklm --out ./out` and read the preview carefully.
2. For every boundary flagged `snap_proposed` or `unverified`, open the book in the web UI and
   check the thumbnails yourself. Record how many proposals were **correct**, how many were
   wrong, and — the number that actually matters — how many boundaries were marked `ok` but
   were in fact wrong. Sample ~5 `ok` boundaries by hand to estimate that false-negative rate.
3. Open three emitted chunks in a PDF reader. Confirm each starts at its real chapter opener,
   carries a correct header page, has a working sub-outline landing on the right pages, and
   ends where the next chapter begins.
4. Confirm the source PDF is untouched: same size, same mtime, no new files beside it.

If the verify pass has a meaningful false-negative rate, say so plainly with the numbers rather
than reporting success — the 0.7 token-overlap threshold in §5.10 is the tunable, and it may
need to move.

## Part C — the premise test

This is the only step that validates the reason the project exists, and I have to do it by
hand — you cannot automate it, and there is no NotebookLM API to add sources.

Tell me to:

1. Create a notebook and upload one book's output folder.
2. Generate an Audio Overview for a single chapter chunk.
3. Compare it against an Audio Overview of the same book unsplit.

Then ask me what I found, and specifically whether chunks felt too thin, about right, or still
too broad. Translate my answer into a concrete band adjustment and change the defaults in the
code — do not leave the numbers as they are just because they were written down first.

## Part D — write it up

Once Parts A–C are done, write `README.md` at the project root:

- What the tool does and the problem it solves, in a few lines.
- Install (`pip install -e .`) and the three commands with real examples.
- The full `split` option table from §9.
- A short "choosing `--level`" section using the real Shape A / Shape B findings from Part A.
- The band defaults **with a note on what evidence they now rest on** after Part C.
- The manual NotebookLM upload step, stated plainly as manual.
- The §13 out-of-scope list, so the boundaries stay obvious later.

Keep it short. It is a personal tool; a README that outgrows the code is a liability.
