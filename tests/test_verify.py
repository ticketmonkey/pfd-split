"""Verification tests (§5.10)."""

from __future__ import annotations

from pdfsplit import verify
from pdfsplit.engine import plan_book


def _by_title(plan, prefix):
    return [c for c in plan.chunks if c.label.startswith(prefix)]


# --------------------------------------------------------------------------- #
# tokens / score primitives
# --------------------------------------------------------------------------- #

def test_tokens_drops_stopwords_and_short_tokens():
    assert verify.tokens("Chapter 3: Route Reflection") == {"route", "reflection"}
    # every word is a stopword or too short -> empty
    assert verify.tokens("Chapter 3 of the") == set()


def test_tokens_normalizes_accents():
    assert verify.tokens("Café Résumé") == {"cafe", "resume"}


# --------------------------------------------------------------------------- #
# aligned bookmarks verify ok
# --------------------------------------------------------------------------- #

def test_aligned_chapters_verify_ok(simple_book):
    plan = plan_book(simple_book, level=1)
    for prefix in ("Chapter 1", "Chapter 2", "Chapter 3"):
        chunk = _by_title(plan, prefix)[0]
        assert chunk.verify.status == "ok"
        assert chunk.verify.score >= 0.7


# --------------------------------------------------------------------------- #
# offset_book — the snap-proposal path
# --------------------------------------------------------------------------- #

def test_misaligned_bookmark_proposes_a_snap(offset_book):
    plan = plan_book(offset_book, level=1)
    ch2 = _by_title(plan, "Chapter 2")[0]
    assert ch2.verify.status == "snap_proposed"
    assert ch2.verify.proposed_start == 15          # the page holding the opener
    assert ch2.start == 14                           # start is NOT silently moved


def test_proposal_never_crosses_neighbouring_starts(offset_book):
    plan = plan_book(offset_book, level=1)
    ch2 = _by_title(plan, "Chapter 2")[0]
    ch1 = _by_title(plan, "Chapter 1")[0]
    ch3 = _by_title(plan, "Chapter 3")[0]
    assert ch1.start < ch2.verify.proposed_start < ch3.start
    assert all(ch1.start < p < ch3.start or p == ch2.start
               for p in ch2.verify.checked_pages)


def test_every_tried_page_is_recorded(offset_book):
    plan = plan_book(offset_book, level=1)
    ch2 = _by_title(plan, "Chapter 2")[0]
    assert ch2.start in ch2.verify.checked_pages
    assert ch2.verify.proposed_start in ch2.verify.checked_pages
