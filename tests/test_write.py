"""Emission + CLI tests (§6, §7, §9), driven by the stage-01 synthetic fixtures."""

from __future__ import annotations

import os
import tempfile

import fitz
from click.testing import CliRunner

from pdfsplit import write
from pdfsplit.cli import main
from pdfsplit.engine import plan_book
from pdfsplit.model import Band

# A small band keeps the oversize/merge fixtures tiny; most write tests use the
# default band so chapters are emitted whole.
SMALL = Band(floor=600, target=1200, ceiling=2000)


def _emitted(plan):
    return [c for c in plan.chunks if c.skip_reason is None and c.include]


def _pdfs(out_dir):
    return sorted(f for f in os.listdir(out_dir) if f.endswith(".pdf"))


# --------------------------------------------------------------------------- #
# page counts and the header page (§7, §7.1)
# --------------------------------------------------------------------------- #

def test_chunk_page_count_includes_header_page(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    ch1 = [c for c in _emitted(plan) if c.label.startswith("Chapter 1")][0]

    path = os.path.join(res.out_dir, _pdfs(res.out_dir)[0])
    doc = fitz.open(path)
    try:
        # end - start + 1 real pages, plus one prepended header page.
        assert doc.page_count == (ch1.end - ch1.start + 1) + 1
    finally:
        doc.close()


def test_no_header_page_gives_exact_count_and_real_opener(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path), header_page=False)
    ch1 = [c for c in _emitted(plan) if c.label.startswith("Chapter 1")][0]

    path = os.path.join(res.out_dir, sorted(_pdfs(res.out_dir))[0])
    doc = fitz.open(path)
    try:
        assert doc.page_count == ch1.end - ch1.start + 1
        # Page 0 is the genuine chapter opener, not a generated header.
        assert "Network Foundations" in doc[0].get_text("text")
    finally:
        doc.close()


def test_metadata_title_is_book_dash_label(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    ch2 = [c for c in _emitted(plan) if c.label.startswith("Chapter 2")][0]

    path = os.path.join(res.out_dir, f"{ch2.seq:02d}-{ch2.slug}.pdf")
    doc = fitz.open(path)
    try:
        assert doc.metadata["title"] == f"{plan.book_title} — {ch2.label}"
        assert doc.metadata["producer"] == "pdfsplit"
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# sub-outline lands after the header offset (§7 step 4)
# --------------------------------------------------------------------------- #

def test_sub_outline_pages_account_for_the_header_offset(deep_book, tmp_path):
    # deep_book at level 1 with a large band: Chapter 2 keeps its level-2
    # subsections (Memory Model p2, Scheduling p5, Networking Stack p8) as a
    # sub-outline rather than being subdivided.
    plan = plan_book(deep_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    ch2 = [c for c in _emitted(plan) if c.label.startswith("Chapter 2")][0]

    path = os.path.join(res.out_dir, f"{ch2.seq:02d}-{ch2.slug}.pdf")
    doc = fitz.open(path)
    try:
        toc = doc.get_toc(simple=True)
        titles = {row[1]: row[2] for row in toc}
        assert "Scheduling" in titles
        # Source page 5, chunk start 1, +1 header, +1 for 1-based → page 6.
        expected = 5 - ch2.start + 1 + 1
        assert titles["Scheduling"] == expected
        # And the bookmark really lands on the Scheduling opener page.
        dest = doc[expected - 1].get_text("text")
        assert "Scheduling" in dest
    finally:
        doc.close()


def test_flat_chunk_bookmarks_its_own_opener_past_the_header(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    ch1 = [c for c in _emitted(plan) if c.label.startswith("Chapter 1")][0]
    path = os.path.join(res.out_dir, f"{ch1.seq:02d}-{ch1.slug}.pdf")
    doc = fitz.open(path)
    try:
        toc = doc.get_toc(simple=True)
        # A flat chapter's only in-range entry is itself: one level-1 bookmark
        # pointing past the header page at the real opener (1-based page 2).
        assert len(toc) == 1
        assert toc[0][0] == 1
        assert toc[0][2] == 2
        assert "Network Foundations" in doc[1].get_text("text")
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# filenames (§6)
# --------------------------------------------------------------------------- #

def test_filenames_are_zero_padded_sorted_and_unique(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    names = _pdfs(res.out_dir)
    assert len(names) == len(_emitted(plan))
    assert len(set(names)) == len(names)
    # Two-digit sequence prefixes for a <100-chunk book, already in sort order.
    assert names == sorted(names)
    assert all(n[:2].isdigit() and n[2] == "-" for n in names)


def test_duplicate_title_yields_suffixed_slug(collision_book, tmp_path):
    plan = plan_book(collision_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    names = _pdfs(res.out_dir)
    assert names == ["01-widgets.pdf", "02-widgets-2.pdf"]


def test_prefix_book_prepends_the_book_slug(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path), prefix_book=True)
    for name in _pdfs(res.out_dir):
        assert name.startswith(f"{plan.book_slug}__")


# --------------------------------------------------------------------------- #
# --also-text (§7.2) and 00-index.md (§7.3)
# --------------------------------------------------------------------------- #

def test_also_text_writes_one_md_per_chunk_with_label_h1(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path), also_text=True)
    mds = [f for f in os.listdir(res.out_dir)
           if f.endswith(".md") and f != "00-index.md"]
    assert len(mds) == len(_emitted(plan))

    ch1 = [c for c in _emitted(plan) if c.label.startswith("Chapter 1")][0]
    md_path = os.path.join(res.out_dir, f"{ch1.seq:02d}-{ch1.slug}.md")
    text = open(md_path, encoding="utf-8").read()
    assert text.startswith(f"# {ch1.label}\n")


def test_index_lists_every_chunk_and_names_skips(simple_book, tmp_path):
    plan = plan_book(simple_book, level=1)
    res = write.write_book(plan, str(tmp_path))
    index = open(res.index, encoding="utf-8").read()

    for c in _emitted(plan):
        assert c.label in index
        assert f"{c.seq:02d}-{c.slug}.pdf" in index

    assert "## Skipped" in index
    assert "Index" in index
    assert "^index$" in index
    assert "Appendix A: Tables" in index


# --------------------------------------------------------------------------- #
# invariant §3.1 — the source is never touched
# --------------------------------------------------------------------------- #

def test_source_file_and_directory_are_untouched(simple_book):
    # The fixture writes the PDF directly into its own tmp dir, so output must go
    # somewhere else entirely to prove nothing lands beside the source (§3.1).
    src_dir = os.path.dirname(simple_book)
    before_listing = set(os.listdir(src_dir))
    before = os.stat(simple_book)

    with tempfile.TemporaryDirectory() as out_root:
        plan = plan_book(simple_book, level=1)
        write.write_book(plan, out_root, also_text=True)

    after = os.stat(simple_book)
    assert (after.st_size, after.st_mtime) == (before.st_size, before.st_mtime)
    assert set(os.listdir(src_dir)) == before_listing


# --------------------------------------------------------------------------- #
# CLI (§9)
# --------------------------------------------------------------------------- #

def test_cli_split_end_to_end(simple_book, tmp_path):
    out = str(tmp_path / "out")
    runner = CliRunner()
    result = runner.invoke(
        main, ["split", simple_book, "--notebooklm", "--out", out, "--yes"]
    )
    assert result.exit_code == 0, result.output
    book_dir = os.path.join(out, "simple-book")
    assert os.path.exists(os.path.join(book_dir, "00-index.md"))
    assert os.path.exists(os.path.join(book_dir, ".pdfsplit.json"))
    assert _pdfs(book_dir)


def test_cli_yes_without_force_refuses_unresolved_and_writes_nothing(
        offset_book, tmp_path):
    out = str(tmp_path / "out")
    runner = CliRunner()
    result = runner.invoke(main, ["split", offset_book, "--out", out, "--yes"])
    # offset_book has a misaligned Chapter 2 bookmark → snap_proposed.
    assert result.exit_code == 2, result.output
    book_dir = os.path.join(out, "offset-book")
    assert not os.path.exists(book_dir) or _pdfs(book_dir) == []


def test_cli_dry_run_writes_nothing(simple_book, tmp_path):
    out = str(tmp_path / "out")
    runner = CliRunner()
    result = runner.invoke(main, ["split", simple_book, "--out", out, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not os.path.exists(os.path.join(out, "simple-book"))


def test_cli_inspect_writes_nothing_and_reports_levels(simple_book, tmp_path):
    src_dir = os.path.dirname(simple_book)
    before = set(os.listdir(src_dir))
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", simple_book])
    assert result.exit_code == 0, result.output
    assert "Level 1" in result.output
    assert set(os.listdir(src_dir)) == before  # inspect writes nothing
