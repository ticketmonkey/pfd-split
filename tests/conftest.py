"""Synthetic PDF fixtures (§12), built with PyMuPDF at test time.

No binary fixtures are committed. Every generated page carries its section title as
real, extractable text so boundary verification (§5.10) has something to match. Filler
uses NATO-alphabet words, deliberately disjoint from every section title, so a title's
tokens only ever appear on the pages that genuinely contain that title.
"""

from __future__ import annotations

import fitz
import pytest

FILLER = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
    "kilo lima mike november oscar papa quebec romeo sierra tango"
).split()


def _fill_page(page, title, n_words, salt=0):
    """Write ``title`` (if given) plus ``n_words`` filler words as real text."""
    lines = []
    if title:
        lines.append(title)
    fw = [FILLER[(salt + i) % len(FILLER)] for i in range(n_words)]
    for i in range(0, len(fw), 10):
        lines.append(" ".join(fw[i:i + 10]))
    y = 60
    for line in lines:
        if y > 760:
            break
        page.insert_text((60, y), line, fontsize=9, fontname="helv")
        y += 12


class Book:
    """Builds a PDF section by section, tracking 0-based start pages for the outline."""

    def __init__(self, width=612, height=792):
        self.doc = fitz.open()
        self.toc: list[list] = []
        self.width = width
        self.height = height

    def add_section(self, level, title, pages, words_per_page=250,
                    title_offset=0, text=True):
        """Add ``pages`` pages for a section starting at the current page.

        The bookmark points at the first page; the *visible* title text lands on
        ``start + title_offset`` (used by ``offset_book`` to misalign a bookmark).
        Returns the 0-based start page.
        """
        start = self.doc.page_count
        for p in range(pages):
            page = self.doc.new_page(width=self.width, height=self.height)
            if not text:
                page.draw_rect(fitz.Rect(80, 80, 420, 520), color=(0, 0, 0),
                               fill=(0.6, 0.6, 0.6))
                continue
            _fill_page(page, title if p == title_offset else None,
                       words_per_page, salt=start + p)
        self.toc.append([level, title, start + 1])  # set_toc wants 1-based pages
        return start

    def blank_pages(self, pages, words_per_page=120, text=True):
        for _ in range(pages):
            page = self.doc.new_page(width=self.width, height=self.height)
            if text:
                _fill_page(page, None, words_per_page)

    def save(self, path, with_outline=True):
        if with_outline and self.toc:
            self.doc.set_toc(self.toc)
        self.doc.save(str(path))
        self.doc.close()
        return str(path)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def simple_book(tmp_path):
    """60 pp, flat level-1 outline (spec §12 plus a Glossary to exercise the keep rule)."""
    b = Book()
    b.add_section(1, "Cover", 2)
    b.add_section(1, "Contents", 3)
    b.add_section(1, "Chapter 1: Network Foundations", 10)
    b.add_section(1, "Chapter 2: Route Reflection", 15)
    b.add_section(1, "Chapter 3: Path Selection", 15)
    b.add_section(1, "Appendix A: Tables", 7)
    b.add_section(1, "Index", 3)
    b.add_section(1, "Glossary", 5)
    return b.save(tmp_path / "simple_book.pdf")


@pytest.fixture
def part_book(tmp_path):
    """Parts at level 1, chapters at level 2. Part I: three short chapters (merge);
    Part II: one longer chapter (must not merge across the Part boundary)."""
    b = Book()
    b.add_section(1, "Part I: Foundations", 1)
    b.add_section(2, "Chapter 1: Basics", 1)
    b.add_section(2, "Chapter 2: Signals", 1)
    b.add_section(2, "Chapter 3: Systems", 1)
    b.add_section(1, "Part II: Advanced", 1)
    b.add_section(2, "Chapter 4: Scaling", 4)
    return b.save(tmp_path / "part_book.pdf")


@pytest.fixture
def offset_book(tmp_path):
    """Like simple_book but Chapter 2's bookmark points one page before the page that
    actually holds the 'Chapter 2' opener text."""
    b = Book()
    b.add_section(1, "Cover", 2)
    b.add_section(1, "Contents", 3)
    b.add_section(1, "Chapter 1: Network Foundations", 9)
    # bookmark at the section's first page (0-based 14), title text on the next page.
    b.add_section(1, "Chapter 2: Route Reflection", 16, title_offset=1)
    b.add_section(1, "Chapter 3: Path Selection", 15)
    b.add_section(1, "Appendix A: Tables", 7)
    b.add_section(1, "Index", 3)
    b.add_section(1, "Glossary", 5)
    return b.save(tmp_path / "offset_book.pdf")


@pytest.fixture
def deep_book(tmp_path):
    """One chapter far above the (test-scaled) ceiling with level-2 subsections, plus
    an oversize chapter with no children to split on."""
    b = Book()
    b.add_section(1, "Chapter 1: Overview", 1)
    b.add_section(1, "Chapter 2: Deep Systems", 1)      # opener page before subsections
    b.add_section(2, "Memory Model", 3)
    b.add_section(2, "Scheduling", 3)
    b.add_section(2, "Networking Stack", 3)
    b.add_section(1, "Chapter 3: Monolith", 9)          # oversize, no children
    return b.save(tmp_path / "deep_book.pdf")


@pytest.fixture
def scanned_book(tmp_path):
    """Pages with only a drawn rectangle — zero extractable text — but a real outline."""
    b = Book()
    b.add_section(1, "Chapter 1: Scan One", 5, text=False)
    b.add_section(1, "Chapter 2: Scan Two", 5, text=False)
    b.add_section(1, "Chapter 3: Scan Three", 5, text=False)
    b.add_section(1, "Chapter 4: Scan Four", 5, text=False)
    return b.save(tmp_path / "scanned_book.pdf")


@pytest.fixture
def no_outline_book(tmp_path):
    """Valid PDF with real text but no bookmarks at all."""
    b = Book()
    b.blank_pages(10)
    return b.save(tmp_path / "no_outline_book.pdf", with_outline=False)


@pytest.fixture
def collision_book(tmp_path):
    """Two chapters whose titles slugify to the same base, to exercise -2 suffixing."""
    b = Book()
    b.add_section(1, "Widgets", 4)
    b.add_section(1, "Widgets", 4)
    return b.save(tmp_path / "collision_book.pdf")
