"""Engine tests (§5) — planning, skiplist, oversize, merge, overrides, naming."""

from __future__ import annotations

import fitz
import pytest

from pdfsplit import engine
from pdfsplit.engine import override_key, plan_book, slugify
from pdfsplit.errors import LevelNotFoundError, NoOutlineError
from pdfsplit.model import Band

# A small band keeps the synthetic oversize/merge fixtures tiny and fast.
SMALL = Band(floor=600, target=1200, ceiling=2000)


def _kept(plan):
    return [c for c in plan.chunks if c.skip_reason is None]


def _by_title(plan, prefix):
    return [c for c in plan.chunks if c.label.startswith(prefix)]


# --------------------------------------------------------------------------- #
# simple_book (§5.4, §5.5)
# --------------------------------------------------------------------------- #

def test_simple_book_emits_three_chapters(simple_book):
    plan = plan_book(simple_book, level=1)
    labels = [c.label for c in _kept(plan) if c.include]
    assert any(l.startswith("Chapter 1") for l in labels)
    assert any(l.startswith("Chapter 2") for l in labels)
    assert any(l.startswith("Chapter 3") for l in labels)


def test_simple_book_skips_front_and_back_matter(simple_book):
    plan = plan_book(simple_book, level=1)
    reasons = {c.label: c.skip_reason for c in plan.chunks if c.skip_reason}
    assert reasons["Cover"] == "skiplist: ^cover$"
    assert reasons["Contents"] == "skiplist: ^(table of )?contents$"
    assert reasons["Index"] == "skiplist: ^index$"
    assert reasons["Appendix A: Tables"] == r"skiplist: ^appendix\b"


def test_glossary_survives_the_keep_rule(simple_book):
    plan = plan_book(simple_book, level=1)
    glossary = _by_title(plan, "Glossary")[0]
    assert glossary.skip_reason is None
    assert glossary.include


def test_chunk_boundaries_are_exact_and_contiguous(simple_book):
    plan = plan_book(simple_book, level=1)
    chapters = [c for c in _kept(plan) if c.label.startswith("Chapter")]
    chapters.sort(key=lambda c: c.start)
    # Ch1 5-14, Ch2 15-29, Ch3 30-44 — contiguous over the kept run.
    assert chapters[0].start == 5
    for a, b in zip(chapters, chapters[1:]):
        assert b.start == a.end + 1


def test_skipped_section_pages_appear_in_no_chunk(simple_book):
    plan = plan_book(simple_book, level=1)
    kept_pages = set()
    for c in _kept(plan):
        kept_pages.update(range(c.start, c.end + 1))
    # Cover 0-1, Contents 2-4, Appendix 45-51, Index 52-54 are all skipped.
    for p in [0, 1, 2, 3, 4, 45, 50, 51, 52, 54]:
        assert p not in kept_pages


def test_last_flat_entry_ends_at_last_page(simple_book):
    plan = plan_book(simple_book, level=1)
    last = max(plan.chunks, key=lambda c: c.end)
    assert last.end == plan.total_pages - 1


# --------------------------------------------------------------------------- #
# part_book (§5.9 merge, --level)
# --------------------------------------------------------------------------- #

def test_part_book_level2_merges_within_a_part(part_book):
    plan = plan_book(part_book, level=2, notebooklm=True, band=SMALL)
    merged = [c for c in _kept(plan) if c.merged_from]
    assert len(merged) == 1
    assert len(merged[0].merged_from) == 3
    assert "Part I" in merged[0].label  # labelled after the Part


def test_part_book_level2_second_part_stays_separate(part_book):
    plan = plan_book(part_book, level=2, notebooklm=True, band=SMALL)
    scaling = _by_title(plan, "Chapter 4")
    assert len(scaling) == 1
    assert scaling[0].merged_from is None


def test_no_chunk_spans_the_part_boundary(part_book):
    plan = plan_book(part_book, level=2, notebooklm=True, band=SMALL)
    # Part II divider is 0-based page 4; no emitted chunk may straddle it.
    for c in _kept(plan):
        assert not (c.start < 4 <= c.end)


def test_part_book_level1_gives_two_part_chunks(part_book):
    plan = plan_book(part_book, level=1)
    kept = _kept(plan)
    assert len(kept) == 2
    assert kept[0].start == 0
    assert kept[1].end == plan.total_pages - 1


# --------------------------------------------------------------------------- #
# deep_book (§5.8 oversize)
# --------------------------------------------------------------------------- #

def test_oversize_chapter_is_subdivided_into_children(deep_book):
    plan = plan_book(deep_book, level=1, band=SMALL)
    pieces = [c for c in plan.chunks if c.subdivided_from == "Chapter 2: Deep Systems"]
    assert len(pieces) == 3
    for i, piece in enumerate(pieces, start=1):
        assert piece.part_of == (i, 3)


def test_no_pages_lost_before_first_subsection(deep_book):
    plan = plan_book(deep_book, level=1, band=SMALL)
    pieces = [c for c in plan.chunks if c.subdivided_from == "Chapter 2: Deep Systems"]
    pieces.sort(key=lambda c: c.start)
    # First piece starts at the parent chapter's opener (page 1), not the first
    # subsection (page 2), so the opener pages are not dropped.
    assert pieces[0].start == 1
    assert pieces[0].end >= 2  # covers the first subsection opener too


def test_oversize_chapter_without_children_is_left_intact(deep_book):
    plan = plan_book(deep_book, level=1, band=SMALL)
    monolith = _by_title(plan, "Chapter 3: Monolith")
    assert len(monolith) == 1
    assert monolith[0].subdivided_from is None
    assert any("Monolith" in w and "no deeper outline" in w for w in plan.warnings)


# --------------------------------------------------------------------------- #
# scanned_book (§5.7)
# --------------------------------------------------------------------------- #

def test_scanned_book_has_no_text_layer(scanned_book):
    plan = plan_book(scanned_book, level=1)
    assert plan.has_text_layer is False
    assert any("No usable text layer" in w for w in plan.warnings)


def test_scanned_book_every_chunk_not_applicable(scanned_book):
    plan = plan_book(scanned_book, level=1)
    assert all(c.verify.status == "not_applicable" for c in _kept(plan))


def test_scanned_book_does_not_collapse_into_one_chunk(scanned_book):
    plan = plan_book(scanned_book, level=1)
    assert len(_kept(plan)) == 4  # one per chapter — not a single merged slab


# --------------------------------------------------------------------------- #
# errors (§5.1, §5.3)
# --------------------------------------------------------------------------- #

def test_no_outline_raises(no_outline_book):
    with pytest.raises(NoOutlineError):
        plan_book(no_outline_book, level=1)


def test_missing_level_raises_and_names_available_levels(simple_book):
    with pytest.raises(LevelNotFoundError) as exc:
        plan_book(simple_book, level=3)
    assert "level 1" in str(exc.value)


def test_unresolvable_entry_is_dropped_with_warning(tmp_path):
    doc = fitz.open()
    for _ in range(6):
        doc.new_page()
    # PyMuPDF surfaces a bookmark with no resolvable destination as page -1.
    doc.set_toc([[1, "Chapter 1: Real", 1], [1, "Chapter 2: Broken", -1]])
    path = str(tmp_path / "b.pdf")
    doc.save(path)
    doc.close()

    plan = plan_book(path, level=1)
    assert any("unresolvable" in w.lower() for w in plan.warnings)
    assert all("Broken" not in c.label for c in plan.chunks)


# --------------------------------------------------------------------------- #
# naming (§6) and overrides (§5.11)
# --------------------------------------------------------------------------- #

def test_slug_collisions_resolve_with_suffix(collision_book):
    plan = plan_book(collision_book, level=1)
    slugs = [c.slug for c in _kept(plan)]
    assert slugs[0] == "widgets"
    assert slugs[1] == "widgets-2"


def test_slugify_strips_leading_chapter_label():
    assert slugify("Chapter 7. Path Selection") == "path-selection"
    assert slugify("Part III: BGP") == "bgp"


def test_start_override_moves_start_and_redereives_previous_end(simple_book):
    entries = engine.read_outline(fitz.open(simple_book), [])
    ch2 = [e for e in entries if e.title.startswith("Chapter 2")][0]
    key = override_key(ch2)
    plan = plan_book(simple_book, level=1,
                     overrides={"starts": {key: 16}, "include": {}})
    ch2_chunk = [c for c in plan.chunks if ch2.idx in c.entries][0]
    ch1_chunk = [c for c in plan.chunks if c.label.startswith("Chapter 1")][0]
    assert ch2_chunk.start == 16
    assert ch2_chunk.verify.status == "ok"       # accepted override marks it ok
    assert ch1_chunk.end == 15                    # previous end re-derived


def test_sequence_numbers_only_count_emitted_chunks(simple_book):
    plan = plan_book(simple_book, level=1)
    emitted = [c for c in plan.chunks if c.skip_reason is None and c.include]
    assert [c.seq for c in emitted] == list(range(1, len(emitted) + 1))
    assert all(c.seq == 0 for c in plan.chunks if c.skip_reason)
