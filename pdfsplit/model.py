"""Data model for the planning engine (§4).

All page indices in this model are **0-based**. PyMuPDF's ``get_toc()`` returns
1-based pages — the engine normalizes on read and only converts back to 1-based
for display and ``set_toc()``.

The ``to_dict`` / ``from_dict`` helpers give ``BookPlan`` (and its nested types) a
stable JSON round-trip, used by the sidecar (§8) and, later, the web API (§10).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OutlineEntry:
    idx: int                        # index into the flat entry list
    level: int                      # 1-based, as returned by get_toc()
    title: str                      # raw title from the outline
    page: int                       # 0-based; -1 if the destination is unresolvable
    parent: int | None              # idx of nearest preceding entry at level-1, else None

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "level": self.level,
            "title": self.title,
            "page": self.page,
            "parent": self.parent,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OutlineEntry":
        return cls(
            idx=d["idx"],
            level=d["level"],
            title=d["title"],
            page=d["page"],
            parent=d["parent"],
        )


@dataclass
class VerifyResult:
    status: str                     # "ok" | "snap_proposed" | "unverified" | "not_applicable"
    score: float = 0.0              # 0.0–1.0 token-overlap score at the current start page
    proposed_start: int | None = None
    proposed_score: float | None = None
    checked_pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "score": self.score,
            "proposed_start": self.proposed_start,
            "proposed_score": self.proposed_score,
            "checked_pages": list(self.checked_pages),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VerifyResult":
        return cls(
            status=d["status"],
            score=d.get("score", 0.0),
            proposed_start=d.get("proposed_start"),
            proposed_score=d.get("proposed_score"),
            checked_pages=list(d.get("checked_pages", [])),
        )


@dataclass
class Chunk:
    seq: int                        # 1-based emission order; assigned last, after all passes
    label: str                      # human label, e.g. "Chapter 3 — Path Selection"
    slug: str                       # filename slug, unique within the book
    start: int                      # 0-based inclusive
    end: int                        # 0-based inclusive
    words: int
    entries: list[int]              # OutlineEntry.idx values this chunk covers
    parent: int | None              # shared outline parent, used by the merge pass
    verify: VerifyResult
    merged_from: list[str] | None = None      # original titles, when a merge product
    subdivided_from: str | None = None        # original chapter title, when a split product
    part_of: tuple[int, int] | None = None    # (2, 3) meaning "part 2 of 3"
    skip_reason: str | None = None            # non-None => not emitted, kept for display
    include: bool = True                      # user override; False => not emitted

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "label": self.label,
            "slug": self.slug,
            "start": self.start,
            "end": self.end,
            "words": self.words,
            "entries": list(self.entries),
            "parent": self.parent,
            "verify": self.verify.to_dict(),
            "merged_from": list(self.merged_from) if self.merged_from is not None else None,
            "subdivided_from": self.subdivided_from,
            "part_of": list(self.part_of) if self.part_of is not None else None,
            "skip_reason": self.skip_reason,
            "include": self.include,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        part_of = d.get("part_of")
        return cls(
            seq=d["seq"],
            label=d["label"],
            slug=d["slug"],
            start=d["start"],
            end=d["end"],
            words=d["words"],
            entries=list(d["entries"]),
            parent=d["parent"],
            verify=VerifyResult.from_dict(d["verify"]),
            merged_from=list(d["merged_from"]) if d.get("merged_from") is not None else None,
            subdivided_from=d.get("subdivided_from"),
            part_of=tuple(part_of) if part_of is not None else None,
            skip_reason=d.get("skip_reason"),
            include=d.get("include", True),
        )


@dataclass
class Band:
    floor: int = 6000
    target: int = 12000
    ceiling: int = 20000

    def to_dict(self) -> dict:
        return {"floor": self.floor, "target": self.target, "ceiling": self.ceiling}

    @classmethod
    def from_dict(cls, d: dict) -> "Band":
        return cls(
            floor=d.get("floor", 6000),
            target=d.get("target", 12000),
            ceiling=d.get("ceiling", 20000),
        )


@dataclass
class BookPlan:
    source: str                     # absolute path
    book_title: str
    book_slug: str
    author: str | None
    total_pages: int
    level: int
    notebooklm: bool
    band: Band
    has_text_layer: bool
    words_per_page: float
    chunks: list[Chunk]             # includes skipped chunks, in document order
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "book_title": self.book_title,
            "book_slug": self.book_slug,
            "author": self.author,
            "total_pages": self.total_pages,
            "level": self.level,
            "notebooklm": self.notebooklm,
            "band": self.band.to_dict(),
            "has_text_layer": self.has_text_layer,
            "words_per_page": self.words_per_page,
            "chunks": [c.to_dict() for c in self.chunks],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BookPlan":
        return cls(
            source=d["source"],
            book_title=d["book_title"],
            book_slug=d["book_slug"],
            author=d.get("author"),
            total_pages=d["total_pages"],
            level=d["level"],
            notebooklm=d["notebooklm"],
            band=Band.from_dict(d["band"]),
            has_text_layer=d["has_text_layer"],
            words_per_page=d["words_per_page"],
            chunks=[Chunk.from_dict(c) for c in d["chunks"]],
            warnings=list(d.get("warnings", [])),
        )
