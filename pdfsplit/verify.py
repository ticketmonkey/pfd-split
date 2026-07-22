"""Boundary verification (§5.10).

Catches misaligned bookmarks — the common failure — by checking whether the tokens
of a chunk's title actually appear on its start page. A low score never moves the
boundary; it *proposes* a correction (``snap_proposed``) for a human to accept, or
flags the boundary ``unverified``. Skipped entirely when there is no text layer.
"""

from __future__ import annotations

import re
import unicodedata

from .model import VerifyResult

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "at", "by",
    "chapter", "part", "section", "appendix", "introduction",
}


def tokens(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) >= 3 and t not in STOPWORDS}


def _score(title_tokens: set[str], page_text: str) -> float:
    if not title_tokens:
        return 0.0
    return len(title_tokens & tokens(page_text)) / len(title_tokens)


def verify_chunk(chunk, prev_start, next_start, page_texts, entries) -> VerifyResult:
    """Verify one chunk's start page (§5.10). ``prev_start`` / ``next_start`` are the
    starts of the neighbouring emitted chunks (or ``None``); a proposal is clamped so
    it never crosses either."""
    n = len(page_texts)
    title = entries[chunk.entries[0]].title
    tt = tokens(title)
    start = chunk.start

    if not tt:
        return VerifyResult("unverified", 0.0, None, None, [start])

    start_score = _score(tt, page_texts[start]) if 0 <= start < n else 0.0
    checked = [start]
    if start_score >= 0.7:
        return VerifyResult("ok", start_score, None, None, checked)

    low = max(0, start - 3)
    high = min(n - 1, start + 3)
    if prev_start is not None:
        low = max(low, prev_start + 1)
    if next_start is not None:
        high = min(high, next_start - 1)

    best_page, best_score = None, start_score
    for p in range(low, high + 1):
        if p == start:
            continue
        sc = _score(tt, page_texts[p])
        checked.append(p)
        if sc >= 0.7 and sc > best_score:
            best_page, best_score = p, sc

    if best_page is not None:
        return VerifyResult("snap_proposed", start_score, best_page, best_score, checked)
    return VerifyResult("unverified", start_score, None, None, checked)


def verify_chunks(chunks, page_texts, entries, total_pages=None) -> None:
    """Run §5.10 over the emitted (kept, non-skipped) chunks in document order,
    updating each ``chunk.verify`` in place. Neighbours for proposal-clamping are the
    adjacent emitted chunks."""
    kept = [c for c in chunks if c.skip_reason is None]
    for k, c in enumerate(kept):
        prev_start = kept[k - 1].start if k > 0 else None
        next_start = kept[k + 1].start if k < len(kept) - 1 else None
        c.verify = verify_chunk(c, prev_start, next_start, page_texts, entries)
